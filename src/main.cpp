#include <Arduino.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>

#include <algorithm>
#include <cstdint>
#include <cstring>

namespace {

constexpr uint32_t kSerialBaud = 115200;
constexpr uint8_t kTouchCsPin = 33;
constexpr uint8_t kBacklightPin = 21;
constexpr uint8_t kRotation = 1;
constexpr uint8_t kMaxLimits = 8;
constexpr uint32_t kStaleAfterMs = 15000;
constexpr uint32_t kDisconnectedAfterMs = 60000;
constexpr size_t kMaxSerialLine = 2048;

constexpr uint16_t kBackground = 0x0000;   // #000000
constexpr uint16_t kCyan = 0x07FF;         // neon cyan, brighter than #00F5FF on RGB565
constexpr uint16_t kMagenta = 0xF8BA;      // #FF2BD6 approx
constexpr uint16_t kPanel = 0x1143;        // #15151D approx
constexpr uint16_t kMuted = 0x03F0;        // #007F86 approx
constexpr uint16_t kDimMagenta = 0x784D;   // #7F156B approx
constexpr uint16_t kForeground = kCyan;
constexpr uint16_t kConnected = kCyan;
constexpr uint16_t kStale = kDimMagenta;
constexpr uint16_t kDisconnected = kMagenta;

struct UsageLimit {
    char id[24] = {};
    uint32_t windowMinutes = 0;
    uint8_t usedPercent = 0;
    uint8_t remainingPercent = 100;
    int64_t resetsAt = 0;
};

struct Credits {
    bool present = false;
    bool active = false;
    int32_t remaining = 0;
};

enum class LinkState : uint8_t { Disconnected, Stale, Connected };

TFT_eSPI display;
UsageLimit limits[kMaxLimits];
uint8_t limitCount = 0;
Credits credits;

char serialLine[kMaxSerialLine];
size_t serialLineLength = 0;

bool hasUsage = false;
int64_t syncedHostTime = 0;
int32_t utcOffsetSeconds = 0;
uint32_t syncedAtMillis = 0;
uint32_t lastHostActivityMillis = 0;
uint32_t lastCountdownSecond = UINT32_MAX;
LinkState renderedLinkState = LinkState::Disconnected;

int64_t currentEpoch() {
    if (!hasUsage) return 0;
    return syncedHostTime + static_cast<int64_t>((millis() - syncedAtMillis) / 1000U);
}

LinkState linkState() {
    if (lastHostActivityMillis == 0) return LinkState::Disconnected;
    const uint32_t age = millis() - lastHostActivityMillis;
    if (age >= kDisconnectedAfterMs) return LinkState::Disconnected;
    if (age >= kStaleAfterMs) return LinkState::Stale;
    return LinkState::Connected;
}

const UsageLimit *findLimit(uint32_t windowMinutes, uint8_t fallbackIndex) {
    for (uint8_t i = 0; i < limitCount; ++i) {
        if (limits[i].windowMinutes == windowMinutes) return &limits[i];
    }
    return fallbackIndex < limitCount ? &limits[fallbackIndex] : nullptr;
}

void formatDuration(int64_t seconds, char *buffer, size_t size) {
    if (seconds < 0) seconds = 0;
    const uint32_t hours = static_cast<uint32_t>(seconds / 3600);
    const uint8_t minutes = static_cast<uint8_t>((seconds / 60) % 60);
    const uint8_t secs = static_cast<uint8_t>(seconds % 60);
    snprintf(buffer, size, "%02lu:%02u:%02u", static_cast<unsigned long>(hours), minutes, secs);
}

void epochToDateTime(int64_t epoch, int &year, unsigned &month, unsigned &day, unsigned &hour, unsigned &minute) {
    int64_t days = epoch / 86400;
    int64_t secondsOfDay = epoch % 86400;
    if (secondsOfDay < 0) {
        secondsOfDay += 86400;
        --days;
    }
    hour = static_cast<unsigned>(secondsOfDay / 3600);
    minute = static_cast<unsigned>((secondsOfDay / 60) % 60);

    days += 719468;
    const int64_t era = (days >= 0 ? days : days - 146096) / 146097;
    const unsigned dayOfEra = static_cast<unsigned>(days - era * 146097);
    const unsigned yearOfEra = (dayOfEra - dayOfEra / 1460 + dayOfEra / 36524 - dayOfEra / 146096) / 365;
    const unsigned dayOfYear = dayOfEra - (365 * yearOfEra + yearOfEra / 4 - yearOfEra / 100);
    const unsigned monthPrime = (5 * dayOfYear + 2) / 153;
    day = dayOfYear - (153 * monthPrime + 2) / 5 + 1;
    month = monthPrime + (monthPrime < 10 ? 3 : -9);
    year = static_cast<int>(yearOfEra) + static_cast<int>(era) * 400 + (month <= 2);
}

void formatResetDate(int64_t epoch, char *buffer, size_t size) {
    static const char *months[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
    int year;
    unsigned month, day, hour, minute;
    epochToDateTime(epoch + utcOffsetSeconds, year, month, day, hour, minute);
    const char *monthName = month >= 1 && month <= 12 ? months[month - 1] : "???";
    snprintf(buffer, size, "%s %u - %02u:%02u", monthName, day, hour, minute);
}

void drawConnectionIndicator(LinkState state) {
    uint16_t color = kDisconnected;
    if (state == LinkState::Connected) color = kConnected;
    else if (state == LinkState::Stale) color = kStale;

    display.fillCircle(303, 13, 6, color);
    display.drawCircle(303, 13, 7, kMuted);
    renderedLinkState = state;
}

void drawProgressBar(int16_t x, int16_t y, int16_t width, uint8_t percent, uint16_t color) {
    const int16_t innerWidth = width - 4;
    const int16_t filled = static_cast<int16_t>((static_cast<uint32_t>(innerWidth) * percent) / 100U);
    display.drawRect(x, y, width, 12, kMuted);
    display.fillRect(x + 2, y + 2, innerWidth, 8, kPanel);
    if (filled > 0) display.fillRect(x + 2, y + 2, filled, 8, color);
}

void drawHeader() {
    display.setTextColor(kCyan, kBackground);
    display.setTextSize(2);
    display.setCursor(10, 7);
    display.print("CODEX");
    display.drawFastHLine(10, 28, 300, kMuted);
    drawConnectionIndicator(linkState());
}

void drawLimitDetail(const char *title, const UsageLimit *limit, int16_t top) {
    display.setTextColor(kForeground, kBackground);
    display.setTextSize(2);
    display.setCursor(12, top);
    display.print(title);

    if (limit == nullptr) {
        display.setTextColor(kMuted, kBackground);
        display.setCursor(12, top + 25);
        display.print("No data");
        return;
    }

    display.setTextColor(kCyan, kBackground);
    display.setCursor(214, top);
    display.printf("%3u%%", limit->remainingPercent);
    drawProgressBar(12, top + 23, 248, limit->remainingPercent, kCyan);

    display.setTextColor(kMuted, kBackground);
    display.setTextSize(1);
    display.setCursor(12, top + 43);
    display.printf("%u%% remaining", limit->remainingPercent);
}

void drawPlanSummaryLine(const char *title, const UsageLimit *limit, int16_t y) {
    display.setTextColor(kMuted, kBackground);
    display.setTextSize(1);
    display.setCursor(18, y);
    display.print(title);
    display.setCursor(82, y);
    display.printf("%3u%%", limit == nullptr ? 0 : limit->remainingPercent);
}

void drawStaticDashboard() {
    display.fillScreen(kBackground);
    drawHeader();

    const UsageLimit *primary = findLimit(300, 0);
    const UsageLimit *secondary = findLimit(10080, 1);

    display.setTextSize(1);
    display.setTextColor(credits.active ? kMagenta : kCyan, kBackground);
    display.setCursor(12, 38);
    display.print(credits.active ? "SOURCE: CREDITS" : "SOURCE: PLAN LIMITS");

    if (credits.active) {
        display.drawRect(10, 56, 300, 88, kMagenta);
        display.setTextColor(kMagenta, kBackground);
        display.setTextSize(2);
        display.setCursor(18, 67);
        display.print("CREDITS");
        display.setTextSize(3);
        display.setCursor(18, 92);
        display.print(credits.present ? credits.remaining : 0);
        display.setTextSize(1);
        display.setTextColor(kMagenta, kBackground);
        display.setCursor(122, 103);
        display.print("remaining");
        drawProgressBar(18, 126, 276, credits.present ? 75 : 0, kMagenta);

        display.setTextColor(kMuted, kBackground);
        display.setTextSize(1);
        display.setCursor(12, 159);
        display.print("PLAN LIMITS");
        drawPlanSummaryLine("5 HOURS", primary, 178);
        drawPlanSummaryLine("WEEKLY", secondary, 194);
    } else {
        drawLimitDetail("5 HOURS", primary, 58);
        display.setTextColor(kMuted, kBackground);
        display.setTextSize(1);
        display.setCursor(12, 122);
        display.print("resets in");
        drawLimitDetail("WEEKLY", secondary, 156);
    }

    display.setTextColor(kMuted, kBackground);
    display.setTextSize(1);
    display.setCursor(12, 229);
    display.print("USB serial - last values retained");

    lastCountdownSecond = UINT32_MAX;
}

void updateCountdownAreas() {
    const uint32_t second = millis() / 1000U;
    if (second == lastCountdownSecond) return;
    lastCountdownSecond = second;

    if (credits.active) return;

    const UsageLimit *primary = findLimit(300, 0);
    const UsageLimit *secondary = findLimit(10080, 1);
    char text[32];

    display.fillRect(12, 134, 296, 20, kBackground);
    if (primary != nullptr && hasUsage) {
        formatDuration(primary->resetsAt - currentEpoch(), text, sizeof(text));
        display.setTextColor(kForeground, kBackground);
        display.setTextSize(2);
        display.setCursor(12, 134);
        display.print(text);
    }

    display.fillRect(12, 215, 190, 11, kBackground);
    if (secondary != nullptr && hasUsage) {
        formatResetDate(secondary->resetsAt, text, sizeof(text));
        display.setTextColor(kMuted, kBackground);
        display.setTextSize(1);
        display.setCursor(12, 215);
        display.print(text);
    }
}

void sendJsonReply(const char *type, const char *message = nullptr) {
    JsonDocument reply;
    reply["type"] = type;
    if (message != nullptr) reply["message"] = message;
    if (strcmp(type, "pong") == 0) reply["uptime"] = millis() / 1000U;
    serializeJson(reply, Serial);
    Serial.println();
}

bool parseCredits(JsonDocument &document, Credits &target) {
    target = Credits{};
    if (!document["credits"].is<JsonObject>()) return true;

    JsonObject item = document["credits"].as<JsonObject>();
    target.present = true;
    target.active = item["active"] | false;
    target.remaining = item["remaining"] | 0;
    if (target.remaining < 0) target.remaining = 0;
    return true;
}

bool applyUsage(JsonDocument &document) {
    if (!document["hostTime"].is<int64_t>() || !document["limits"].is<JsonArray>()) return false;

    UsageLimit parsed[kMaxLimits];
    uint8_t parsedCount = 0;
    for (JsonObject item : document["limits"].as<JsonArray>()) {
        if (parsedCount >= kMaxLimits) break;
        if (!item["id"].is<const char *>() || !item["windowMinutes"].is<uint32_t>() ||
            !item["usedPercent"].is<int>() || !item["resetsAt"].is<int64_t>()) {
            return false;
        }

        const int used = item["usedPercent"].as<int>();
        const int remaining = item["remainingPercent"] | (100 - used);
        if (used < 0 || used > 100 || remaining < 0 || remaining > 100) return false;

        UsageLimit &target = parsed[parsedCount++];
        strlcpy(target.id, item["id"].as<const char *>(), sizeof(target.id));
        target.windowMinutes = item["windowMinutes"].as<uint32_t>();
        target.usedPercent = static_cast<uint8_t>(used);
        target.remainingPercent = static_cast<uint8_t>(remaining);
        target.resetsAt = item["resetsAt"].as<int64_t>();
    }
    if (parsedCount == 0) return false;

    Credits parsedCredits;
    if (!parseCredits(document, parsedCredits)) return false;

    memcpy(limits, parsed, sizeof(UsageLimit) * parsedCount);
    limitCount = parsedCount;
    credits = parsedCredits;
    syncedHostTime = document["hostTime"].as<int64_t>();
    utcOffsetSeconds = (document["utcOffsetMinutes"] | 0) * 60;
    syncedAtMillis = millis();
    lastHostActivityMillis = millis();
    hasUsage = true;
    drawStaticDashboard();
    updateCountdownAreas();
    return true;
}

void processLine(char *line) {
    while (*line == ' ' || *line == '\t') ++line;
    if (strcmp(line, "WHO_ARE_YOU?") == 0) {
        lastHostActivityMillis = millis();
        Serial.println("CYD_CODEX_DASHBOARD_V1");
        return;
    }

    JsonDocument document;
    const DeserializationError error = deserializeJson(document, line);
    if (error) {
        sendJsonReply("error", "invalid_json");
        return;
    }

    const char *type = document["type"] | "";
    if (strcmp(type, "ping") == 0) {
        lastHostActivityMillis = millis();
        sendJsonReply("pong");
    } else if (strcmp(type, "usage") == 0) {
        if (applyUsage(document)) sendJsonReply("ack", "usage");
        else sendJsonReply("error", "invalid_usage");
    } else {
        sendJsonReply("error", "unknown_type");
    }
}

void readSerialLines() {
    while (Serial.available() > 0) {
        const char value = static_cast<char>(Serial.read());
        if (value == '\r') continue;
        if (value == '\n') {
            serialLine[serialLineLength] = '\0';
            if (serialLineLength > 0) processLine(serialLine);
            serialLineLength = 0;
        } else if (serialLineLength + 1 < sizeof(serialLine)) {
            serialLine[serialLineLength++] = value;
        } else {
            serialLineLength = 0;
            sendJsonReply("error", "line_too_long");
        }
    }
}

} // namespace

void setup() {
    pinMode(kTouchCsPin, OUTPUT);
    digitalWrite(kTouchCsPin, HIGH);

    Serial.begin(kSerialBaud);

    display.init();
    display.invertDisplay(true);
    display.setRotation(kRotation);

    pinMode(kBacklightPin, OUTPUT);
    ledcAttach(kBacklightPin, 5000, 8);
    ledcWrite(kBacklightPin, 255);

    drawStaticDashboard();
    Serial.println("CYD_CODEX_DASHBOARD_V1 READY");
}

void loop() {
    readSerialLines();
    updateCountdownAreas();

    const LinkState state = linkState();
    if (state != renderedLinkState) drawConnectionIndicator(state);
    delay(2);
}

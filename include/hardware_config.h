#pragma once

#include <Arduino.h>

// Runtime settings for the tested ESP32-2432S028R / CYD variant.
// TFT_eSPI bus pins must remain build definitions; see platformio.ini.
namespace hardware {

constexpr uint32_t kSerialBaud = 115200;
constexpr uint8_t kTouchCsPin = 33;
constexpr uint8_t kBacklightPin = 21;
constexpr uint8_t kBacklightPwmBits = 8;
constexpr uint32_t kBacklightPwmFrequency = 5000;
constexpr uint8_t kBacklightBrightness = 255;
constexpr uint8_t kDisplayRotation = 1;

// Required by the tested panel for correct colors. Other panels may differ.
constexpr bool kInvertDisplay = true;

}  // namespace hardware

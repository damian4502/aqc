# ESP-NOW LED receiver

Sketch for an ESP32 + 4-module FC-16 MAX7219 matrix (32×8).

## Behaviour

- JSON objects and arrays are flattened into a key, then its value, shown
  one after another (about 2.4 s each, or scrolled if the line is wider
  than the panel).
- Anything that is not valid JSON is shown as a single line.
- New broadcasts replace the current queue immediately.
- First-seen masters are registered automatically (ESP-NOW broadcast slave).

## Hardware

| Pin | Function |
|-----|----------|
| 5   | CS / SS  |
| 18  | CLK / SCK |
| 23  | MOSI (hardware SPI; `DATA_PIN` in the sketch is unused) |

Channel: Wi-Fi 3. Hardware type: `MD_MAX72XX::FC16_HW`, 4 devices.

## Libraries

- ESP32 Arduino core 3.x (`ESP32_NOW.h`)
- [MD_MAX72XX](https://github.com/MajicDesigns/MD_MAX72XX)

No JSON library is required.

# Bill of materials

One glove. Footprints are the measured envelope used for the enclosure, not the datasheet drawing.

| Ref | Component | Part | Footprint | Height |
|:--|:--|:--|:--|:--|
| MCU1 | Microcontroller | Seeed XIAO nRF52840 **Sense** · 102010469 | 21.0 × 17.5 mm | 3.5 mm (≈5 mm over the antenna) |
| AMP1 | I²S class-D amplifier | Adafruit MAX98357A · 3006 | 17.5 × 15.3 mm | 2.2 mm without headers |
| LRA1 | Linear resonant actuator | TITAN Drake HF, f₀ 160 Hz | confirm | confirm |
| FS1 | Flex / force strip | 2.2″, index finger | 73 × 6.4 mm (56 mm active) | 0.5 mm |
| PPG1 | Optical PPG | Pulse Sensor Amped, middle finger | 15.8 mm ⌀ | ≈3 mm |
| BT1 | LiPo cell | Protected 3.7 V · 401230 · ≈120 mAh | 30 × 12 mm | 4.0 mm |
| SW1 | Pushbutton | 6 mm tactile, 4 legs / 2 pairs | 6.0 × 6.0 mm | 4.3 mm (plunger varies 4.3–7.0) |
| SW2 | Power switch | SPDT slide, in the battery + lead | ≈9 × 4 mm | ≈3.5 mm |
| R1 | Resistor | 10 kΩ force divider (brown·black·orange) | 6.0 × 2.3 mm axial ¼ W | 2.3 mm |
| PCB1 | Protoboard | Cut to size, 2 copper rails + isolated pads | ≈45 × 22 mm | 1.6 mm |
| CAB | External cable | 24 AWG 2-core, white PVC | jacket ≈3.5 mm ⌀ | — |
| INT | Internal wire | 22 AWG stranded silicone | ≈1.7 mm ⌀ | — |

## Cable runs

| Cable | From → to | Red core | Black core | Crosses |
|:--|:--|:--|:--|:--|
| A | Wrist → palm | switched V+ | battery GND | the wrist |
| B | Wrist → palm | force node | button node | the wrist |
| C | Palm → index finger | 3V3 | force node | 2 knuckles |
| D | Palm → middle finger | 3V3 | GND | 2 knuckles |
| E | Palm → middle finger | pulse S | spare, unterminated | 2 knuckles |

Colours follow the bench guides, so the same wire keeps the same job from breadboard to glove.

## Notes

- The BOM has **no FSR** — only flex sensors. The channel reads ~30 kΩ (240–260 counts) at rest.
- `SUPPLY_VOLTS` in the firmware must be `4.2f` whenever AMP1's Vin is on the battery.
- Amp `SD` is jumpered to `Vin`; `GAIN` to GND for 12 dB.

Full netlist and assembly order: `hardware/wiring/amulet_build-spec.html`.

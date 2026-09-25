# Bill of materials

One glove. Footprints are the measured envelope used for the enclosure, not the datasheet drawing.

| Ref | Component | Part | Footprint | Height |
|:--|:--|:--|:--|:--|
| MCU1 | Microcontroller | Seeed XIAO nRF52840 **Sense** · 102010469 | 21.0 × 17.5 mm | 3.5 mm (≈5 mm over the antenna) |
| AMP1 | I²S class-D amplifier | Adafruit MAX98357A · 3006 | 17.5 × 15.3 mm | 2.2 mm without headers |
| LRA1 | Linear resonant actuator | TITAN Drake HF, f₀ 160 Hz | confirm | confirm |
| FS1 | Flex strip | 2.2″, index finger | 73 × 6.4 mm (56 mm active) | 0.5 mm |
| PPG1 | Optical PPG | Pulse Sensor Amped, middle finger | 15.8 mm ⌀ | ≈3 mm |
| BT1 | LiPo cell | Protected 3.7 V · 401230 · ≈120 mAh | 30 × 12 mm | 4.0 mm |
| SW1 | Pushbutton | 6 mm tactile, 4 legs / 2 pairs | 6.0 × 6.0 mm | 4.3 mm (plunger varies) |
| R1 | Resistor | 10 kΩ flex divider (brown·black·orange) | 6.0 × 2.3 mm axial ¼ W | 2.3 mm |
| PCB1 | Protoboard | Cut to size, 2 copper rails + isolated pads | ≈45 × 22 mm | 1.6 mm |
| CAB | External cable | 24 AWG 2-core, white PVC | jacket ≈3.5 mm ⌀ | — |
| INT | Internal wire | 22 AWG stranded silicone | ≈1.7 mm ⌀ | — |

## No power switch

**There is no slide switch on this build.** The battery is wired directly to the rail and the
button is the power control: a 3 s hold from idle parks the chip in System OFF at roughly 2 µA
with the button configured as the wake source, and the next press boots it.

This removes a component, a failure point and a hole in the enclosure. It does mean a flat
battery needs the charger rather than a switch, which for a 7-hour runtime on 60-minute sessions
is not a constraint.

## The button has four legs and it matters which two

They are two internally-shorted pairs. Take **one leg from each pair** — diagonally opposite legs
are always safe whichever way the internal pairing runs. Wiring both legs of a single pair is a
permanent short, and since the firmware reads the pin active-low it looks exactly like the button
being held down forever.

Better still, use all four: one whole pair on the GND row, the other pair on an isolated pad
carrying the signal. Each pair is internally common, so it costs nothing electrically and makes
the button far harder to tear off a worn harness.

Details: `wiring/amulet_wiring_button-legs.html`.

## Cable runs

| Cable | From → to | Red core | Black core | Crosses |
|:--|:--|:--|:--|:--|
| A | Wrist → palm | V+ | battery GND | the wrist |
| B | Wrist → palm | flex node | button node | the wrist |
| C | Palm → index finger | 3V3 | flex node | 2 knuckles |
| D | Palm → middle finger | 3V3 | GND | 2 knuckles |
| E | Palm → middle finger | pulse S | spare, unterminated | 2 knuckles |

Colours follow the bench guides, so the same wire keeps the same job from breadboard to glove.

## Notes

- The channel is a **flex sensor**, not an FSR. It reads ~30 kΩ (240–260 counts) at rest, and
  bending lowers the reading.
- `SUPPLY_VOLTS` in the firmware must be `4.2f` whenever AMP1's Vin is on the battery.
- Amp `SD` is jumpered to `Vin`; `GAIN` to GND for 12 dB.
- **AMP1's Vin goes to the battery rail, not the XIAO 3V3.** See [`docs/01-system.md`](../docs/01-system.md).

Full netlist and assembly order: `wiring/amulet_build-spec.html`.

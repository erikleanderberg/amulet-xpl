# Hardware

| | |
|:--|:--|
| [`bom.md`](bom.md) | Bill of materials, cable runs, the button and power notes |
| [`wiring/`](wiring/) | Build guides — self-contained HTML, open in a browser |
| [`cad/`](cad/) | Enclosure models, one folder per released revision |

What each part does and why: [`docs/01-system.md`](../docs/01-system.md).

## Build order

Each stage is testable on the bridge dashboard before you move to the next.

1. `wiring/amulet_build-spec.html` — BOM, netlist, assembly order
2. `wiring/amulet_wiring_FSR-strip.html` — flex strip and divider
3. `wiring/amulet_wiring_Adafruit-1093-PulseAmp.html` — PPG
4. `wiring/amulet_wiring_MAX98357A-Drake-LRA.html` — amp and actuator
5. `wiring/amulet_wiring_button-legs.html` — which two legs of the tactile switch
6. `wiring/amulet_wiring_soldered-harness.html` — the protoboard build, joint by joint
7. `wiring/amulet_wiring_glove-harness.html` — the worn harness

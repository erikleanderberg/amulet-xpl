# Hardware

| | |
|:--|:--|
| [`bom.md`](bom.md) | Bill of materials, footprints, cable runs |
| [`wiring/`](wiring/) | Build guides — self-contained HTML, open them in a browser |
| [`cad/`](cad/) | Enclosure models, one folder per released revision |

Pin map, power notes and the physical quirks that matter:
[`docs/02-hardware.md`](../docs/02-hardware.md).

## Start here if you are building one

1. `wiring/amulet_build-spec.html` — the BOM, the netlist and the assembly order
2. `wiring/amulet_wiring_FSR-strip.html` — flex strip and divider
3. `wiring/amulet_wiring_Adafruit-1093-PulseAmp.html` — PPG
4. `wiring/amulet_wiring_MAX98357A-Drake-LRA.html` — amp and actuator
5. `wiring/amulet_wiring_button-legs.html` — which two legs of the tactile switch
6. `wiring/amulet_wiring_soldered-harness.html` — the protoboard build, joint by joint
7. `wiring/amulet_wiring_glove-harness.html` — the worn harness

Every stage is testable on the dashboard before you move to the next one.

# 04 · Protocol

Text lines, `\n`-terminated, comma-separated, ASCII. **Identical over USB serial at 115200 and over
Bluetooth.** Fields are integers unless noted. This is the contract between the board and
everything else; if you only need to integrate, this page and [05 · Software](05-software.md) are
enough.

## Board → host

| Line | Rate | Fields |
|:--|:--|:--|
| `S,<ms>,<fsr>,<pulse>,<thresh>` | 100 Hz | `fsr` 0–1023 at A0 · `pulse` raw PPG 0–1023 at A1 · `thresh` the beat detector's current threshold, for plotting |
| `B,<ms>,<bpm>,<ibi>` | per beat | Only when 40 ≤ bpm ≤ 180. `bpm` is a 10-beat running mean; **`ibi` is the single latest beat-to-beat interval in ms — use `ibi` for HRV, not `bpm`** |
| `R,<ms>,<rssi>` | 50 Hz | Link RSSI in dBm, measured board-side per connection event. Only while a central is connected |
| `M,<ms>,<rms>,<peak>` | 50 Hz | Microphone level. Only while the mic is on |
| `V,<ms>,<mV>,<pct>,<chg>,<fast>,<raw>,<sag>` | 2 Hz | Battery: millivolts, percent, charging flag, fast-charge flag, raw ADC, sag in mV |
| `K,<ms>,<down>,<count>` | on edge | Button. `down` 1 = pressed, 0 = released. `count` is the cumulative press count |
| `H,<mode>,<hz>,<vol>,<peak>,<sync>,<gap_us>,<dropped>` | 1 Hz idle, 10 Hz driving | Haptic and loop health. `mode` ∈ idle/tone/tap/sweep/buzz/hold · `peak` largest \|sample\| in the last I²S buffer (0–32767) · `gap_us` worst loop gap · `dropped` lines lost for want of link buffer |
| `A,<seq>,<base64>` | during download | A chunk of raw little-endian int16 PCM |
| `AEND,<total_bytes>,<rate>` | once | Closes an audio download |
| `I,<text>` | on events | Human-readable acknowledgements and errors |

### The button is edges only

The firmware reports every press and release. **It does not detect double-clicks.** The host
judges the gesture, on the board's `ms` clock so link jitter cannot split one press into two —
see `BTN_DOUBLE_MS` in `live.py`, currently 450 ms. A recognised double is consumed, so a
triple-click cannot register as two doubles.

### Audio is store-and-forward

There is no streaming audio. `r` records `REC_SECONDS` into RAM; `d` streams it back as a run of
`A` lines closed by `AEND`. At 3 s / 16 kHz that is ~96 KB raw, about 800 chunks.

## Host → board

Single ASCII characters, no newline needed.

| Char | Action | Bounds |
|:--|:--|:--|
| `b` | Tap, 150 ms at 160 Hz | no cooldown |
| `t` | Tone, 3 s at 160 Hz | 1.5 s cooldown |
| `o` | Hold: continuous 160 Hz until `o` again or `s` | 60 s hard cap |
| `z` | Buzz toggle: 250 ms on / 250 off | 5 min hard cap |
| `w` / `n` | Wide sweep 40–300 Hz / narrow 120–210 Hz | cooldown after |
| `s` | Stop everything, I²S clock off | |
| `+` / `-` | Volume ± 0.10 | 0.10 … `VOLUME_MAX` |
| `k` | Toggle tap-on-every-heartbeat | gated 40–180 bpm |
| `h` | Emit an `H` line now | |
| `m` | Microphone on / off | |
| `r` | Record `REC_SECONDS` of audio to RAM | |
| `d` | Download the recording as `A` lines | |
| `g` | Cycle mic gain 0 → 20 → 40 → 60 → 80 | |
| `l` | Acoustic loopback latency probe (tap → mic) | |
| `c` | Toggle fast charge (100 mA / 50 mA) | |

> A command sent while the board is `busy` or `cooling` is refused with an `I,busy` or
> `I,cooling down` line. **Check for the reply.** Board-side command latency is < 11 ms.

> **Not implemented:** any parametric pattern command. `onset/haptic.py` can emit a `DB k=v …`
> line and the board ignores it. The cleanest addition would be a `P,<hz>,<ms>,<vol>` one-shot.

## Link facts

Nordic UART Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`, RX (write) `…0002`, TX (notify) `…0003`.
Advertised name `Amulet-XPL`.

**One central at a time.** The bridge holds it. Do not open a second BLE client to the board while
the bridge is running — read the bridge's event stream instead.

Measured over Bluetooth: connect 1.6 s · 50 notifications/s · 99.5 `S` lines/s · 0 missed,
malformed or dropped over 20 s · host arrival jitter p50 0 ms, max 89 ms.

## Timing semantics

`ms` is exact in its spacing; host time is when the bridge received the line. For inter-beat
intervals always use the `B` line's `ibi`, computed on the board at 2 ms resolution and immune to
link jitter.

> The board clock runs slow: **−51.6 s over 2 h 36 m (−0.55 %)** in the 21 Sep overnight run.
> Fine for intervals, wrong for absolute alignment across hours. Align on host time.

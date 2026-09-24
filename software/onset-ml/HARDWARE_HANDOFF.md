# Handoff — hardware/firmware → software, 24 Sep 2026

Successor to `HANDOFF_hardware-to-software_2026-09-16.md`. That document still describes the
board and harness correctly; this one covers what changed on 23–24 Sep. Where the two disagree,
**this one is current**.

Firmware is now **`bench4_live`**, not `bench3_live`. bench3 has no mic, no button, no battery
channel, and a PPG beat detector that does not work. Do not flash bench3.

---

## 1. Where everything lives

| What | Path |
|---|---|
| Firmware source (working copy) | `~/Documents/Arduino/bench4_live/bench4_live.ino` |
| Firmware archive + build + UF2 | `~/Documents/Amulet XPL/firmware-and-tools/bench4_live/` |
| Bridge (serial/BLE → HTTP+SSE, :8787) | `onset-ml/tools/bench_bridge.py` |
| Live scope + voice notes (:8792) | `onset-ml/tools/bench_scope.py` + `bench_scope.html` |
| CLI recorder with countdown | `onset-ml/tools/mic_record.py` |
| Whisper model | `onset-ml/models/ggml-base.en.bin` (147,964,211 bytes) |
| Captures | `onset-ml/captures/voice-<stamp>.wav` |

Build: **flash 156,980 B (19%)**, **RAM 121,300 B (51%)**, 116 KB free. The 96 KB audio buffer is
most of that RAM.

### Flashing
The board's app handles the 1200-baud DFU hook, so the normal two-step works:
```bash
cd ~/Documents/Arduino/bench4_live
arduino-cli compile -b Seeeduino:nrf52:xiaonRF52840Sense --build-path build .
# stop anything holding the port first: pkill -f bench_bridge.py ; pkill -f bench_scope.py
# 1200-baud touch, wait for re-enumeration, then:
~/Library/Python/3.9/bin/adafruit-nrfutil dfu serial \
  --package build/bench4_live.ino.zip -p /dev/cu.usbmodem2101 -b 115200 --singlebank
```
**If the app is crashed, no uploader can ever get in** — the 1200-baud touch is serviced by the
running application, so a dead app never requests DFU. The only recovery is a physical
**double-tap RESET** (bootloader detects that itself), then drag `bench4_live.uf2` onto the
`XIAO-SENSE` drive. Bootloader USB `idProduct` = **69 (0x0045)**; application = **32837 (0x8045)** —
poll `ioreg -p IOUSB` to tell "bootloader engaged" from "drive didn't mount".
Copying a UF2 onto `/Volumes/XIAO-SENSE` **from a background process raises `PermissionError`**
(macOS removable-volume TCC) and looks exactly like the double-tap having failed.

---

## 2. Protocol

All lines are ASCII, newline-terminated, over USB serial **and** BLE (Nordic UART, `Amulet-XPL`).
`<ms>` is the board's `sampleCounter` in milliseconds throughout.

| Line | Rate | Fields |
|---|---|---|
| `S,<ms>,<fsr>,<pulse>,<thresh>` | 100 Hz | raw ADC, 10-bit. **Unchanged from bench3.** |
| `F,<ms>,<filt>,<thr>,<amp>,<usable>` | 100 Hz | **NEW.** Filtered PPG. First three are ×10 fixed-point. `usable` is 0/1. |
| `B,<ms>,<bpm>,<ibi>` | per beat | `bpm` is a median of 9 intervals; `ibi` is the raw instantaneous interval — use `ibi` for HRV, not `bpm`. |
| `V,<ms>,<mV>,<pct>,<chg>,<fast>,<raw>,<sag>` | 2 Hz | **NEW.** Battery. `chg` 1 = charging, `fast` 1 = 100 mA. |
| `K,<ms>,<down>,<count>` | on edge | **NEW.** Button. `down` 1 = pressed. Edge-triggered only; `h` also emits one on demand. |
| `M,<ms>,<rms>,<peak>` | 50 Hz | **NEW.** Mic level, only while the mic is on. |
| `A,<seq>,<base64>` | during dump | **NEW.** Audio chunks, 135 raw bytes each. |
| `AEND,<total>,<rate>` | end of dump | **NEW.** Byte count and sample rate. |
| `H,<mode>,<hz>,<vol>,<peak>,<sync>,<gap_us>,<dropped>` | 10 Hz / 1 Hz | haptics, as bench3 |
| `R,<ms>,<rssi>` | 50 Hz | link RSSI while a BLE central is connected |
| `I,<text>` | on event | human-readable info |

**Commands** (single chars, no newline, via serial, BLE, or `POST /cmd {"c":"x"}`):

```
haptics  t tone 3s   b tap   w wide sweep   n narrow sweep
         z buzz toggle   o hold (60 s cap)   s stop   + / - volume   k beat-sync
mic      m on/off   r record 3 s   d download   g cycle gain   l latency probe
battery  c toggle 50/100 mA charge current
status   h  (emits H and K immediately)
```

### Adding a line type to the bridge or scope
`bench_scope.py` filters lines through one regex — `LINE = re.compile(...)`. **A new letter must be
added there or the line is silently dropped.** This cost time twice.

---

## 3. What was broken and is now fixed

Each of these was measured, not guessed. Do not reintroduce them.

**PPG beat detection did not work at all.** bench3 fed raw ADC into an amplitude threshold with no
filtering. Measured: 38.6% of signal energy below 0.5 Hz (baseline drift), only 13.9% in the cardiac
band. The threshold tracked the drift, so BPM swung 45–163 at rest. Now: one-pole high-pass ~0.5 Hz
plus low-pass ~5 Hz, envelope-tracked threshold, median of 9 intervals. Drift energy fell to 1.2%.

**Then it double-counted the dicrotic notch.** Runs of ~150 bpm against a true 75 — exactly double.
A fixed 300 ms refractory let the secondary arterial pulse through. Now the refractory is **55% of
the running median interval**, and the fire threshold is 68% of envelope (the notch sits at 40–60%).
Double-counts: 16/55 → **0/37**. Verified against offline autocorrelation: detector 73 bpm, ground
truth 74.1.

**The quality gate was too permissive.** `MIN_AMP` was 8 filtered counts; a 13-count signal passed
and produced confident nonsense (126 bpm, 11/47 intervals halving or doubling). Now **40**. A real
finger-on signal measures 100–400. Below the gate the firmware emits **no** `B` lines rather than
inventing them — `F.usable` goes 0 and the scope shows it in red. **Keep this behaviour.**

**The battery gauge flickered 7% on quantisation alone.** 10-bit on a divided cell voltage is
10.4 mV per count, and one count is 2.2% of state of charge. Now read at **12-bit, 32× averaged,
`AR_INTERNAL_1_8`**, then the ADC is handed straight back to the sensors at 10-bit/`AR_DEFAULT`.
Voltage swing at rest: 25 mV → **3 mV**.

**The battery ADC was running far out of spec.** The divider is 1M/510k, so source impedance is
**337.7 kΩ**. The nRF52840 requires **TACQ ≥ 20 µs** above 200 kΩ; the core default is 3 µs (rated
for 10 kΩ). Every reading before this was ~34× out of spec and read low. `analogSampleTime(20)` is
now set for the battery read and restored to 3 afterwards.

**PDM gain was never applied.** `PDM.cpp:159` — `begin()` calls `setGain(DEFAULT_PDM_GAIN)` where
`DEFAULT_PDM_GAIN = 20`. On the nRF52840 `GAINL` register 0x28 (40) is 0 dB in 0.5 dB steps, so 20
is **−10 dB**. Any `setGain()` before `begin()` is silently discarded. Clips came back at peak
305/32767. **`setGain()` must be called AFTER `begin()`.** Default is now 80 = +20 dB (hardware max).
Peak went 305 → **15614**, a 51× improvement.

**The button was never read.** bench3 never configured D2. Now `INPUT_PULLUP` with 25 ms debounce,
emitting `K` on both edges.

**Charge current was set unsafely and by the wrong method.** See §4.

---

## 4. ⚠ Battery safety — unresolved, needs a decision

**The cell is ~70 mAh** (recorded in the 16 Sep handoff, §"Battery").

- 50 mA = **0.71C** — inside the documented 0.5–1C charging band.
- 100 mA = **1.43C** — **above it.** Battery University's charging guidance puts energy cells at
  0.5–1C, with ≤0.8C recommended for longevity.

Firmware now **defaults to 50 mA**. `c` still selects 100 mA deliberately. Do not change the default
without a bigger cell — the 250–500 mAh upgrade already flagged in the 16 Sep handoff is the real fix.

**The correct way to select the current** (Seeed's own BSP, `variant.cpp initVariant`):
```c
pinMode(PIN_CHARGING_CURRENT, INPUT);                       // high-Z -> 2.7k  -> 50 mA
pinMode(PIN_CHARGING_CURRENT, OUTPUT); digitalWrite(..., LOW);  // 2.7k||2.7k -> 100 mA
```
**Never drive it HIGH.** That is not a documented mode — it sources current into the charger's ISET
node. Seeed's own *wiki code example* does this and is wrong; their BSP does not.

**200 mA is not reachable.** It needs R_ISET ≈ 675 Ω; the only two reachable values are 2.7 kΩ and
1.35 kΩ, both fixed in copper. The ISET node is not brought to any pad. Would require rework on a
0.4 mm-pitch BGA.

**There is no battery temperature protection on this board.** The schematic disables the NTC path
(fixed 10 kΩ on TS), so the charger's JEITA behaviour is inactive. Relevant for a cell worn against
skin overnight.

**Charging is automatic in hardware.** The BQ25101 starts as soon as VBUS appears. Firmware cannot
enable or disable it — only select the rate.

**Retracted claim:** an earlier note in this session said system draw is 50–100 mA. That was inferred
from a test that used the undefined HIGH state, so it does not establish the charge current was
50 mA. **System draw has not been measured.** Do that before quoting battery life.

---

## 5. Voice notes and transcription

**Architecture: the board records, the Mac transcribes.** The nRF52840 has 256 KB of RAM; no speech
model runs on it. Do not attempt on-device ASR.

Flow, all server-side in `bench_scope.py` (`POST /record`):
```
r  ->  3.6 s wait  ->  d  ->  collect A chunks  ->  DC-remove + peak-normalise
   ->  WAV  ->  whisper-cli  ->  transcript in /data
```
End to end **~5 s**. State machine is `idle → recording → downloading → transcribing → done|error`,
exposed as `rec` in `/data`; the last clip is served at `GET /voice.wav`.

**whisper.cpp 1.9.4**, installed via `brew install whisper.cpp`, binary `/opt/homebrew/bin/whisper-cli`.
Prebuilt Apple Silicon bottle with Metal; 513 ms for a 3 s clip on `base.en`. Entirely local.
```bash
whisper-cli -m models/ggml-base.en.bin -f clip.wav \
  --language en --no-timestamps --suppress-nst --temperature 0 -nt
```

**Audio facts that matter:**
- The PDM clock divides to **exactly 16.000 kHz** (1.280 MHz ÷ 80), which is Whisper's native rate.
  Nothing resamples anywhere. Do not add a resampling step.
- **DC removal is mandatory**, not hygiene — the MSM261D3526H1CPM datasheet specifies a typical
  **4% of full-scale** DC offset. Remove it *before* normalising or it eats the headroom.
- The first **60 ms** after the mic starts is discarded in firmware (20 ms power-up, 5 ms decimation
  delay, ~50 transient samples). Do not shorten this.
- Whisper pads everything to 30 s internally and its mel stage normalises against each clip's own
  peak, so **gain in post barely helps**. SNR at the microphone is what matters.
- **Whisper invents text on silence.** Check `clip.peak` before trusting a transcript — above ~5000
  means real speech; a few hundred means it was guessing.

**`REC_SECONDS` is 3** (48000 samples = 96 KB). That is the hard ceiling on a spoken note. There is
room for roughly 5 s before RAM gets tight; raising it is a one-line change plus a reflash.

---

## 6. Channel status as of this handoff

| Channel | State | Notes |
|---|---|---|
| Haptics (Drake LRA) | **working** | `peakOut` 19660 at vol 0.60. Amp Vin is on the **switched battery**, so haptics are dead with the cell disconnected even though the firmware reports driving. |
| PPG (D1) | **working** | 73 bpm vs 74.1 ground truth, beat sd 2.6. Needs steady finger contact — amplitude swings 60× with pressure. Watch `F.amp`; 100–400 is good, under 40 reports nothing. |
| Button (D2 / P0.28) | **working** | Was permanently shorted: both wires on legs from the same internally-joined pair. Fixed by moving one to a **diagonally opposite** leg. |
| Battery | **working** | 4046 mV, 84%, 3 mV swing. Charging at 50 mA. |
| Mic (PDM) | **working** | +20 dB, peak 15614. Coexists with I²S and BLE without faulting. |
| FSR (D0) | **needs confirming** | Reads a steady **366** at rest, not the `<20` the FSR guide expects. That is ~15.6 kΩ, which is normal for the **Electrokit flex sensor** the guide says may be fitted instead of a true FSR. **Press/bend it and check the range before trusting the channel.** |

---

## 7. Open items

1. **Measure actual system current draw.** Not done. Needed before any battery-life claim.
2. **Confirm the FSR/flex sensor range** and recalibrate the expected rest/press values in
   `amulet_wiring_FSR-strip.html`.
3. **Cell upgrade to 250–500 mAh** would make 100 mA charging safe (0.2–0.4C) and fix runtime.
4. **Power reduction, unimplemented, in rough order of value:** CPU sleep between ticks (3.16 µA
   asleep vs 3.3–6.3 mA busy-looping — the loop currently spins); BLE connection interval and slave
   latency; `digitalWrite(19, LOW)` to cut the mic's 770 µA at the rail when audio is not in use.
   The DC/DC buck **is** now enabled (L3 10 µH is fitted) via `sd_power_dcdc_mode_set()`.
5. **Never write `NRF_POWER`/`NRF_CLOCK`/`NRF_RADIO` registers directly after `Bluefruit.begin()`** —
   they are SoftDevice-owned and a direct write hard-faults into a reset loop. Use the `sd_*` calls.
   This bit the project once already (16 Sep).

---

---

## 8. Battery + BLE operation (24 Sep, measured)

The board runs fully on the cell with no USB. Everything below was measured over the air.

### Negotiated link
```
I,ble mtu 247 payload 244 interval 15.00 ms latency 0
```
| | value |
|---|---|
| connection interval | **15.00 ms** |
| ATT payload | 244 B (MTU 247) |
| S lines delivered | **99.5/s of a 100 Hz source — 100%** |
| packet gap | median 14.8 ms, p95 16.3 ms |
| command round trip (host -> board -> host) | **33 ms median** |
| audio download | **96 KB in 3.0 s = 31.1 kB/s raw, 41.5 kB/s on the wire, zero loss** |

### The connection-interval trap
The firmware used to request **7.5-15 ms** — the Bluetooth spec floor. Apple's accessory
guidelines require **Interval Min >= 15 ms and Max >= Min + 15 ms**, so macOS *silently ignored*
the request and imposed its own **30 ms**. Actual latency was double what the code appeared to ask
for, and only 94% of lines arrived.

Requesting **15-30 ms** (`setConnInterval(12, 24)`, units of 1.25 ms) is compliant, so it is
honoured: interval halves to 15 ms and delivery goes to 100%. **Do not "optimise" this back down
to 6 -- it makes things worse.** The board reports what it actually negotiated, so never assume.

Also now requested on connect, all previously unused: `requestPHY(BLE_GAP_PHY_2MBPS)`,
`requestDataLengthUpdate()`, `requestMtuExchange(247)`, and
`requestConnectionParameter(12, 0, 600)`. Each can be refused by the central; the link just keeps
its previous setting, so they are safe to attempt unconditionally.

The BLE packet buffer sizes itself from the **negotiated** MTU (`getMtu() - 3`, capped at 244)
rather than a hardcoded 180. A central that refuses the MTU exchange leaves it at 23 and the
firmware adapts instead of truncating.

### Audio over BLE
`dumpService()` picks its transport at run time: `Serial` if a USB host has the port open,
otherwise BLE notifications. Chunks are `DUMP_BYTES 171` raw bytes -> 228 base64 chars, which plus
the `A,<seq>,` prefix fits inside one 244-byte notification.

Two things make it fast, and both matter:
1. **Telemetry is suppressed while a dump is in flight** (`&& !dumping` on the S/F and M emitters)
   so the whole radio budget goes to audio. They resume automatically.
2. **Flow control by short write.** `bleuart.write()` returning less than requested means the radio
   queue is full; `dumpPos` is left alone and the same chunk is retried next pass. Nothing is ever
   dropped or half-sent.

Host side needs no change: `bench_scope.py` reads `A`/`AEND` from the bridge, and the bridge
forwards them identically whether it is on USB or BLE.

## 9. Wiring guides written in this session

In `~/Documents/Amulet XPL/wiring-guides/`:

- `amulet_wiring_FSR-junction.html` — the divider junction as a third stripboard row, instead of
  soldering the 10 kΩ onto the sensor tail.
- `amulet_wiring_button-legs.html` — which legs of a 6 mm tactile switch to use, and why diagonal
  is the only pairing that is right regardless of orientation.
- `amulet_bench_haptic-triage.html` — the silent-LRA diagnosis. Note its conclusion that the
  double-tap never engaged was wrong; it was a `PermissionError` on the volume write.

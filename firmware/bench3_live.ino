/*
 * bench3_live -- bench2_live (FSR on A0 + Pulse Sensor Amped on A1) PLUS the
 * haptic channel: Adafruit MAX98357A I2S amp driving the TITAN Drake LRA.
 *
 * Board:  Seeed XIAO nRF52840 Sense (Seeed nRF52 Boards, not mbed)
 *
 * Wiring (adds to bench2):
 *   D8  (P1.13) -> amp DIN
 *   D9  (P1.14) -> amp BCLK
 *   D10 (P1.15) -> amp LRC
 *   amp Vin  -> 3.3 V rail (or its own 3.0-3.3 V supply, GND shared)
 *   amp GND  -> GND rail
 *   amp SD   -> Vin   (tie high: floating drifts into shutdown)
 *   amp GAIN -> GND   (12 dB; the level is set here in software)
 *   Drake LRA across the amp's + / - output block. Neither LRA lead to GND.
 *
 * Serial, 115200. Same F/P/B/I lines as bench2_live, plus:
 *   R,<ms>,<rssi_dbm>                   link RSSI measured on the board, 50 Hz while a BLE central is connected
 *   H,<mode>,<hz>,<vol>,<peak>,<sync>   haptic status: 10 Hz while driving, 1 Hz idle
 *     mode: idle | tone | tap | sweep     hz: current tone frequency
 *     vol: 0.10-1.00                      peak: largest |sample| in the last buffer
 *     sync: 0/1 beat-synced taps
 * Commands (single chars, sent by the dashboard or a serial monitor):
 *   t  3 s tone at 160 Hz (Drake HF resonance)     b  150 ms tap
 *   w  wide sweep 40-300 Hz, 700 ms/step            n  narrow sweep 120-210 Hz, 1 s/step
 *   z  LRA on/off toggle: 250 ms on / 250 ms off at 160 Hz until 'z' or 's' (5 min cap)
 *   s  stop (kills the I2S clock -> amp shuts down)  + / -  volume by 0.1
 *   k  toggle beat-synced taps (80 ms tap per detected heartbeat)
 *   h  print status now
 *
 * Every drive mode is bounded IN THE BUFFER FILLER, not in loop(): if loop() ever
 * stalls, the tone still dies on schedule. A sustained tone at resonance is the
 * hardest thing you can do to this coil, so nothing here runs free.
 */
#include <Adafruit_TinyUSB.h>  // Serial lives in TinyUSB on this core
#include <bluefruit.h>         // BLE Nordic UART: same lines, same commands, over the air
#include <nrf.h>
#include <stdio.h>
#include <string.h>
#include <math.h>

// ---------------------------------------------------------------- sensors
const uint8_t  FSR_PIN   = A0;    // D0 = P0.02
const uint8_t  PULSE_PIN = A1;    // D1 = P0.03
const uint32_t TICK_US   = 2000;  // 500 Hz
const uint16_t IDLE      = 470;   // mid-scale on this board

uint32_t seq = 0, lastSampleUs = 0, maxGapUs = 0, dropped = 0;

// ---------------------------------------------------------------- output (USB serial + BLE UART)
// Every protocol line goes through emit(): to USB if a host is attached and the buffer has room,
// and to the BLE UART if a central is connected with notifications on. Drops are counted per link.
BLEUart bleuart;
static uint32_t bleDropped = 0;
static char     lineBuf[96];
static inline bool bleReady() { return Bluefruit.connected() && bleuart.notifyEnabled(); }
// Whole lines are packed into one notification-sized packet (fits an MTU of 185+) and sent as a unit,
// so a full radio queue drops a whole packet, never half a line. Flushed when full or every 20 ms.
static char     blePkt[180];
static uint16_t blePktLen = 0;
static uint32_t blePktMs = 0;
static void bleFlush() {
  if (blePktLen) {
    if (bleReady() && bleuart.write((const uint8_t *)blePkt, blePktLen) != blePktLen) bleDropped++;
    blePktLen = 0;
  }
  blePktMs = millis();
}
static void blePack(const char *line, size_t n) {
  if (n + 1 > sizeof blePkt) return;
  if (blePktLen + n + 1 > sizeof blePkt) bleFlush();
  memcpy(blePkt + blePktLen, line, n); blePktLen += n; blePkt[blePktLen++] = '\n';
}
static void emit(const char *line) {
  const size_t n = strlen(line);
  if (Serial) {
    if (Serial.availableForWrite() > (int)n + 2) { Serial.write(line, n); Serial.write('\n'); }
    else dropped++;
  }
  if (bleReady()) blePack(line, n);
}
#define EMITF(...) do { snprintf(lineBuf, sizeof lineBuf, __VA_ARGS__); emit(lineBuf); } while (0)
// Link RSSI, measured by THIS radio on every connection event (sd_ble_gap_rssi_start via monitorRssi;
// getRssi() returns the SoftDevice's latest per-event measurement). Emitted as R,<ms>,<rssi_dbm> on
// every 2nd S tick (50 Hz) while a central is connected. Same idea as the AmuletRSSI firmware: the
// phone/Mac cannot read RSSI fast, the board can. Added 16 Sep 2026 (onset-ml side).
static volatile uint16_t rssiConn = BLE_CONN_HANDLE_INVALID;
static void bleConnect(uint16_t h) {
  rssiConn = h;
  BLEConnection *c = Bluefruit.Connection(h);
  if (c) c->monitorRssi(0);
}
static void bleDisconnect(uint16_t h, uint8_t r) { (void)h; (void)r; rssiConn = BLE_CONN_HANDLE_INVALID; }
static void bleSetup() {
  Bluefruit.configPrphBandwidth(BANDWIDTH_MAX);   // MTU 247, biggest event length
  Bluefruit.begin();
  Bluefruit.setName("Amulet-XPL");
  Bluefruit.setTxPower(4);
  Bluefruit.Periph.setConnInterval(6, 12);        // 7.5-15 ms
  Bluefruit.Periph.setConnectCallback(bleConnect);
  Bluefruit.Periph.setDisconnectCallback(bleDisconnect);
  bleuart.begin();                                // unbuffered: one write() == one notification, atomic
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(bleuart);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.setInterval(32, 244);     // 20 ms fast, 152.5 ms slow
  Bluefruit.Advertising.setFastTimeout(30);
  Bluefruit.Advertising.start(0);                 // forever
}

int      Signal;
int      thresh = IDLE + 20, P = IDLE + 20, T = IDLE + 20, amp = 100;
bool     Pulse = false, firstBeat = true, secondBeat = false;
uint32_t sampleCounter = 0, lastBeatTime = 0;
int      IBI = 600, BPM = 0, rate[10];

uint32_t nextUs = 0, lastBlink = 0;
uint8_t  tick = 0;

// ---------------------------------------------------------------- haptics
// PSEL takes ABSOLUTE nRF port numbers: P1.xx == 32 + xx.
#define NRF_PIN_SDOUT 45  // P1.13  XIAO D8  -> DIN
#define NRF_PIN_BCLK  46  // P1.14  XIAO D9  -> BCLK
#define NRF_PIN_LRCK  47  // P1.15  XIAO D10 -> LRC

// MCK 32MDIV21 = 1.5238 MHz, RATIO 32X -> LRCK 47619 Hz, SCK = MCK = 1.52 MHz.
// The MAX98357A needs BCLK >= 512 kHz; RATIO 96X (15873 Hz) put SCK at 508 kHz,
// just under spec. 32X is the fastest LRCK this MCK allows and is safely inside.
#define SAMPLE_RATE 47619
#define FRAMES 512            // 10.75 ms per buffer = the deadline for loop()

static int16_t bufA[FRAMES * 2] __attribute__((aligned(4)));
static int16_t bufB[FRAMES * 2] __attribute__((aligned(4)));
static int16_t *activeBuf = bufA;

// Drive ceiling comes from the SUPPLY: bridge-tied amp swings ~2x Vdd. The
// Drake is rated 0.5-7 Vpp. 3.3 V -> 6.6 Vpp, under the rating, so cap = 1.0.
// On a LiPo set SUPPLY_VOLTS 4.2f and the cap becomes 0.83 automatically.
#define SUPPLY_VOLTS 4.2f   // Board B is on a LiPo (hardware handoff, pending item 2); volume cap becomes 0.83
#define COIL_MAX_VPP 7.0f
static const float VOLUME_MAX = (COIL_MAX_VPP / (2.0f * SUPPLY_VOLTS)) > 1.0f ? 1.0f : (COIL_MAX_VPP / (2.0f * SUPPLY_VOLTS));
static float VOLUME = 0.60f;  // start moderate; walk up with '+'

#define RES_HZ 160.0f         // Drake HF resonance
#define BUFS_FOR_MS(ms) ((uint32_t)(ms) * SAMPLE_RATE / 1000 / FRAMES)
#define TONE_MS 3000
#define TAP_MS 150
#define SYNC_TAP_MS 80
#define COOLDOWN_MS 1500      // between long runs (tone/sweep); taps are exempt
#define BUZZ_ON_MS 250
#define BUZZ_OFF_MS 250
#define BUZZ_TOTAL_MS 300000  // LRA toggle: runs until switched off, 5 min hard cap
#define HOLD_MAX_MS   60000   // 'o' continuous drive: on until switched off, 60 s hard cap (coil heat)

static const float SWEEP_WIDE[]   = { 40, 50, 60, 70, 85, 100, 115, 130, 150, 175, 210, 250, 300 };
static const float SWEEP_NARROW[] = { 120, 140, 155, 160, 165, 175, 190, 210 };
#define N_WIDE   (sizeof(SWEEP_WIDE) / sizeof(SWEEP_WIDE[0]))
#define N_NARROW (sizeof(SWEEP_NARROW) / sizeof(SWEEP_NARROW[0]))

enum HMode : uint8_t { H_IDLE, H_TONE, H_TAP, H_SWEEP, H_BUZZ, H_HOLD };
static volatile HMode hmode = H_IDLE;
static bool     i2sOn = false;
static uint32_t runBufs = 0, runLimit = 0;        // bounded run, counted in buffers
static float    phase = 0.0f, curHz = RES_HZ;
static const float *sweepTab = SWEEP_WIDE;
static uint8_t  sweepN = N_WIDE, sweepIdx = 0;
static uint32_t sweepStepBufs = 0, sweepBufs = 0;
static uint32_t readyAt = 0;
static bool     beatSync = false;
static volatile int32_t peakOut = 0;
static uint32_t buzzBufs = 0, buzzTotal = 0;
static uint32_t lastHStatus = 0;

static void i2sInit() {
  NRF_I2S->CONFIG.MODE     = I2S_CONFIG_MODE_MODE_Master << I2S_CONFIG_MODE_MODE_Pos;
  NRF_I2S->CONFIG.RXEN     = I2S_CONFIG_RXEN_RXEN_Disabled << I2S_CONFIG_RXEN_RXEN_Pos;
  NRF_I2S->CONFIG.TXEN     = I2S_CONFIG_TXEN_TXEN_Enabled << I2S_CONFIG_TXEN_TXEN_Pos;
  NRF_I2S->CONFIG.MCKEN    = I2S_CONFIG_MCKEN_MCKEN_Enabled << I2S_CONFIG_MCKEN_MCKEN_Pos;
  NRF_I2S->CONFIG.MCKFREQ  = I2S_CONFIG_MCKFREQ_MCKFREQ_32MDIV21 << I2S_CONFIG_MCKFREQ_MCKFREQ_Pos;
  NRF_I2S->CONFIG.RATIO    = I2S_CONFIG_RATIO_RATIO_32X << I2S_CONFIG_RATIO_RATIO_Pos;
  NRF_I2S->CONFIG.SWIDTH   = I2S_CONFIG_SWIDTH_SWIDTH_16Bit << I2S_CONFIG_SWIDTH_SWIDTH_Pos;
  NRF_I2S->CONFIG.ALIGN    = I2S_CONFIG_ALIGN_ALIGN_Left << I2S_CONFIG_ALIGN_ALIGN_Pos;
  NRF_I2S->CONFIG.FORMAT   = I2S_CONFIG_FORMAT_FORMAT_I2S << I2S_CONFIG_FORMAT_FORMAT_Pos;
  NRF_I2S->CONFIG.CHANNELS = I2S_CONFIG_CHANNELS_CHANNELS_Stereo << I2S_CONFIG_CHANNELS_CHANNELS_Pos;
  NRF_I2S->PSEL.SCK   = NRF_PIN_BCLK;
  NRF_I2S->PSEL.LRCK  = NRF_PIN_LRCK;
  NRF_I2S->PSEL.SDOUT = NRF_PIN_SDOUT;
  NRF_I2S->PSEL.MCK   = (I2S_PSEL_MCK_CONNECT_Disconnected << I2S_PSEL_MCK_CONNECT_Pos);
  NRF_I2S->PSEL.SDIN  = (I2S_PSEL_SDIN_CONNECT_Disconnected << I2S_PSEL_SDIN_CONNECT_Pos);
  NRF_I2S->RXTXD.MAXCNT = FRAMES;  // 32-bit words; one stereo frame per word
}

// Zeros are NOT silence: with the clock running the amp's output stage keeps
// switching into the coil. Dropping the clock makes the MAX98357A shut down.
static void i2sStop() {
  NRF_I2S->TASKS_STOP = 1;
  NRF_I2S->ENABLE = 0;
  i2sOn = false;
  peakOut = 0;
}
static void i2sStart() {
  memset(bufA, 0, sizeof(bufA));
  memset(bufB, 0, sizeof(bufB));
  NRF_I2S->TXD.PTR = (uint32_t)bufA;
  activeBuf = bufA;
  NRF_I2S->EVENTS_TXPTRUPD = 0;
  NRF_I2S->ENABLE = 1;
  NRF_I2S->TASKS_START = 1;
  i2sOn = true;
}

static void hapticStatus() {
  const char *m = hmode == H_TONE ? "tone" : hmode == H_TAP ? "tap" : hmode == H_SWEEP ? "sweep" : hmode == H_BUZZ ? "buzz" : hmode == H_HOLD ? "hold" : "idle";
  EMITF("H,%s,%d,%.2f,%d,%d,%lu,%lu", m, (int)curHz, (double)VOLUME, (int)peakOut, beatSync ? 1 : 0,
        (unsigned long)maxGapUs, (unsigned long)(dropped + bleDropped));
  maxGapUs = 0;
}

static void startRun(HMode m, float hz, uint32_t ms) {
  hmode = m; curHz = hz; runBufs = 0; runLimit = BUFS_FOR_MS(ms); phase = 0.0f; lastHStatus = 0;
  if (!i2sOn) i2sStart();
}
static void startSweep(const float *tab, uint8_t n, uint32_t stepMs) {
  sweepTab = tab; sweepN = n; sweepIdx = 0; sweepBufs = 0; sweepStepBufs = BUFS_FOR_MS(stepMs);
  hmode = H_SWEEP; curHz = tab[0]; phase = 0.0f;
  if (!i2sOn) i2sStart();
}
static void endRun(bool cooldown) {
  hmode = H_IDLE;
  if (cooldown) readyAt = millis() + COOLDOWN_MS;
}

// Fills one buffer with the current mode's waveform. All time bounds live here.
static void fillBuffer(int16_t *buf) {
  int32_t pk = 0;
  bool last = false;

  if (hmode == H_SWEEP) {
    if (sweepBufs >= sweepStepBufs) { sweepBufs = 0; sweepIdx++; if (sweepIdx < sweepN) curHz = sweepTab[sweepIdx]; }
    if (sweepIdx >= sweepN) { memset(buf, 0, FRAMES * 2 * sizeof(int16_t)); peakOut = 0; endRun(true); return; }
    sweepBufs++;
  } else if (hmode == H_BUZZ) {
    if (buzzTotal >= BUFS_FOR_MS(BUZZ_TOTAL_MS)) { memset(buf, 0, FRAMES * 2 * sizeof(int16_t)); peakOut = 0; endRun(true); return; }
    buzzTotal++; buzzBufs++;
    const uint32_t period = BUFS_FOR_MS(BUZZ_ON_MS + BUZZ_OFF_MS);
    if (buzzBufs >= period) buzzBufs = 0;
    if (buzzBufs >= BUFS_FOR_MS(BUZZ_ON_MS)) { memset(buf, 0, FRAMES * 2 * sizeof(int16_t)); peakOut = 0; phase = 0.0f; return; }
    last = (buzzBufs + 1 >= BUFS_FOR_MS(BUZZ_ON_MS));
  } else if (hmode == H_TONE || hmode == H_TAP || hmode == H_HOLD) {
    if (runBufs >= runLimit) { memset(buf, 0, FRAMES * 2 * sizeof(int16_t)); peakOut = 0; endRun(hmode != H_TAP); return; }
    runBufs++;
    last = (runBufs >= runLimit);   // fade the final buffer so the coil isn't handed a step
  } else {
    memset(buf, 0, FRAMES * 2 * sizeof(int16_t)); peakOut = 0; return;
  }

  const float step = 2.0f * 3.14159265f * curHz / (float)SAMPLE_RATE;
  for (int i = 0; i < FRAMES; i++) {
    const float env = last ? (1.0f - (float)i / (float)FRAMES) : 1.0f;
    float v = sinf(phase) * 32767.0f * VOLUME * env;
    phase += step; if (phase > 6.28318531f) phase -= 6.28318531f;
    const int16_t s = (int16_t)v;
    buf[i * 2] = s; buf[i * 2 + 1] = s;   // same mono sample on both channels
    const int32_t as = s < 0 ? -(int32_t)s : (int32_t)s;
    if (as > pk) pk = as;
  }
  peakOut = pk;
}

static void i2sService() {
  if (!i2sOn) return;
  if (NRF_I2S->EVENTS_TXPTRUPD == 0) return;
  NRF_I2S->EVENTS_TXPTRUPD = 0;
  (void)NRF_I2S->EVENTS_TXPTRUPD;
  int16_t *next = (activeBuf == bufA) ? bufB : bufA;
  fillBuffer(next);
  NRF_I2S->TXD.PTR = (uint32_t)next;
  activeBuf = next;
  // Once a run has ended AND the zero buffer has gone out, drop the clock.
  if (hmode == H_IDLE && peakOut == 0 && runBufs == 0 && sweepBufs == 0) { /* handled below */ }
}

// Stop the clock a little after the last run ends so the final zero buffer plays out.
static uint32_t idleSince = 0;
static void hapticHousekeeping() {
  const uint32_t now = millis();
  if (hmode == H_IDLE && i2sOn) {
    if (!idleSince) idleSince = now;
    else if (now - idleSince > 40) { i2sStop(); idleSince = 0; }
  } else idleSince = 0;

  const uint32_t period = hmode == H_IDLE ? 1000 : 100;
  if (now - lastHStatus >= period && (Serial || bleReady())) { lastHStatus = now; hapticStatus(); }
}

static void hapticCommand(char c) {
  const uint32_t now = millis();
  const bool busy = hmode != H_IDLE;
  const bool cooling = (int32_t)(now - readyAt) < 0;
  switch (c) {
    case 't': if (!busy && !cooling) { startRun(H_TONE, RES_HZ, TONE_MS); emit("I,tone 160 Hz, 3 s"); }
              else emit(cooling ? "I,cooling down -- wait a moment" : "I,busy"); break;
    case 'b': if (!busy) { startRun(H_TAP, RES_HZ, TAP_MS); emit("I,tap"); } break;
    case 'w': if (!busy && !cooling) { startSweep(SWEEP_WIDE, N_WIDE, 700); emit("I,wide sweep 40-300 Hz"); }
              else emit(cooling ? "I,cooling down -- wait a moment" : "I,busy"); break;
    case 'n': if (!busy && !cooling) { startSweep(SWEEP_NARROW, N_NARROW, 1000); emit("I,narrow sweep 120-210 Hz"); }
              else emit(cooling ? "I,cooling down -- wait a moment" : "I,busy"); break;
    case 'z': if (hmode == H_BUZZ) { endRun(false); i2sStop(); emit("I,LRA off"); }
              else if (!busy) { buzzBufs = 0; buzzTotal = 0; hmode = H_BUZZ; curHz = RES_HZ; phase = 0.0f; lastHStatus = 0; if (!i2sOn) i2sStart(); emit("I,LRA on: 250/250 ms bursts at 160 Hz"); }
              else emit("I,busy"); break;
    case 'o': if (hmode == H_HOLD) { endRun(true); i2sStop(); emit("I,LRA hold off"); }
              else if (!busy) { startRun(H_HOLD, RES_HZ, HOLD_MAX_MS); emit("I,LRA hold on: continuous 160 Hz, 60 s cap"); }
              else emit("I,busy"); break;
    case 's': endRun(false); i2sStop(); emit("I,haptics stopped"); break;
    case '+': VOLUME += 0.1f; if (VOLUME > VOLUME_MAX) VOLUME = VOLUME_MAX; EMITF("I,volume %.2f", (double)VOLUME); break;
    case '-': VOLUME -= 0.1f; if (VOLUME < 0.1f) VOLUME = 0.1f; EMITF("I,volume %.2f", (double)VOLUME); break;
    case 'k': beatSync = !beatSync; emit(beatSync ? "I,beat-sync ON: a tap per heartbeat" : "I,beat-sync off"); break;
    case 'h': hapticStatus(); break;
    default: break;
  }
  lastHStatus = 0;  // push a status line right away
}

// ---------------------------------------------------------------- setup/loop
void setup() {
  Serial.begin(115200);
  const uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 2000) { }
  bleSetup();

  pinMode(LED_GREEN, OUTPUT);
  digitalWrite(LED_GREEN, HIGH);  // active-LOW
  analogReadResolution(10);
  pinMode(FSR_PIN, INPUT);
  pinMode(PULSE_PIN, INPUT);

  // Crystal HFCLK so the I2S rate is exact (TinyUSB may drop it on VBUS removal).
  // CLOCK is a SoftDevice-owned peripheral once BLE is up: a direct register write hard-faults
  // and the board reset-loops. Ask the SoftDevice for the crystal instead.
  { uint8_t sdOn = 0; sd_softdevice_is_enabled(&sdOn);
    if (sdOn) sd_clock_hfclk_request();
    else { NRF_CLOCK->EVENTS_HFCLKSTARTED = 0; NRF_CLOCK->TASKS_HFCLKSTART = 1; } }
  i2sInit();  // configured but NOT started: amp stays shut down until asked

  emit("I,bench3_live started (USB + BLE Amulet-XPL) -- S,ms,fsr,pulse,thr @100Hz, B,ms,bpm,ibi, H,...,gap_us,dropped (o t b z w n s + - k)");
  nextUs = micros();
}

void loop() {
  i2sService();  // every pass, before the tick gate: this is the 10 ms deadline
  while (Serial.available()) hapticCommand((char)Serial.read());
  while (bleuart.available()) hapticCommand((char)bleuart.read());
  if (blePktLen && millis() - blePktMs >= 20) bleFlush();
  hapticHousekeeping();

  const uint32_t now = micros();
  if ((int32_t)(now - nextUs) < 0) return;
  if (now - nextUs >= TICK_US) nextUs = now;   // fell a whole tick behind
  nextUs += TICK_US;
  sampleCounter += 2;                          // ms
  const uint32_t N = sampleCounter - lastBeatTime;

  // ---- pulse, every 2 ms ----
  Signal = analogRead(PULSE_PIN);
  if (Signal < thresh && N > (uint32_t)(IBI * 3 / 5)) { if (Signal < T) T = Signal; }
  if (Signal > thresh && Signal > P) P = Signal;

  if (N > 250) {
    if (Signal > thresh && !Pulse && N > (uint32_t)(IBI * 3 / 5)) {
      Pulse = true;
      IBI = sampleCounter - lastBeatTime;
      lastBeatTime = sampleCounter;
      if (secondBeat) { secondBeat = false; for (int i = 0; i < 10; i++) rate[i] = IBI; }
      if (firstBeat)  { firstBeat = false; secondBeat = true; }
      else {
        uint32_t total = 0;
        for (int i = 0; i < 9; i++) { rate[i] = rate[i + 1]; total += rate[i]; }
        rate[9] = IBI; total += IBI; total /= 10;
        BPM = 60000 / total;
        // 40-180 bpm plausibility gate: no-finger flicker noise trips the detector at ~235 bpm
        const bool plausible = BPM >= 40 && BPM <= 180;
        if (plausible) EMITF("B,%lu,%d,%d", (unsigned long)sampleCounter, BPM, IBI);
        if (plausible && beatSync && hmode == H_IDLE) startRun(H_TAP, RES_HZ, SYNC_TAP_MS);
        digitalWrite(LED_GREEN, LOW); lastBlink = millis();
      }
    }
  }
  if (Signal < thresh && Pulse) { Pulse = false; amp = P - T; thresh = amp / 2 + T; P = thresh; T = thresh; }
  if (N > 2500) { thresh = IDLE + 20; P = IDLE + 20; T = IDLE + 20; lastBeatTime = sampleCounter; firstBeat = true; secondBeat = false; BPM = 0; IBI = 600; }
  if (lastBlink && millis() - lastBlink > 60) { digitalWrite(LED_GREEN, HIGH); lastBlink = 0; }

  // ---- FSR + waveforms, every 5th tick (100 Hz) ----
  if (++tick >= 5) {
    tick = 0;
    const int fsr = analogRead(FSR_PIN);
    if (lastSampleUs) { const uint32_t gap = now - lastSampleUs; if (gap > maxGapUs) maxGapUs = gap; }
    lastSampleUs = now;
    seq++;
    // ---- one line per tick: S,<ms>,<fsr>,<pulse>,<thresh> ----
    if (Serial || bleReady()) EMITF("S,%lu,%d,%d,%d", (unsigned long)sampleCounter, fsr, Signal, thresh);
    // ---- link RSSI, every 2nd tick (50 Hz): R,<ms>,<rssi_dbm> ----
    if ((seq & 1) == 0 && rssiConn != BLE_CONN_HANDLE_INVALID) {
      BLEConnection *c = Bluefruit.Connection(rssiConn);
      if (c && c->connected()) EMITF("R,%lu,%d", (unsigned long)sampleCounter, (int)c->getRssi());
    }
  }
}

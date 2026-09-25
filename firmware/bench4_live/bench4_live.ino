/*
 * bench4_live -- bench3_live (FSR on A0 + Pulse Sensor Amped on A1) PLUS the
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
#include <PDM.h>          // built-in Sense mic (PDM_DIN 21 / CLK 20 / PWR 19)

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
static char     blePkt[244];          // ATT payload ceiling at MTU 247 (247 - 3)
static uint16_t blePayload = 0;       // negotiated usable payload, filled after connect
static uint32_t bleNegoMs = 0;
static uint16_t bleMaxPayload();      // defined with the connect callback below
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
  if (blePktLen + n + 1 > bleMaxPayload()) bleFlush();
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
// Ask for everything that raises throughput and lowers latency. Each request can
// be refused by the central; the link just stays on the previous setting, so these
// are safe to attempt unconditionally. Results are reported once negotiation
// settles (see bleNegotiationReport) rather than assumed.
static void bleConnect(uint16_t h) {
  rssiConn = h;
  blePayload = 0;
  BLEConnection *c = Bluefruit.Connection(h);
  if (!c) return;
  c->monitorRssi(0);
  c->requestPHY(BLE_GAP_PHY_2MBPS);      // 2 Mbit symbol rate instead of 1
  c->requestDataLengthUpdate();          // 251-byte link-layer PDU
  c->requestConnectionParameter(12, 0, 600);   // 15 ms, no slave latency, 6 s timeout
  bleNegoMs = millis() + 2500;           // report once the central has answered
}

// Usable ATT payload for one notification. Recomputed after negotiation instead
// of assuming 247 -- a central that refuses the MTU exchange leaves it at 23.
static uint16_t bleMaxPayload() {
  if (blePayload) return blePayload;
  BLEConnection *c = Bluefruit.Connection(rssiConn);
  uint16_t m = c ? c->getMtu() : 23;
  if (m < 23)  m = 23;
  if (m > 247) m = 247;          // never trust a reported MTU above what we configured
  uint16_t pl = m - 3;
  if (pl > sizeof blePkt) pl = sizeof blePkt;
  blePayload = pl;
  return pl;
}

static void bleNegotiationReport() {
  if (!bleNegoMs || (int32_t)(millis() - bleNegoMs) < 0) return;
  bleNegoMs = 0;
  BLEConnection *c = Bluefruit.Connection(rssiConn);
  if (!c) return;
  blePayload = 0;
  // conn interval is in 1.25 ms units
  const uint8_t phy = c->getPHY();
  EMITF("I,ble mtu %d payload %d interval %d.%02d ms latency %d phy %s dle %d",
        (int)c->getMtu(), (int)bleMaxPayload(),
        (int)(c->getConnectionInterval() * 125 / 100),
        (int)((c->getConnectionInterval() * 125) % 100),
        (int)c->getSlaveLatency(),
        phy == 2 ? "2M" : (phy == 1 ? "1M" : (phy == 4 ? "coded" : "?")),
        (int)c->getDataLength());
}
static void bleDisconnect(uint16_t h, uint8_t r) { (void)h; (void)r; rssiConn = BLE_CONN_HANDLE_INVALID; }
static void bleSetup() {
  // = BANDWIDTH_MAX (247, 100, 3, 1) but with the HVN notify queue at 6.
  // begin() fails if the SoftDevice needs more SRAM than the linker reserved,
  // so the return value is checked rather than assumed.
  Bluefruit.configPrphConn(247, 100, 6, 1);
  const bool bleOk = Bluefruit.begin();
  Bluefruit.setName("Amulet-XPL");
  Bluefruit.setTxPower(4);
  // Apple's BLE accessory guidelines require Interval Min >= 15 ms and
  // Max >= Min + 15 ms. Asking for 7.5-15 ms violates that, so macOS ignored it
  // and imposed its own 30 ms. 12-24 units = 15-30 ms is the compliant request
  // that actually gets us the 15 ms floor.
  Bluefruit.Periph.setConnInterval(12, 24);       // 15-30 ms, Apple-compliant
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
  if (!bleOk) emit("I,BLE begin() FAILED -- SoftDevice SRAM too small for this config");
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


// ============================== MIC (PDM) ===================================
// Built-in MSM261D3526H1CPM on the Sense module. PDM is a separate peripheral
// from I2S with its own EasyDMA, so the amp and the mic run at the same time.
// PDM.cpp asks the SoftDevice for HFCLK when it is enabled, so starting the mic
// after Bluefruit.begin() is safe -- it does NOT repeat the 16 Sep hard fault.
// The mic is OPT-IN ('m'): a boot-time mic fault would cost another double-tap.
#define MIC_HZ         8000     // speech band; doubles the seconds per KB vs 16 k
#define MIC_BUF_BYTES  1024                    // 512 samples per block = 32 ms
#define REC_SECONDS    8        // one take. Press again for another; the host joins them.
#define REC_SAMPLES    (MIC_HZ * REC_SECONDS)  // 64000 samples = 128 KB, leaves ~84 KB headroom

static int16_t       micBlk[MIC_BUF_BYTES / 2];
static volatile int  micCount = 0;
static bool          micOn    = false;
static int           micGain  = 80;   // 0x50 = +20 dB, hardware maximum

static int16_t       recBuf[REC_SAMPLES];
static uint32_t      recPos    = 0;
static bool          recording = false;

static bool          dumping = false;
static uint32_t      dumpPos = 0, dumpSeq = 0;

static uint32_t      micRms = 0, micPeak = 0, micLastEmit = 0, micStartMs = 0;
#define MIC_SETTLE_MS 60   // datasheet: 20 ms power-up, 5 ms filter delay, ~50 bad samples
static bool          latArmed = false;
static uint32_t      latT0 = 0, latFloor = 0;

static void onPDMdata() {
  int n = PDM.available();
  if (n <= 0) return;
  if (n > (int)sizeof micBlk) n = (int)sizeof micBlk;
  PDM.read(micBlk, n);
  micCount = n / 2;
}

// GAIN ORDERING BUG (verified in the core on disk): PDM.cpp:159, inside begin(),
// calls setGain(DEFAULT_PDM_GAIN) with DEFAULT_PDM_GAIN = 20. On the nRF52840
// GAINL register 0x28 (40) is 0 dB in 0.5 dB steps, so 20 is -10 dB. Any setGain()
// made BEFORE begin() is silently discarded -- which is why every clip came back
// at ~1% of full scale. setGain() must come AFTER begin().
static void micStart() {
  if (micOn) return;
  PDM.onReceive(onPDMdata);
  PDM.setBufferSize(MIC_BUF_BYTES);
  if (!PDM.begin(1, MIC_HZ)) { emit("I,mic FAILED to start"); return; }
  PDM.setGain(micGain);               // AFTER begin(), or it does nothing
  micOn = true;
  micStartMs = millis();
  EMITF("I,mic on, %d Hz, gain %d (%+d dB)", MIC_HZ, micGain, (micGain - 40) / 2);
}

static void micStop() {
  if (!micOn) return;
  PDM.end(); micOn = false; micCount = 0; recording = false;
  emit("I,mic off");
}

static void micService() {
  if (!micOn) return;
  const int n = micCount;
  if (n <= 0) return;
  micCount = 0;
  if (millis() - micStartMs < MIC_SETTLE_MS) return;   // drop the startup transient

  uint64_t acc = 0; uint32_t pk = 0;
  for (int i = 0; i < n; i++) {
    const int32_t v = micBlk[i];
    acc += (uint64_t)((int64_t)v * v);
    const uint32_t a = (uint32_t)(v < 0 ? -v : v);
    if (a > pk) pk = a;
  }
  micRms  = (uint32_t)sqrtf((float)(acc / (uint32_t)n));
  micPeak = pk;

  if (recording) {
    const uint32_t room = REC_SAMPLES - recPos;
    const uint32_t take = (uint32_t)n < room ? (uint32_t)n : room;
    memcpy(&recBuf[recPos], micBlk, take * 2);
    recPos += take;
    if (recPos >= REC_SAMPLES) {
      recording = false;
      EMITF("I,rec done: %lu samples @%d Hz -- send d to download", (unsigned long)recPos, MIC_HZ);
    }
  }

  // acoustic loopback: 'l' fires a tap and arms this; first peak over the floor wins
  if (latArmed && micPeak > latFloor) {
    latArmed = false;
    EMITF("I,latency %lu us  (peak %lu over floor %lu)",
          (unsigned long)(micros() - latT0), (unsigned long)micPeak, (unsigned long)latFloor);
  }

  const uint32_t nowMs = millis();
  if (nowMs - micLastEmit >= 20 && !dumping) {  // 50 Hz level meter, paused during a dump
    micLastEmit = nowMs;
    EMITF("M,%lu,%lu,%lu", (unsigned long)sampleCounter, (unsigned long)micRms, (unsigned long)micPeak);
  }
}

// ---- recording download: base64, flow-controlled so nothing is ever dropped ----
static const char B64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
#define DUMP_BYTES 171   // divisible by 3 (no mid-stream padding) and 228 base64 chars
                         // + prefix fits inside one 244-byte BLE notification

static void dumpService() {
  if (!dumping) return;
  const bool viaSerial = (bool)Serial;
  if (!viaSerial && !bleReady()) { dumping = false; emit("I,dump aborted: no link"); return; }

  // Serial can absorb several chunks per pass; the radio gets fewer, because each
  // notification has to wait for a connection event. Either way this is bounded so
  // loop() keeps its 10 ms deadline.
  int guard = viaSerial ? 6 : 3;
  while (dumping && guard-- > 0) {
    if (viaSerial) {
      if (Serial.availableForWrite() < 260) return;      // let USB drain; never drop audio
    } else {
      if (blePktLen) bleFlush();                          // clear pending telemetry first
    }

    const uint8_t *src = (const uint8_t *)recBuf;
    const uint32_t total = recPos * 2;
    const uint32_t left  = total - dumpPos;
    const uint32_t take  = left < DUMP_BYTES ? left : DUMP_BYTES;
    char out[256];
    int p = snprintf(out, sizeof out, "A,%lu,", (unsigned long)dumpSeq);
    for (uint32_t i = 0; i < take; i += 3) {
      const uint32_t r  = take - i;
      const uint8_t  b0 = src[dumpPos + i];
      const uint8_t  b1 = r > 1 ? src[dumpPos + i + 1] : 0;
      const uint8_t  b2 = r > 2 ? src[dumpPos + i + 2] : 0;
      out[p++] = B64[b0 >> 2];
      out[p++] = B64[((b0 & 3) << 4) | (b1 >> 4)];
      out[p++] = r > 1 ? B64[((b1 & 15) << 2) | (b2 >> 6)] : '=';
      out[p++] = r > 2 ? B64[b2 & 63] : '=';
    }
    out[p++] = '\n';

    if (viaSerial) {
      Serial.write((const uint8_t *)out, p);
    } else {
      // One write == one notification. A short return means the radio queue is
      // full: leave dumpPos alone and try the same chunk again next pass.
      if (bleuart.write((const uint8_t *)out, p) != p) return;
    }
    dumpPos += take; dumpSeq++;
    if (dumpPos >= total) {
      dumping = false;
      EMITF("AEND,%lu,%d", (unsigned long)total, MIC_HZ);
    }
  }
}


// ============================= SESSION MACHINE ==============================
// The study protocol is hands-off: the wearer lies down, the board calibrates, then
// holds them at the hypnagogic threshold with small cues for the session duration.
// They touch nothing until it is over.
//
//   IDLE --press--> CALIB (2 min) --> RUN (60 min, cues fire) --> wake cue
//        --> REPORT (each press records a take) --long press--> DONE
//
// The timer lives here rather than on the laptop on purpose: if Bluetooth drops
// mid-session the wearer must still be woken and still be able to record.
enum SState : uint8_t { SESS_IDLE = 0, SESS_CALIB, SESS_RUN, SESS_REPORT, SESS_DONE };
static const char *SESS_NAME[] = { "idle", "calib", "run", "report", "done" };

#define SESS_CALIB_S    120UL
#define SESS_RUN_S      3600UL      // 60 min; "45x" over the link runs 45 instead
#define SESS_WAKE_MS    2000        // the wake cue is a tone, not a nudge: unmistakable

static SState   sessState   = SESS_IDLE;
static uint32_t sessT0      = 0;    // millis() at state entry
static uint32_t sessRunS    = SESS_RUN_S;
static uint32_t sessTakes   = 0;
static uint32_t sessLastG   = 0;
static uint32_t pendingMinutes = 0;   // digits typed before 'x' set the duration

static void sessEmit() {
  const uint32_t el = (millis() - sessT0) / 1000;
  uint32_t left = 0;
  if (sessState == SESS_CALIB) left = el < SESS_CALIB_S ? SESS_CALIB_S - el : 0;
  else if (sessState == SESS_RUN) left = el < sessRunS ? sessRunS - el : 0;
  EMITF("G,%lu,%s,%lu,%lu,%lu", (unsigned long)sampleCounter, SESS_NAME[sessState],
        (unsigned long)el, (unsigned long)left, (unsigned long)sessTakes);
}

static void sessGo(SState st) {
  sessState = st; sessT0 = millis();
  sessEmit();
}

static void sessStart(uint32_t minutes) {
  if (sessState != SESS_IDLE && sessState != SESS_DONE) { emit("I,session already running"); return; }
  sessRunS = minutes ? minutes * 60UL : SESS_RUN_S;
  sessTakes = 0;
  if (!micOn) micStart();                  // the level meter doubles as a mic-health check
  EMITF("I,session start: %lu s calibration then %lu min", (unsigned long)SESS_CALIB_S,
        (unsigned long)(sessRunS / 60));
  sessGo(SESS_CALIB);
}

static void sessAbort(const char *why) {
  if (sessState == SESS_IDLE) return;
  EMITF("I,session aborted: %s", why);
  sessGo(SESS_IDLE);
}

static void sessService() {
  const uint32_t now = millis();
  const uint32_t el = (now - sessT0) / 1000;
  switch (sessState) {
    case SESS_CALIB:  if (el >= SESS_CALIB_S) { emit("I,calibration done -- watching"); sessGo(SESS_RUN); } break;
    case SESS_RUN:
      if (el >= sessRunS) {
        emit("I,session over -- wake cue");
        if (hmode == H_IDLE) startRun(H_TONE, RES_HZ, SESS_WAKE_MS);
        sessGo(SESS_REPORT);
        emit("I,press the button to record a take, hold it to finish");
      }
      break;
    default: break;
  }
  if (now - sessLastG >= 1000) { sessLastG = now; if (sessState != SESS_IDLE) sessEmit(); }
}


// ============================== BUTTON (D2) =================================
// 6 mm tactile switch, one leg to the GND rail, the other to D2 (soldered-harness
// joints 11-12). No external resistor: internal pull-up, so pressed reads LOW.
// Edge-triggered on its own line type so existing S-line parsers are untouched.
#define BUTTON_PIN   D2
#define BTN_DEBOUNCE 25

#define BTN_LONG_MS   1200    // finish the report
#define BTN_OFF_MS    3000    // power down: there is no slide switch on this build

static bool     btnUp      = true;     // pull-up idle = HIGH = released
static uint32_t btnEdgeMs  = 0;
static uint32_t btnDownMs  = 0;
static uint32_t btnPresses = 0;
static bool     btnHandled = false;    // a long press already acted; ignore the release

// The battery has no switch, so the button is the power control. A 3 s hold parks the
// chip in System OFF (~2 uA) with the button itself configured as the wake source, so
// the next press boots it. Only from idle or done -- never mid-session.
static void powerOff() {
  emit("I,powering down -- press the button to wake");
  delay(120);                                  // let the line drain over USB/BLE
  i2sStop();
  if (micOn) micStop();
  digitalWrite(LED_GREEN, HIGH);
  // wake on the button going low; g_ADigitalPinMap turns the Arduino pin into a port pin
  nrf_gpio_cfg_sense_input((uint32_t)g_ADigitalPinMap[BUTTON_PIN],
                           NRF_GPIO_PIN_PULLUP, NRF_GPIO_PIN_SENSE_LOW);
  uint8_t sdOn = 0; sd_softdevice_is_enabled(&sdOn);
  if (sdOn) sd_power_system_off(); else NRF_POWER->SYSTEMOFF = 1;
  while (true) { __WFE(); }                    // not reached
}

static void buttonService() {
  const bool up = digitalRead(BUTTON_PIN);
  const uint32_t now = millis();

  // held: act on the long thresholds without waiting for the release
  if (!btnUp && !btnHandled) {
    const uint32_t held = now - btnDownMs;
    if (held >= BTN_OFF_MS && (sessState == SESS_IDLE || sessState == SESS_DONE)) {
      btnHandled = true; powerOff();
    } else if (held >= BTN_LONG_MS) {
      btnHandled = true;
      if (sessState == SESS_REPORT) {
        EMITF("I,report finished: %lu take(s)", (unsigned long)sessTakes);
        sessGo(SESS_DONE);
      } else if (sessState == SESS_CALIB || sessState == SESS_RUN) {
        sessAbort("button held");
      }
    }
  }

  if (up == btnUp || (now - btnEdgeMs) <= BTN_DEBOUNCE) return;
  btnUp = up; btnEdgeMs = now;

  if (!up) {                                   // ---- pressed
    btnDownMs = now; btnHandled = false; btnPresses++;
    EMITF("K,%lu,1,%lu", (unsigned long)sampleCounter, (unsigned long)btnPresses);
    return;
  }

  // ---- released
  EMITF("K,%lu,0,%lu", (unsigned long)sampleCounter, (unsigned long)btnPresses);
  if (btnHandled) return;                      // the hold already did the work
  switch (sessState) {
    case SESS_IDLE:
    case SESS_DONE:
      sessStart(0);                            // a press starts a session: no laptop needed
      break;
    case SESS_REPORT:
      if (recording || dumping) { emit("I,busy -- wait for the take to finish"); break; }
      if (!micOn) micStart();
      recPos = 0; recording = true; sessTakes++;
      EMITF("I,take %lu recording %d s ...", (unsigned long)sessTakes, REC_SECONDS);
      break;
    default:
      break;                                   // mid-session presses are logged, not acted on
  }
}


// ======================= PPG FILTER + BEAT DETECTION ========================
// The bench3 detector fed RAW ADC into an amplitude threshold with no filtering.
// Measured 23 Sep: 39% of the signal's energy sits below 0.5 Hz (baseline drift)
// and only 14% in the cardiac band, so the threshold chased the drift and the
// BPM swung 45-163 at rest. Fixed here with a 0.5-5 Hz band, an envelope-tracked
// threshold, a refractory period, a MEDIAN (not mean) of intervals, and -- most
// importantly -- a signal-quality gate that reports nothing when there is no pulse
// to find, instead of inventing one.
#define PPG_FS        500.0f
#define HP_A          0.99375f   // one-pole high-pass, ~0.5 Hz  (kills drift)
#define LP_B          0.0592f    // one-pole low-pass,  ~5 Hz    (kills hash)
#define ENV_RISE      0.25f
#define ENV_DECAY     0.0008f    // envelope falls back over ~2.5 s
#define BEAT_FRAC     0.68f      // fire at 68%: above the dicrotic notch, which sits ~40-60%
#define REFRACTORY_MS 300        // 200 bpm ceiling
#define MIN_AMP       40.0f      // filtered counts. Was 8, which let a 13-count signal
                                 // through and produced confident nonsense (126 bpm, 11/47
                                 // intervals halving or doubling). A real finger-on signal
                                 // measures 100-400 here; 40 is the honest floor.

static float    hpIn = 0, hpOut = 0, lpOut = 0;
static float    envMax = 0, envMin = 0;
static bool     ppgAbove = false;
static uint32_t lastBeatMs2 = 0;
#define IBI_N 9                  // display median. bench3 used mean-of-10; median-of-5 was
                                 // too twitchy against normal respiratory variation.
static uint16_t ibiRing[IBI_N]; static uint8_t ibiPos = 0, ibiFill = 0;
static float    ppgFilt = 0, ppgAmp = 0, ppgThr = 0;
static bool     ppgUsable = false;

static uint16_t medianIbi() {
  uint16_t t[IBI_N]; uint8_t n = ibiFill;
  for (uint8_t i = 0; i < n; i++) t[i] = ibiRing[i];
  for (uint8_t i = 1; i < n; i++) { uint16_t k = t[i]; int8_t j = i - 1;
    while (j >= 0 && t[j] > k) { t[j+1] = t[j]; j--; } t[j+1] = k; }
  return n ? t[n/2] : 0;
}

// called once per 2 ms tick with the raw ADC reading
static void ppgService(int raw, uint32_t nowMs) {
  const float in = (float)raw;
  const float hp = HP_A * (hpOut + in - hpIn);
  hpIn = in; hpOut = hp;
  lpOut += LP_B * (hp - lpOut);
  const float v = lpOut;
  ppgFilt = v;

  envMax += (v - envMax) * (v > envMax ? ENV_RISE : ENV_DECAY);
  envMin += (v - envMin) * (v < envMin ? ENV_RISE : ENV_DECAY);
  ppgAmp = envMax - envMin;
  ppgThr = envMin + BEAT_FRAC * ppgAmp;
  ppgUsable = (ppgAmp >= MIN_AMP);

  if (!ppgUsable) { ppgAbove = false; return; }   // no pulse worth detecting

  if (!ppgAbove && v > ppgThr) {
    ppgAbove = true;
    // Adaptive refractory. A fixed 300 ms lets the dicrotic notch through as a
    // second beat, which showed up as runs of ~150 bpm against a true 72 (exactly
    // double). Gate on 55% of the running median interval instead.
    uint32_t refr = REFRACTORY_MS;
    if (ibiFill >= 3) { const uint32_t a = (uint32_t)medianIbi() * 55 / 100;
                        if (a > refr) refr = a > 900 ? 900 : a; }
    if (lastBeatMs2 && (nowMs - lastBeatMs2) >= refr) {
      const uint32_t ibi = nowMs - lastBeatMs2;
      if (ibi >= 333 && ibi <= 1500) {            // 40-180 bpm
        ibiRing[ibiPos] = (uint16_t)ibi;
        ibiPos = (ibiPos + 1) % IBI_N;
        if (ibiFill < IBI_N) ibiFill++;
        const uint16_t m = medianIbi();
        if (m) EMITF("B,%lu,%d,%d", (unsigned long)sampleCounter, (int)(60000 / m), (int)ibi);
        digitalWrite(LED_GREEN, LOW); lastBlink = millis();
      }
      lastBeatMs2 = nowMs;
    } else if (!lastBeatMs2) {
      lastBeatMs2 = nowMs;
    }
  } else if (ppgAbove && v < envMin + 0.30f * ppgAmp) {
    ppgAbove = false;
  }
}


// ============================ BATTERY (BQ25100) =============================
// Charging itself is AUTOMATIC IN HARDWARE: the BQ25100 starts as soon as VBUS
// appears. Firmware cannot switch it on. What firmware CAN do is pick the rate
// via HICHG, and read the charger's status line.
//   D14 -> P0.14 READ_BAT  drive LOW to enable the voltage divider
//   D32 -> P0.31 VBAT      analog input, behind a 1M/510k divider
//   D22 -> P0.13 HICHG     LOW = 100 mA fast, HIGH = 50 mA standard
//   D23 -> P0.17 ~CHG      LOW while charging, HIGH when done or no source
// AR_DEFAULT is 0.6 V x 6 = 3.6 V full scale, analogReadResolution(10) -> 0..1023.
#define BAT_EN_PIN     VBAT_ENABLE
#define BAT_ADC_PIN    PIN_VBAT
#define BAT_HICHG_PIN  PIN_CHARGING_CURRENT
#define BAT_CHG_PIN    23
#define BAT_FS_V       1.8f      // AR_INTERNAL_1_8 for the battery read only
#define BAT_DIV        2.960784f        // (1M + 510k)/510k, from the Seeed schematic
#define BAT_PERIOD_MS  500

static float    batV = 0.0f, batIdleV = 0.0f, batMinV = 9.9f;
static int      batPct = 0;
static bool     batCharging = false, batFast = true, batPresent = false;
static uint32_t batLastMs = 0;
static int      batSagMv = 0;

// Resting LiPo discharge curve. Voltage-based SoC is only honest at rest:
// under load it reads low, while charging it reads high. Flagged in the line.
static int batSoC(float v) {
  static const float V[] = {3.00f,3.45f,3.68f,3.74f,3.77f,3.79f,3.82f,3.87f,3.93f,4.00f,4.10f,4.20f};
  static const int   P[] = {    0,    5,   10,   20,   30,   40,   50,   60,   70,   80,   90,  100};
  if (v <= V[0])  return 0;
  if (v >= V[11]) return 100;
  for (int i = 0; i < 11; i++)
    if (v < V[i+1]) return P[i] + (int)((v - V[i]) / (V[i+1] - V[i]) * (P[i+1] - P[i]));
  return 100;
}

// ISET network on the XIAO: R18 2.7k to GND, R12 2.7k to P0.13. TI bq25101,
// R_ISET = K_ISET/I_OUT with K_ISET = 135. High-Z -> 2.7k -> 50 mA.
// Driven LOW -> 2.7k||2.7k = 1.35k -> 100 mA. Driving it HIGH is NOT a documented
// mode -- it sources current into the ISET node. Seeed's own BSP uses INPUT for
// 50 mA (variants/.../variant.cpp initVariant), and so do we.
static void batSetFast(bool on) {
  batFast = on;
  if (on) { pinMode(BAT_HICHG_PIN, OUTPUT); digitalWrite(BAT_HICHG_PIN, LOW); }
  else    { pinMode(BAT_HICHG_PIN, INPUT); }
  EMITF("I,charge current %s", on ? "100 mA (0.4C on 250 mAh -- in band)"
                                  : "50 mA (0.2C on 250 mAh -- slow but safe)");
}

static void batteryInit() {
  pinMode(BAT_EN_PIN, OUTPUT);
  digitalWrite(BAT_EN_PIN, LOW);          // enable the divider (~2.4 uA, leave on)
  pinMode(BAT_CHG_PIN, INPUT_PULLUP);
  // Cell is 250 mAh (Erik, 24 Sep -- supersedes the ~70 mAh in the 16 Sep handoff,
  // which pre-dates the cell upgrade that same document recommended). 100 mA is
  // 0.4C, inside the 0.5-1C band, and charges twice as fast. 'c' drops to 50 mA.
  batSetFast(true);
}

static void batteryService() {
  const uint32_t now = millis();
  if (now - batLastMs < BAT_PERIOD_MS) return;
  batLastMs = now;

  // 10-bit on a divided cell voltage gives 10.4 mV per count -- and 1 count is
  // 2.2% of state of charge, so the percentage flickered on quantisation alone
  // (measured 7 counts of noise = 73 mV of phantom swing). Read the battery at
  // 12-bit with 32x averaging, then hand the ADC straight back to the sensors.
  // Source impedance of the divider is 1M||510k = 337.7 kOhm. The nRF52840 PS
  // requires TACQ >= 20 us above 200 kOhm; the core default of 3 us is rated for
  // 10 kOhm, so a stock analogRead here runs ~34x outside spec and reads low.
  analogReference(AR_INTERNAL_1_8);              // 4.2 V -> 1.418 V = 79% of range
  analogReadResolution(12);
  analogSampleTime(20);
  (void)analogRead(BAT_ADC_PIN);                 // discard first conversion
  uint32_t acc = 0;
  for (uint8_t i = 0; i < 32; i++) acc += (uint32_t)analogRead(BAT_ADC_PIN);
  analogReference(AR_DEFAULT);                   // hand the ADC back to the sensors
  analogReadResolution(10);
  analogSampleTime(3);
  const float raw12 = acc / 32.0f;
  const int   raw   = (int)(raw12 / 4.0f);       // report on the old 10-bit scale
  const float v = (raw12 / 4095.0f) * BAT_FS_V * BAT_DIV;

  batV = batV > 0.1f ? batV + (v - batV) * 0.10f : v;   // ~5 s time constant
  batPresent = (batV > 2.5f);
  const int pctNow = batPresent ? batSoC(batV) : 0;
  // hysteresis: a battery gauge that twitches is worse than one that lags
  if (batPct == 0 || pctNow >= batPct + 2 || pctNow <= batPct - 2) batPct = pctNow;
  batCharging = (digitalRead(BAT_CHG_PIN) == LOW);
  if (batV < batMinV) batMinV = batV;

  // V,<ms>,<mV>,<pct>,<charging>,<fast>,<raw>,<sag_mV>
  EMITF("V,%lu,%d,%d,%d,%d,%d,%d", (unsigned long)sampleCounter,
        (int)(batV * 1000.0f), batPct, batCharging ? 1 : 0, batFast ? 1 : 0, raw, batSagMv);
}

// Cell health: a known load (the LRA) should sag a healthy pack only slightly.
// Rising sag over weeks = rising internal resistance = the pack is aging.
static void batMarkLoadStart() { batIdleV = batV; batMinV = batV; }
static void batMarkLoadEnd()   { if (batIdleV > 2.5f) batSagMv = (int)((batIdleV - batMinV) * 1000.0f); }

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
    case 'm': if (micOn) micStop(); else micStart(); break;
    case 'r': if (!micOn) micStart();
              if (micOn) { recPos = 0; recording = true; EMITF("I,recording %d s ...", REC_SECONDS); }
              break;
    case 'd': if (recPos == 0)      emit("I,nothing recorded -- send r first");
              else if (dumping)     emit("I,already dumping");
              else { dumping = true; dumpPos = 0; dumpSeq = 0;
                     EMITF("I,dump %lu bytes @%d Hz", (unsigned long)(recPos * 2), MIC_HZ); }
              break;
    case 'g': micGain += 20; if (micGain > 80) micGain = 0;
              if (micOn) { micStop(); micStart(); } else EMITF("I,mic gain %d", micGain);
              break;
    case 'l': if (!micOn) micStart();
              if (micOn && !busy) { latFloor = micPeak * 3 + 400; latT0 = micros(); latArmed = true;
                                    startRun(H_TAP, RES_HZ, TAP_MS); emit("I,latency probe: tap fired"); }
              else emit("I,busy");
              break;
    case 'c': batSetFast(!batFast); break;
    case 'x': sessStart(pendingMinutes); pendingMinutes = 0; break;   // "45x" = 45 min, "x" = default
    case 'y': sessAbort("host"); break;
    case '0': case '1': case '2': case '3': case '4':
    case '5': case '6': case '7': case '8': case '9':
      pendingMinutes = pendingMinutes * 10 + (uint32_t)(c - '0');
      if (pendingMinutes > 600) pendingMinutes = 600;
      break;
    case 'h': hapticStatus();
              EMITF("K,%lu,%d,%lu", (unsigned long)sampleCounter,
                    digitalRead(BUTTON_PIN) ? 0 : 1, (unsigned long)btnPresses);
              break;
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
  pinMode(BUTTON_PIN, INPUT_PULLUP);   // pressed = LOW
  batteryInit();

  // Crystal HFCLK so the I2S rate is exact (TinyUSB may drop it on VBUS removal).
  // CLOCK is a SoftDevice-owned peripheral once BLE is up: a direct register write hard-faults
  // and the board reset-loops. Ask the SoftDevice for the crystal instead.
  { uint8_t sdOn = 0; sd_softdevice_is_enabled(&sdOn);
    if (sdOn) sd_clock_hfclk_request();
    else { NRF_CLOCK->EVENTS_HFCLKSTARTED = 0; NRF_CLOCK->TASKS_HFCLKSTART = 1; } }
  { uint8_t sdOn2 = 0; sd_softdevice_is_enabled(&sdOn2);
    if (sdOn2) sd_power_dcdc_mode_set(NRF_POWER_DCDC_ENABLE);
    else NRF_POWER->DCDCEN = 1; }
  i2sInit();  // configured but NOT started: amp stays shut down until asked

  emit("I,bench4_live started (USB + BLE Amulet-XPL) -- S,ms,fsr,pulse,thr @100Hz, B beats, R,ms,rssi @50Hz, M,ms,rms,peak @50Hz, V,ms,mV,pct,chg,fast @2Hz, K,ms,down,count on press, G,ms,state,elapsed,left,takes @1Hz | session: x start (NNx = NN min), y abort; button: press=start, in report press=take, hold 1.2s=finish, hold 3s=power off | haptics o t b z w n s + - k | mic m r d g l");
  nextUs = micros();
}

void loop() {
  i2sService();  // every pass, before the tick gate: this is the 10 ms deadline
  micService();
  dumpService();
  buttonService();
  sessService();
  batteryService();
  while (Serial.available()) hapticCommand((char)Serial.read());
  while (bleuart.available()) hapticCommand((char)bleuart.read());
  if (blePktLen && millis() - blePktMs >= 12) bleFlush();   // ~ one connection interval
  bleNegotiationReport();
  hapticHousekeeping();

  const uint32_t now = micros();
  if ((int32_t)(now - nextUs) < 0) return;
  if (now - nextUs >= TICK_US) nextUs = now;   // fell a whole tick behind
  nextUs += TICK_US;
  sampleCounter += 2;                          // ms
  const uint32_t N = sampleCounter - lastBeatTime;

  // ---- pulse, every 2 ms ----
  Signal = analogRead(PULSE_PIN);
  ppgService(Signal, millis());
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
        // legacy detector retained for its threshold trace only; ppgService() emits B now
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
    if ((Serial || bleReady()) && !dumping) {
      EMITF("S,%lu,%d,%d,%d", (unsigned long)sampleCounter, fsr, Signal, thresh);
      EMITF("F,%lu,%d,%d,%d,%d", (unsigned long)sampleCounter,
            (int)(ppgFilt * 10.0f), (int)(ppgThr * 10.0f),
            (int)(ppgAmp * 10.0f), ppgUsable ? 1 : 0);
    }
    // ---- link RSSI, every 2nd tick (50 Hz): R,<ms>,<rssi_dbm> ----
    if ((seq & 1) == 0 && rssiConn != BLE_CONN_HANDLE_INVALID) {
      BLEConnection *c = Bluefruit.Connection(rssiConn);
      if (c && c->connected()) EMITF("R,%lu,%d", (unsigned long)sampleCounter, (int)c->getRssi());
    }
  }
}

// =============================================================================
// lockin_esp32_test.ino -- talk to tt_um_manyetox_lockin (DE0-Nano or the chip)
// Arduino IDE, board: any ESP32 ("ESP32 Dev Module" for a classic DevKit).
// Serial monitor: 115200 baud.
//
// LOOPBACK TEST (no optics needed): the chip measures its own modulation output.
//   MOD    (JP2-15) --20k--+--10k-- ADC_FB (JP2-14)
//                          |
//                          +------- ADC_CMP (JP2-6)
//                          |
//                        10 nF
//                          |
//                         GND (JP2-12)
//   Expected: ID 0x4C, step ~0.50 (= 10k/20k), phase within a degree or so of 0.
//   (Model: step 0.499, phase +0.1..0.3 deg at 50 MHz and 10 MHz.)
//   Remove the 20k from MOD -> step drops to ~0: the lock-in only sees MOD.
//
// ESP32 <-> DE0-Nano header JP2 (3.3 V both sides, share GND):
//   PIN_SCK  -> JP2-2  (ui_in[0])     PIN_MISO <- JP2-13 (uo_out[0])
//   PIN_MOSI -> JP2-4  (ui_in[1])     PIN_DONE <- JP2-17 (uo_out[4])
//   PIN_CS   -> JP2-5  (ui_in[2])     GND      -- JP2-12
// =============================================================================
#include <SPI.h>

// ---- pins (classic ESP32 DevKit VSPI defaults; change for other boards) ----
const int PIN_SCK  = 18;
const int PIN_MISO = 19;
const int PIN_MOSI = 23;
const int PIN_CS   = 5;
const int PIN_DONE = 4;

// ---- measurement settings ---------------------------------------------------
const double   F_CLK = 50e6;     // 50e6 if the FPGA uses USE_PLL_10MHZ 0, 10e6 if 1
const uint32_t TW    = 1024;     // POWER OF TWO. f_mod = TW*F_CLK/2^24 = 3052 Hz at 50 MHz
const uint16_t M     = 153;      // whole periods: 153 * 2^24/TW / F_CLK = 50.1 ms at 50 MHz
                                 // (10 MHz: use TW = 4096, M = 122 -> 2441 Hz, 50.0 ms)
const double   VDD        = 3.3;
const double   R1_OVER_R2 = 2.0; // loopback 20k/10k. With the TIA and 10k/10k: 1.0

// ---- register map (model/regmap.py) -----------------------------------------
const uint8_t ADDR_ID = 0x00, ADDR_CTRL = 0x01, ADDR_STATUS = 0x02, ADDR_M = 0x04;
const uint8_t ADDR_TW = 0x10, ADDR_I = 0x14, ADDR_Q = 0x18;
const uint8_t CTRL_RUN = 0x01, CTRL_START = 0x02;
const uint8_t STATUS_DONE = 0x01, STATUS_BUSY = 0x02;

SPISettings spiCfg(1000000, MSBFIRST, SPI_MODE0);   // 1 MHz <= f_clk/8 at both clocks

// One 16-bit frame: R/W, 7-bit address, 8 data bits. Returns the 8 bits read back.
uint8_t xfer(bool read, uint8_t addr, uint8_t data) {
  uint16_t word = (read ? 0x8000 : 0) | ((uint16_t)(addr & 0x7F) << 8) | data;
  SPI.beginTransaction(spiCfg);
  digitalWrite(PIN_CS, LOW);
  uint16_t rx = SPI.transfer16(word);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  return rx & 0xFF;
}
void    wr(uint8_t a, uint8_t d) { xfer(false, a, d); }
uint8_t rd(uint8_t a)            { return xfer(true, a, 0); }

void wrLE(uint8_t a, uint32_t v, int n) { for (int k = 0; k < n; k++) wr(a + k, (v >> (8 * k)) & 0xFF); }
uint32_t rdLE(uint8_t a, int n) { uint32_t v = 0; for (int k = 0; k < n; k++) v |= (uint32_t)rd(a + k) << (8 * k); return v; }
int32_t rd24s(uint8_t a) { int32_t v = (int32_t)rdLE(a, 3); if (v & 0x800000) v -= 0x1000000; return v; }

// Delay of a square signal vs MOD, degrees, positive = signal lags (model: square_phase_deg).
// ref_q LEADS ref_i on this chip, so a lagging signal gives negative Q.
double squarePhaseDeg(int32_t i, int32_t q) {
  double s = fabs((double)i) + fabs((double)q);
  if (s == 0) return NAN;
  double x = i / s, y = q / s, frac;
  if (x >= 0)      frac = -y / 4;
  else if (y <= 0) frac = (2 + y) / 4;
  else             frac = (y - 2) / 4;
  return 360.0 * frac;
}

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);
  pinMode(PIN_DONE, INPUT);
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, PIN_CS);

  uint8_t id = rd(ADDR_ID);
  Serial.printf("\nID = 0x%02X (expect 0x4C)%s\n", id, id == 0x4C ? "" : "  <-- check wiring, GND, KEY0, LED7");

  wr(ADDR_CTRL, 0);
  wrLE(ADDR_TW, TW, 3);
  wrLE(ADDR_M, M, 2);
  uint32_t tw_rb = rdLE(ADDR_TW, 3), m_rb = rdLE(ADDR_M, 2);
  Serial.printf("TW = %lu (read back %lu), M = %u (read back %lu)\n",
                (unsigned long)TW, (unsigned long)tw_rb, M, (unsigned long)m_rb);
  Serial.printf("f_mod = %.1f Hz, window = %.1f ms\n",
                TW * F_CLK / 16777216.0, 1e3 * M * (16777216.0 / TW) / F_CLK);

  wr(ADDR_CTRL, CTRL_RUN);   // MOD starts toggling
  delay(50);                 // let the LED/TIA/ADC settle
}

void loop() {
  wr(ADDR_CTRL, CTRL_RUN | CTRL_START);
  uint32_t t0 = millis();
  while (!digitalRead(PIN_DONE)) {
    if (millis() - t0 > 2000) {
      Serial.printf("timeout: STATUS = 0x%02X\n", rd(ADDR_STATUS));
      delay(500);
      return;
    }
  }
  int32_t I = rd24s(ADDR_I), Q = rd24s(ADDR_Q);
  double N    = M * (16777216.0 / TW);                  // samples in the window
  double step = (fabs((double)I) + fabs((double)Q)) / N; // density step, exact for square signals
  double volts = step * VDD * R1_OVER_R2;               // step at the R1 input
  Serial.printf("I=%9ld  Q=%9ld  step=%.4f  -> %.4f V at R1  phase=%+6.2f deg\n",
                (long)I, (long)Q, step, volts, squarePhaseDeg(I, Q));
  delay(200);
}

/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// tt_um_manyetox_lockin -- digital lock-in amplifier, Tiny Tapeout GF180 (1x1 tile)
// =============================================================================
// PINS
//   ui_in[0]   SPI SCLK        (from ESP32)
//   ui_in[1]   SPI MOSI
//   ui_in[2]   SPI CS_N
//   ui_in[3]   ADC_CMP         RC node of the pin sigma-delta ADC
//   ui_in[4]   EXT_REF         stretch goal, unused for now
//   ui_in[7:5] spare           reserved for more channels
//   uo_out[0]  SPI MISO
//   uo_out[1]  ADC_FB          = ~b, drives R2 into the RC node
//   uo_out[2]  MOD             = ref_i AND run (LED/laser modulation; low when stopped)
//   uo_out[3]  REF90           = ref_q AND run (scope trigger)
//   uo_out[4]  DONE
//   uo_out[7:5] 0
//   uio_*      unused: uio_out = 0, uio_oe = 0 (all inputs)
//
// STRUCTURE  (N_CH = 1 for now)
//   spi_regs  --run,tw-->  nco  --ref_i,ref_q,period_tick-->  lockin_core
//   sd_adc    --b----------------------------------------->   lockin_core
//   lockin_core --acc_i,acc_q,busy,done--> spi_regs
//
// CONTRACT  (test/test.py drives only these pins, so it also runs on the gate-level netlist)
//   * After reset: MISO = 0, ADC_FB = 1, MOD = 0, REF90 = 0, DONE = 0, uio_oe = 0.
//   * With CTRL.RUN = 1, MOD toggles at TW * f_clk / 2^24 and REF90 leads it by 90 deg.
//   * CTRL.START then DONE after M whole periods. I and Q are readable over SPI.
//   * Every flip-flop must be reset, or gate-level simulation shows X.
//
// DESIGN QUESTIONS TO BE READY TO DEFEND
//   1. For N_CH > 1 (SpectraBite), which channel defines the M-period window, and why
//      must the other channels' TWs then be power-of-two multiples of it?
//      (Walsh/Rademacher orthogonality: model/design_sweep.py, section 2.)
//   2. Gating MOD with run keeps the LED off when idle. Any downside?
// =============================================================================
`default_nettype none

module tt_um_manyetox_lockin (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  localparam int N_CH = 1;

  // ---------------------------------------------------------------------------
  // Pin names
  // ---------------------------------------------------------------------------
  wire spi_sclk = ui_in[0];
  wire spi_mosi = ui_in[1];
  wire spi_cs_n = ui_in[2];
  wire adc_cmp  = ui_in[3];

  // ---------------------------------------------------------------------------
  // Blocks
  // ---------------------------------------------------------------------------
  wire                 run, start, done, busy;
  wire [15:0]          m_periods;
  wire [24*N_CH-1:0]   tw;
  wire [24*N_CH-1:0]   acc_i, acc_q;
  wire                 miso;
  wire                 ref_i, ref_q, period_tick;
  wire                 b, fb;

  spi_regs #(.N_CH(N_CH)) u_spi (
      .clk      (clk),
      .rst_n    (rst_n),
      .sclk     (spi_sclk),
      .mosi     (spi_mosi),
      .cs_n     (spi_cs_n),
      .miso     (miso),
      .run      (run),
      .start    (start),
      .m_periods(m_periods),
      .tw       (tw),
      .done     (done),
      .busy     (busy),
      .acc_i    (acc_i),
      .acc_q    (acc_q)
  );

  nco u_nco (
      .clk        (clk),
      .rst_n      (rst_n),
      .en         (run),
      .tw         (tw[23:0]),
      .ref_i      (ref_i),
      .ref_q      (ref_q),
      .period_tick(period_tick)
  );

  sd_adc u_adc (
      .clk   (clk),
      .rst_n (rst_n),
      .cmp_in(adc_cmp),
      .b     (b),
      .fb    (fb)
  );

  lockin_core u_core (
      .clk        (clk),
      .rst_n      (rst_n),
      .start      (start),
      .m_periods  (m_periods),
      .b          (b),
      .ref_i      (ref_i),
      .ref_q      (ref_q),
      .period_tick(period_tick),
      .acc_i      (acc_i[23:0]),
      .acc_q      (acc_q[23:0]),
      .busy       (busy),
      .done       (done)
  );

  // ---------------------------------------------------------------------------
  // Outputs
  // ---------------------------------------------------------------------------
  assign uo_out[0]   = miso;
  assign uo_out[1]   = fb;
  assign uo_out[2]   = ref_i & run;   // LED off while stopped
  assign uo_out[3]   = ref_q & run;
  assign uo_out[4]   = done;
  assign uo_out[7:5] = 3'b000;

  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, ui_in[7:4], uio_in, 1'b0};

endmodule

`default_nettype wire

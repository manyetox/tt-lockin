/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// spi_regs.sv -- SPI mode-0 slave, oversampled by clk, plus the byte register file
// =============================================================================
// FRAME  (one frame per CS_N low period, MSB first)
//     bit 15     R/W   1 = read, 0 = write
//     bits 14:8  ADDR  7-bit byte address
//     bits 7:0   DATA  write: from MOSI.  read: on MISO (MISO = 0 during bits 15:8)
//
// REGISTER MAP  (also in model/regmap.py and docs/info.md; keep all three in sync)
//     0x00        ID       R    8'h4C
//     0x01        CTRL     RW   bit0 RUN (NCO + modulation on), bit1 START (write 1 = pulse, reads 0)
//     0x02        STATUS   R    bit0 DONE, bit1 BUSY
//     0x04-0x05   M[15:0]  RW   little-endian (0x04 = bits 7:0)
//     channel c, base = 0x10 + 0x10*c:
//       +0..+2    TW[23:0] RW
//       +4..+6    I[23:0]  R    two's complement
//       +8..+A    Q[23:0]  R    two's complement
//     Anything else reads 0x00. Writes to read-only or unmapped addresses are ignored.
//     Reset values: CTRL = 0, M = 0, TW = 0.
//
// CONTRACT  (test/unit/tb_spi_regs.py drives a real mode-0 master, async to clk)
//   * sclk, mosi, cs_n are asynchronous pins. Synchronise them before use.
//   * CS_N high resets the frame. A frame cut short by CS_N rising changes nothing.
//   * Write: takes effect when the 16th bit is sampled, i.e. before CS_N rises.
//     Within 6 clk cycles of the 16th SCLK rising edge, the target register (and run,
//     m_periods, tw outputs) hold the new value. Writing CTRL with bit1 = 1 makes `start`
//     high for EXACTLY ONE clk cycle. Writing CTRL with bit1 = 0 makes no pulse.
//   * Read: MISO must already hold each data bit at the SCLK rising edge where the master
//     samples it (bits 7..0 = 9th..16th rising edges). The first data bit (bit 7) must be
//     valid by the 9th rising edge, one SCLK period after the address is complete.
//   * Extra SCLK edges after bit 0 in the same CS_N period are ignored.
//   * The tests run SCLK = f_clk / SPI_DIV with SPI_DIV = 8 by default (env var).
//
// THINGS TO DECIDE AND BE READY TO DEFEND
//   1. Latency budget. A 2-FF synchroniser plus an edge detector sees an SCLK edge 2-3
//      clk cycles late. If you change MISO on the detected FALLING edge, MISO becomes valid
//      ~3-4 clks after that falling edge, but the master samples half an SCLK period
//      later. At SCLK = f_clk/4 that is only 2 clks: too late. Two fixes:
//        (a) keep SCLK <= f_clk/8, or
//        (b) advance MISO right after the detected RISING edge (the master has already
//            sampled the old bit), giving almost a full SCLK period of setup.
//      Which did you pick? Set SPI_DIV=4 to see whether your design survives f_clk/4.
//   2. Why is it fine to sample the SYNCHRONISED mosi on the synchronised sclk edge,
//      even though MOSI changes only half an SCLK period after the rising edge?
//   3. uo_out pins cannot be tri-stated. MISO is therefore always driven, so this chip
//      cannot share an SPI bus with other slaves unless MISO moves to a uio pin with
//      uio_oe = ~cs_n. Is a dedicated bus on the ESP32 acceptable?
//   4. Multi-byte TW writes take three frames, so the NCO briefly runs at a mixed frequency.
//      Harmless? (Stop RUN while writing TW if you care.)
// =============================================================================
`default_nettype none

module spi_regs #(
    parameter int N_CH = 1
) (
    input  logic                 clk,
    input  logic                 rst_n,      // active low
    // SPI pins, asynchronous to clk
    input  logic                 sclk,
    input  logic                 mosi,
    input  logic                 cs_n,
    output logic                 miso,
    // control outputs (clk domain)
    output logic                 run,        // CTRL.RUN
    output logic                 start,      // one-cycle pulse on a CTRL write with bit1 = 1
    output logic [15:0]          m_periods,  // M
    output logic [24*N_CH-1:0]   tw,         // channel c tuning word = tw[24*c +: 24]
    // status inputs from the lock-in cores
    input  logic                 done,
    input  logic                 busy,
    input  logic [24*N_CH-1:0]   acc_i,      // channel c I = acc_i[24*c +: 24]
    input  logic [24*N_CH-1:0]   acc_q
);

  // Register addresses (must match model/regmap.py)
  localparam logic [7:0] ID_VALUE    = 8'h4C;
  localparam logic [6:0] ADDR_ID     = 7'h00;
  localparam logic [6:0] ADDR_CTRL   = 7'h01;
  localparam logic [6:0] ADDR_STATUS = 7'h02;
  localparam logic [6:0] ADDR_M_LO   = 7'h04;
  localparam logic [6:0] ADDR_M_HI   = 7'h05;
  localparam logic [6:0] CH_BASE     = 7'h10;  // + 7'h10 per channel
  localparam logic [3:0] OFF_TW      = 4'h0;   // +0..+2
  localparam logic [3:0] OFF_I       = 4'h4;   // +4..+6
  localparam logic [3:0] OFF_Q       = 4'h8;   // +8..+A

  // ---------------------------------------------------------------------------
  // TODO(Taylan): your implementation goes here. Delete the tie-offs below.
  // ---------------------------------------------------------------------------
  assign miso      = 1'b0;
  assign run       = 1'b0;
  assign start     = 1'b0;
  assign m_periods = '0;
  assign tw        = '0;

  logic _unused;
  assign _unused = &{1'b0, clk, rst_n, sclk, mosi, cs_n, done, busy, acc_i, acc_q,
                     ID_VALUE, ADDR_ID, ADDR_CTRL, ADDR_STATUS, ADDR_M_LO, ADDR_M_HI,
                     CH_BASE, OFF_TW, OFF_I, OFF_Q};

endmodule

`default_nettype wire

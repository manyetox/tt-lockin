/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// lockin_core.sv -- 1-bit x 1-bit correlator over M whole reference periods
// =============================================================================
// WHAT IT DOES
//   With a 1-bit signal and 1-bit references, "multiply" is XNOR and "accumulate" is an
//   up/down counter:
//       I += (b == ref_i) ? +1 : -1
//       Q += (b == ref_q) ? +1 : -1
//   It integrates exactly M whole reference periods, then freezes and raises done.
//
// CONTRACT  (test/unit/tb_lockin_core.py checks this against model/lockin_model.py:LockinCore)
//   All outputs are registered: a change made at edge k is visible from cycle k+1.
//   * Reset: IDLE, acc_i = acc_q = 0, busy = 0, done = 0.
//   * START (start = 1 in cycle k), priority over everything else:
//       at edge k: acc_i = acc_q = 0, done = 0, busy = 1 (ARMED).
//       A period_tick in the same cycle k is IGNORED.
//   * ARMED, waiting for the first period_tick in a cycle after k:
//       - if m_periods == 0: done = 1, busy = 0 at that edge (empty window, I = Q = 0)
//       - otherwise that tick cycle's sample is the FIRST one integrated (RUN).
//   * RUN: every cycle's sample is integrated, except:
//       at the M-th period_tick after the first one, that sample is NOT integrated
//       (it belongs to the next period). At that edge: done = 1, busy = 0.
//     So the window is exactly M whole periods: [tick_0, tick_M).
//   * DONE: acc_i, acc_q frozen and done held high until the next start.
//   * busy/done are checked every cycle; acc_i/acc_q are checked in IDLE, ARMED (must be 0)
//     and DONE. Their values during RUN are yours to choose (running sums are natural).
//   * A start during ARMED or RUN restarts the run.
//   * m_periods must be held constant from start until done (spi_regs guarantees this
//     as long as the ESP32 doesn't write M mid-run).
//   * No overflow handling: the ESP32 keeps M <= TW/2 so that N < 2^(ACC_W-1).
//     (model: max_m_periods)
//
// THINGS TO DECIDE AND BE READY TO DEFEND
//   1. Why does an up/down counter equal a multiplier here? What is lost by quantising
//      the signal to 1 bit, and why does the sigma-delta loop get it back?
//   2. Why 24 bits? (N < 2^23 samples. What is that in seconds at 10 and 50 MHz?)
//   3. Count periods up and compare with M, or load M and count down? Which costs fewer
//      cells, and which handles M = 0 more naturally?
//   4. "Whole periods cancel DC exactly" is only exact for power-of-two TW. Why?
//      (model/design_sweep.py, section 2)
//   5. Why single-shot (freeze on done) rather than continuous with copy registers?
//      (FF budget on one GF180 tile, and atomic multi-byte SPI reads.)
// =============================================================================
`default_nettype none

module lockin_core #(
    parameter int ACC_W = 24,
    parameter int M_W   = 16
) (
    input  logic                    clk,
    input  logic                    rst_n,        // active low
    input  logic                    start,        // 1-cycle pulse from spi_regs
    input  logic [M_W-1:0]          m_periods,    // whole periods to integrate
    input  logic                    b,            // ADC bit from sd_adc
    input  logic                    ref_i,        // from nco
    input  logic                    ref_q,        // from nco
    input  logic                    period_tick,  // from nco
    output logic signed [ACC_W-1:0] acc_i,
    output logic signed [ACC_W-1:0] acc_q,
    output logic                    busy,         // ARMED or RUN
    output logic                    done          // window finished, I/Q frozen
);

  // ---------------------------------------------------------------------------
  // TODO(Taylan): your implementation goes here. Delete the tie-offs below.
  // ---------------------------------------------------------------------------
  assign acc_i = '0;
  assign acc_q = '0;
  assign busy  = 1'b0;
  assign done  = 1'b0;

  logic _unused;
  assign _unused = &{1'b0, clk, rst_n, start, m_periods, b, ref_i, ref_q, period_tick};

endmodule

`default_nettype wire

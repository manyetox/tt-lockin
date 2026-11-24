/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// nco.sv -- 24-bit phase accumulator (numerically controlled oscillator)
// =============================================================================
// WHAT IT DOES
//   Adds tw to a W-bit phase register on every enabled clock. Bit W-1 of the phase is
//   then a square wave at   f_mod = tw * f_clk / 2^W.
//
// CONTRACT  (test/unit/tb_nco.py checks this every cycle against model/lockin_model.py:Nco)
//   "Cycle k" is the interval between rising edges k-1 and k. Registers load at edge k.
//
//   * Reset (rst_n low for at least one rising edge): phase = 0, period_tick = 0.
//   * Rising edge with en = 1:  phase       <= phase + tw          (mod 2^W, just let it wrap)
//                               period_tick <= carry out of that same addition
//     Rising edge with en = 0:  phase holds, period_tick <= 0
//   * Outputs depend only on the CURRENT phase register:
//         ref_i = phase[W-1]
//         ref_q = phase[W-1] ^ phase[W-2]       (leads ref_i by 90 degrees)
//   * So period_tick is high for exactly one cycle per period: the cycle whose phase is
//     the first sample of a new reference period.
//   * tw may change at any time. Phase stays continuous (no reset on a TW write).
//
// THINGS TO DECIDE AND BE READY TO DEFEND
//   1. How do you get the carry out of a W-bit add without building a second adder?
//   2. Frequency resolution is f_clk / 2^W. What is it at 10 MHz and at 50 MHz?
//      Why is the period of ref_i not always the same number of clocks, and what does that
//      jitter look like on a scope?
//   3. ref_q is an XOR of two flip-flops. It can glitch when both bits flip on the same edge
//      (01->10 and 11->00). Inside the chip that is harmless (everything samples on clk),
//      but ref_q also drives a pin (uo_out[3]). Do you care? What fixes it, at what cost?
//   4. Sign convention: with this ref_q, a signal DELAYED by 90 deg gives Q = -N.
//      Keep it, or use XNOR so that "delayed => +Q" (textbook X = <s cos>, Y = <s sin>)?
//      If you switch, set REF_Q_XNOR = True in model/lockin_model.py (one line).
//   5. Exact DC rejection needs TW to be a power of two (model/design_sweep.py, section 2).
//      Nothing to build here, but know why.
// =============================================================================
`default_nettype none

module nco #(
    parameter int W = 24
) (
    input  logic         clk,
    input  logic         rst_n,        // active low
    input  logic         en,           // advance the phase this cycle
    input  logic [W-1:0] tw,           // tuning word
    output logic         ref_i,        // in-phase square reference = phase[W-1]
    output logic         ref_q,        // quadrature reference      = phase[W-1] ^ phase[W-2]
    output logic         period_tick   // 1 in the first cycle of every new period
);

  // ---------------------------------------------------------------------------
  // TODO(Taylan): your implementation goes here. Delete the tie-offs below.
  // ---------------------------------------------------------------------------
  assign ref_i       = 1'b0;
  assign ref_q       = 1'b0;
  assign period_tick = 1'b0;

  logic _unused;
  assign _unused = &{1'b0, clk, rst_n, en, tw};

endmodule

`default_nettype wire

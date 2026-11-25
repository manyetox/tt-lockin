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
// DESIGN QUESTIONS TO BE READY TO DEFEND
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

  // One adder, one bit wider than the phase: bit W of the sum is the carry out,
  // i.e. "the phase wrapped round", which is exactly the period_tick we need.
  logic [W-1:0] phase;
  logic [W:0]   sum;
  assign sum = {1'b0, phase} + {1'b0, tw};

  // ref_q is registered from the NEXT phase, so the pin never glitches when
  // phase[W-1] and phase[W-2] flip on the same edge (01->10, 11->00). The register
  // always holds phase[W-1]^phase[W-2] of the current phase, as the contract says.
  logic ref_q_r;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      phase       <= '0;
      period_tick <= 1'b0;
      ref_q_r     <= 1'b0;
    end else if (en) begin
      phase       <= sum[W-1:0];
      period_tick <= sum[W];
      ref_q_r     <= sum[W-1] ^ sum[W-2];
    end else begin
      period_tick <= 1'b0;          // phase and ref_q hold
    end
  end

  assign ref_i = phase[W-1];
  assign ref_q = ref_q_r;

endmodule

`default_nettype wire

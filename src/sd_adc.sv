/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// sd_adc.sv -- digital half of the pin-based first-order sigma-delta ADC
// =============================================================================
// THE WHOLE ADC
//        Vin --R1--+--R2-- fb  (uo_out[1], driven 0 / VDD)
//                  |
//                  C            node --> cmp_in (ui_in[3])
//                  |
//                 GND
//   The input buffer of a digital pin is the 1-bit quantiser, C is the integrator and fb
//   is the 1-bit DAC. The loop keeps the node at the pin threshold, so on average
//       (Vin - Vth)/R1 + (VDD*(1-d) - Vth)/R2 = 0
//   where d is the density of ones in b. With R1 = R2:  d = (Vin + VDD - 2*Vth) / VDD.
//   The threshold only shifts the offset, the gain is R2/(R1*VDD), and the lock-in rejects
//   the offset. (model/lockin_model.py: PinAdc.d_ideal)
//
// CONTRACT  (test/unit/tb_sd_adc.py)
//   * cmp_in comes straight from a pin. It is asynchronous: the node hovers at the
//     threshold, so the pin buffer output toggles at arbitrary times.
//   * b during cycle k = cmp_in sampled at rising edge k-2   (two flip-flops in series)
//   * fb = ~b
//   * Reset: both flip-flops 0, so b = 0 and fb = 1.
//
// THINGS TO DECIDE AND BE READY TO DEFEND
//   1. Why two flip-flops, not one or three? (MTBF: resolution time vs clock period.)
//   2. Is fb = ~b as a combinational inverter on a flop output glitch-free? Why does
//      that matter for a signal that drives an RC network?
//   3. The loop delay is ~2 clocks plus pin delays. Why does that not make the loop
//      unstable? (First order, tau = C*R1*R2/(R1+R2) = 500 clocks for 10k/10k/10n at 10 MHz.)
//   4. What happens to b if Vin leaves the range [2*Vth - VDD, 2*Vth]? Can the lock-in
//      tell you? (It cannot: a stuck b gives I = Q = 0. See docs/info.md, "saturation".)
// =============================================================================
`default_nettype none

module sd_adc (
    input  logic clk,
    input  logic rst_n,    // active low
    input  logic cmp_in,   // raw pin: RC node through the pin's input buffer
    output logic b,        // synchronised bit, density tracks Vin
    output logic fb        // feedback pin drive = ~b
);

  // ---------------------------------------------------------------------------
  // TODO(Taylan): your implementation goes here. Delete the tie-offs below.
  // ---------------------------------------------------------------------------
  assign b  = 1'b0;
  assign fb = 1'b1;

  logic _unused;
  assign _unused = &{1'b0, clk, rst_n, cmp_in};

endmodule

`default_nettype wire

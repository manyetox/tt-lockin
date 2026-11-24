// =============================================================================
// lockin_pll.v -- 50 MHz -> 10 MHz for the DE0-Nano (Cyclone IV E), used only when
// de0_nano_top is built with USE_PLL_10MHZ = 1.
//
// Hand-written altpll instantiation in the same shape as the IP Catalog output.
// If Quartus rejects it, delete this file and generate the IP instead:
//   IP Catalog -> Basic Functions -> Clocks; PLLs and Resets -> PLL -> ALTPLL
//   name: lockin_pll (Verilog), inclk0 = 50 MHz, output c0 = 10 MHz (multiply 1,
//   divide 5), keep the 'areset' and 'locked' ports. The wrapper doesn't change.
// =============================================================================
module lockin_pll (
    input  wire areset,
    input  wire inclk0,
    output wire c0,
    output wire locked
);

  wire [4:0] clk_bus;
  assign c0 = clk_bus[0];
  wire _unused = &{clk_bus[4:1], 1'b0};

  altpll #(
      .bandwidth_type         ("AUTO"),
      .clk0_divide_by         (5),
      .clk0_duty_cycle        (50),
      .clk0_multiply_by       (1),
      .clk0_phase_shift       ("0"),
      .compensate_clock       ("CLK0"),
      .inclk0_input_frequency (20000),            // ps
      .intended_device_family ("Cyclone IV E"),
      .lpm_hint               ("CBX_MODULE_PREFIX=lockin_pll"),
      .lpm_type               ("altpll"),
      .operation_mode         ("NORMAL"),
      .pll_type               ("AUTO"),
      .port_areset            ("PORT_USED"),
      .port_inclk0            ("PORT_USED"),
      .port_inclk1            ("PORT_UNUSED"),
      .port_locked            ("PORT_USED"),
      .port_clk0              ("PORT_USED"),
      .port_clk1              ("PORT_UNUSED"),
      .port_clk2              ("PORT_UNUSED"),
      .port_clk3              ("PORT_UNUSED"),
      .port_clk4              ("PORT_UNUSED"),
      .self_reset_on_loss_lock("OFF"),
      .width_clock            (5)
  ) altpll_component (
      .areset(areset),
      .inclk ({1'b0, inclk0}),
      .clk   (clk_bus),
      .locked(locked)
  );

endmodule

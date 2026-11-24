/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// de0_nano_top.sv -- Terasic DE0-Nano (Cyclone IV E EP4CE22F17C6) wrapper
// =============================================================================
// Instantiates tt_um_manyetox_lockin UNCHANGED, so the FPGA runs exactly the RTL that
// goes to the chip. All Tiny Tapeout pins are brought out on GPIO_1 (header JP2) in
// TT order:
//
//   TT pin       GPIO_1 bit   JP2 header pin   FPGA pin   use
//   ui_in[0]     GPIO_1[0]    2                F13        SPI SCLK   (from ESP32)
//   ui_in[1]     GPIO_1[1]    4                T15        SPI MOSI
//   ui_in[2]     GPIO_1[2]    5                T14        SPI CS_N   (weak pull-up)
//   ui_in[3]     GPIO_1[3]    6                T13        ADC_CMP    RC node (NO pull-up!)
//   ui_in[4]     GPIO_1[4]    7                R13        EXT_REF    (stretch)
//   ui_in[7:5]   GPIO_1[7:5]  8, 9, 10         T12 R12 T11 spare
//   uo_out[0]    GPIO_1[8]    13               T10        SPI MISO
//   uo_out[1]    GPIO_1[9]    14               R11        ADC_FB     -> R2 -> RC node
//   uo_out[2]    GPIO_1[10]   15               P11        MOD        LED/laser modulation
//   uo_out[3]    GPIO_1[11]   16               R10        REF90      scope trigger
//   uo_out[4]    GPIO_1[12]   17               N12        DONE
//   uo_out[7:5]  GPIO_1[15:13] 18, 19, 20      P9 N9 N11  0
//   uio[7:0]     GPIO_1[23:16] 21..28          L16..N16   inputs (uio_oe = 0)
//   GND: JP2 pins 12 and 30.   3.3 V: JP2 pin 29.   5 V: JP2 pin 11 (never on a GPIO!)
//
// On-board extras
//   KEY[0]  reset (active low). KEY[1], SW[3:0] unused.
//   LED[0]  DONE          LED[1]  MOD (glows at half brightness while running)
//   LED[2]  SPI active    LED[3]  ADC_FB (brightness ~ 1 - bit density)
//   LED[7]  clock running and out of reset
//
// Clock: parameter USE_PLL_10MHZ (set in lockin_de0nano.qsf with set_parameter)
//   0 -> the design runs from CLOCK_50 directly (50 MHz, matches CLOCK_PERIOD = 20 ns)
//   1 -> the design runs at 10 MHz from the Cyclone IV PLL (lockin_pll.v)
// =============================================================================
`default_nettype none

module de0_nano_top #(
    parameter bit USE_PLL_10MHZ = 1'b0
) (
    input  wire        CLOCK_50,
    input  wire [1:0]  KEY,
    input  wire [3:0]  SW,
    output wire [7:0]  LED,
    inout  wire [23:0] GPIO_1
);

  // ---------------------------------------------------------------------------
  // Clock
  // ---------------------------------------------------------------------------
  wire clk;
  wire pll_locked;

  generate
    if (USE_PLL_10MHZ) begin : g_pll
      lockin_pll u_pll (
          .areset(1'b0),
          .inclk0(CLOCK_50),
          .c0    (clk),
          .locked(pll_locked)
      );
    end else begin : g_direct
      assign clk        = CLOCK_50;
      assign pll_locked = 1'b1;
    end
  endgenerate

  // ---------------------------------------------------------------------------
  // Reset: asynchronous assert (KEY[0] pressed or PLL unlocked), synchronous release
  // ---------------------------------------------------------------------------
  wire       rst_async_n = KEY[0] & pll_locked;
  logic [1:0] rst_sync;
  always_ff @(posedge clk or negedge rst_async_n) begin
    if (!rst_async_n) rst_sync <= 2'b00;
    else              rst_sync <= {rst_sync[0], 1'b1};
  end
  wire rst_n = rst_sync[1];

  // ---------------------------------------------------------------------------
  // The chip, unchanged
  // ---------------------------------------------------------------------------
  wire [7:0] ui_in;
  wire [7:0] uo_out;
  wire [7:0] uio_in;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  tt_um_manyetox_lockin u_chip (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (1'b1),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // ui_in: header inputs (GPIO_1[7:0] are never driven by the FPGA)
  assign GPIO_1[7:0] = 8'bz;
  assign ui_in       = GPIO_1[7:0];

  // uo_out: header outputs
  assign GPIO_1[15:8] = uo_out;

  // uio: real tri-state, like the TT pad ring
  genvar i;
  generate
    for (i = 0; i < 8; i++) begin : g_uio
      assign GPIO_1[16+i] = uio_oe[i] ? uio_out[i] : 1'bz;
    end
  endgenerate
  assign uio_in = GPIO_1[23:16];

  // ---------------------------------------------------------------------------
  // LEDs
  // ---------------------------------------------------------------------------
  assign LED[0]   = uo_out[4];       // DONE
  assign LED[1]   = uo_out[2];       // MOD
  assign LED[2]   = ~ui_in[2];       // CS_N low = SPI transfer in progress
  assign LED[3]   = uo_out[1];       // ADC_FB
  assign LED[6:4] = 3'b000;
  assign LED[7]   = rst_n;           // clock running, out of reset

  wire _unused = &{KEY[1], SW, 1'b0};

endmodule

`default_nettype wire

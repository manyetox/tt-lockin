`default_nettype none
`timescale 1ns / 1ps
// Harness for the DE0-Nano wrapper: turns the inout header into plain regs/wires
// that cocotb can drive and read. Built with USE_PLL_10MHZ = 0 (no altpll in Icarus).
module tb_de0_nano ();
  reg        CLOCK_50;
  reg  [1:0] KEY;
  reg  [3:0] SW;
  reg  [7:0] ui_pins;      // what the outside world drives onto GPIO_1[7:0]
  wire [7:0] LED;
  wire [23:0] GPIO_1;

  assign GPIO_1[7:0] = ui_pins;   // GPIO_1[23:8] are left to the FPGA (or float)

  de0_nano_top #(.USE_PLL_10MHZ(1'b0)) dut (
      .CLOCK_50(CLOCK_50),
      .KEY     (KEY),
      .SW      (SW),
      .LED     (LED),
      .GPIO_1  (GPIO_1)
  );
endmodule
`default_nettype wire

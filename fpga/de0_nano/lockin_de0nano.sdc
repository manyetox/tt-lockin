# lockin_de0nano.sdc -- timing constraints for the DE0-Nano build

create_clock -name CLOCK_50 -period 20.000 [get_ports {CLOCK_50}]
derive_pll_clocks
derive_clock_uncertainty

# Every design input is asynchronous to clk (SPI pins, the RC node, keys) and is
# synchronised inside the design; every output goes to slow external loads (ESP32 at
# SCLK <= f_clk/8, RC network, LED driver, scope). Nothing to time at the pins.
set_false_path -from [get_ports {KEY[*] SW[*] GPIO_1[*]}]
set_false_path -to   [get_ports {LED[*] GPIO_1[*]}]

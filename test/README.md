# Tests

Everything uses cocotb 2.1 with Icarus Verilog. Tests count in clock cycles. The absolute
clock only affects the SPI master and the RC-node model: `CLK_NS` (default 20, i.e. 50 MHz).

## Unit tests, one module each (`unit/`)

```sh
make units            # = cd unit && python -m pytest -v
make units K=nco      # one module
WAVES=1 make units K=lockin_core   # FST in unit/sim_build/lockin_core/
SPI_DIV=4 make units K=spi         # SCLK = f_clk/4 stress test
CLK_NS=100 make units              # pretend 10 MHz
```

| Module | File | What it checks |
|---|---|---|
| nco | `tb_nco.py` | bit-exact vs model, frequency, ref_q leading by exactly 1/4 period, enable, TW changes |
| lockin_core | `tb_lockin_core.py` | in-phase I = N; quadrature signs; DC = 0 for power-of-two TW; DC leak for odd periods; M = 0 / 1; start rules; random bit-exact; noise bound |
| sd_adc | `tb_sd_adc.py` | reset values, 2-cycle synchroniser latency, closed-loop density vs Vin with the RC-node model, bit-exact vs model, threshold moves offset not gain |
| spi_regs | `tb_spi_regs.py` | ID/reset values, read/write, START pulse width, STATUS/I/Q reads, MISO zero outside data, aborted frames, extra clocks, write before CS rises, read-only/unmapped |
| DE0-Nano wrapper | `tb_fpga_wrapper.py` | KEY reset, JP2 pin order, uio high-Z, LEDs (needs the whole chip) |

## Top level (`test.py` through `tb.v`)

```sh
make -B               # RTL
make -B GATES=yes     # gate level (gate_level_netlist.v comes from the GDS action)
```

A `Bench` coroutine plays the board: the LED follows MOD, the photodiode/TIA voltage is
`V_dc + V_step*LED`, and the R1/R2/C node is driven from ADC_FB onto ADC_CMP. `SpiMaster`
plays the ESP32. The tests touch only pins, so they work on the gate-level netlist.

## View waveforms

```sh
gtkwave tb.fst tb.gtkw
```

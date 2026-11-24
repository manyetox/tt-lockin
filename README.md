![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# Lock-in amplifier on Tiny Tapeout (GF180, shuttle GF26c)

`tt_um_manyetox_lockin`: a digital lock-in amplifier on one GF180 tile, read by an ESP32
over SPI. Datasheet: [docs/info.md](docs/info.md).

## Layout

| Path | What |
|---|---|
| `src/*.sv` | RTL. **Stubs**: ports and the cycle-accurate contract in each header; bodies are Taylan's to write |
| `model/lockin_model.py` | Reference model: bit-exact digital part + physical model of the pin sigma-delta ADC and the optics |
| `model/design_sweep.py` | Chooses f_clk, TW, M, R and C with numbers (6 sections, ~1 min) |
| `model/regmap.py` | Register map shared by tests, docs and (by copy) the ESP32 firmware |
| `test/unit/` | Per-module cocotb tests (`tb_*.py`), run by `test_units.py` (pytest) |
| `test/test.py` | Top-level tests through `tb.v`, pins only, so they also run at gate level |
| `test/tb_lib.py` | Clock/reset helpers, an asynchronous SPI mode-0 master, pin buses |
| `fpga/de0_nano/` | DE0-Nano (EP4CE22F17C6) wrapper, PLL, Quartus project, pin map, wiring notes |

## Quick start (WSL2, Ubuntu)

```sh
sudo apt install iverilog verilator gtkwave
python3 -m venv ~/venv-tt && source ~/venv-tt/bin/activate
pip install -r test/requirements.txt numba matplotlib   # numba optional, speeds up the model

cd model && python design_sweep.py --quick        # the numbers behind the design choices
cd ../test && make units K=nco                    # one module's tests
make units                                        # all unit tests
make -B                                           # top-level test
WAVES=1 make units K=nco && gtkwave unit/sim_build/nco/nco.fst
```

With the stubs, every functional test fails. That is the starting point: each module
is done when its tests pass. Suggested order: `nco`, `lockin_core`, `sd_adc`, `spi_regs`,
then `project.sv` wiring, then the FPGA (`make units K=de0` checks the wrapper first).

## Workflow on GitHub

* **gds** hardens the design and reports cell count, utilisation and timing at 20 ns.
  It should already pass with the stubs (an empty design). Push early to see real numbers.
* **test** runs the unit tests, then the top-level test. It turns green module by module.
* **docs** checks `info.yaml` and `docs/info.md`.

## Findings from the model (details in `model/design_sweep.py`)

* Constant offsets cancel **exactly only for power-of-two TW**. Otherwise a small leak
  remains, as large as one count per period when the period is an odd number of clocks.
* `ref_q = p[23] ^ p[22]` **leads** `ref_i`, so a delayed signal gives **negative** Q.
  Either keep that and use `square_phase_deg()`, or switch to XNOR.
* `|I| + |Q|` is exact only when the **signal** is square too. For a sine excitation (e.g.
  the MOF potentiostat), use `(pi/2)*sqrt(I^2 + Q^2)/N`.
* Integrate a whole number of 10 ms (100 Hz flicker) periods: M = 122 at TW = 2^12 and 10 MHz.
* An oversampled SPI slave with 2-FF synchronisers needs SCLK at most f_clk/8 if MISO shifts on
  the falling edge. Shifting MISO right after the rising edge lets it reach f_clk/4.

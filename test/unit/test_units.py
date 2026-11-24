"""
test_units.py -- pytest runner for the per-module cocotb tests (Icarus, cocotb 2.1).

    cd test/unit
    python -m pytest -v                 # all modules
    python -m pytest -v -k nco          # one module
    WAVES=1 python -m pytest -k nco     # also dump sim_build/nco/nco.fst
    CLK_NS=100 python -m pytest         # pretend 10 MHz (affects SPI/RC timing only)
    SPI_DIV=4 python -m pytest -k spi   # stress test: SCLK = f_clk/4

The top-level test (test/test.py) is run by the template Makefile instead:
    cd test && make -B          # RTL
    cd test && make -B GATES=yes  # gate level, after the GDS action
"""
import os
import sys
from pathlib import Path

import pytest
from cocotb_tools.runner import get_results, get_runner

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = Path(os.environ.get("SRC_DIR", ROOT / "src"))

# the simulator inherits sys.path as PYTHONPATH: tb modules, tb_lib and the model
for p in (HERE, HERE.parent, ROOT / "model"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

CHIP = ["nco.sv", "sd_adc.sv", "lockin_core.sv", "spi_regs.sv", "project.sv"]
UNITS = {
    # toplevel        sources                                              cocotb test module
    "nco":          ([SRC / "nco.sv"],                                    "tb_nco"),
    "sd_adc":       ([SRC / "sd_adc.sv"],                                 "tb_sd_adc"),
    "lockin_core":  ([SRC / "lockin_core.sv"],                            "tb_lockin_core"),
    "spi_regs":     ([SRC / "spi_regs.sv"],                               "tb_spi_regs"),
    # board wrapper smoke test (needs the whole chip working)
    "tb_de0_nano":  ([SRC / f for f in CHIP]
                     + [ROOT / "fpga/de0_nano/de0_nano_top.sv", HERE / "tb_de0_nano.v"], "tb_fpga_wrapper"),
}


@pytest.mark.parametrize("unit", list(UNITS))
def test_unit(unit):
    sources, module = UNITS[unit]
    waves = os.environ.get("WAVES", "0") == "1"
    build_dir = HERE / "sim_build" / unit
    runner = get_runner("icarus")
    runner.build(
        sources=sources,
        hdl_toplevel=unit,
        build_dir=build_dir,
        timescale=("1ns", "1ps"),
        waves=waves,
        always=True,
    )
    results = runner.test(
        hdl_toplevel=unit,
        test_module=module,
        build_dir=build_dir,
        test_dir=build_dir,
        waves=waves,
        timescale=("1ns", "1ps"),
    )
    n_tests, n_fail = get_results(results)
    assert n_fail == 0, f"{unit}: {n_fail} of {n_tests} cocotb tests failed (see log above)"

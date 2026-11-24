# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info
#
#   make -B              top-level RTL test (test.py through tb.v)
#   make -B GATES=yes    same test on the gate-level netlist (after the GDS action)
#   make units           per-module unit tests (unit/test_units.py, needs pytest)
#   make units K=nco     one module only
#   make sweep           run the reference-model design sweep

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
# Keep this list in sync with source_files in ../info.yaml
PROJECT_SOURCES = nco.sv sd_adc.sv lockin_core.sv spi_regs.sv project.sv

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DUSE_POWER_PINS
COMPILE_ARGS    += -DSIM
COMPILE_ARGS    += -DUNIT_DELAY=\#1
VERILOG_SOURCES += $(PDK_ROOT)/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/primitives.v
VERILOG_SOURCES += $(PDK_ROOT)/gf180mcuD/libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/gf180mcu_fd_sc_mcu7t5v0.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# SystemVerilog sources (logic, always_ff, always_comb)
COMPILE_ARGS 		+= -g2012

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# The tests import the reference model from ../model
export PYTHONPATH := $(PWD):$(PWD)/../model:$(PYTHONPATH)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# ---------------------------------------------------------------------------
# Extra targets (defined after the include so the default goal stays cocotb's)
.PHONY: units sweep
K ?=
units:
	cd unit && python -m pytest -v $(if $(K),-k $(K),)

sweep:
	cd ../model && python design_sweep.py --quick

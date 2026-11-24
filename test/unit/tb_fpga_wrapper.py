"""
tb_fpga_wrapper.py -- smoke test of fpga/de0_nano/de0_nano_top.sv (50 MHz path).

Checks the board-level plumbing before you open Quartus: KEY[0] reset, the TT pin
order on header JP2 (GPIO_1), uio pins left floating, and the LEDs. Needs the whole chip
RTL, so expect it to fail until spi_regs and nco work.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from tb_lib import CLK_PS, PinBus, SpiMaster
import regmap as R

SCLK, MOSI, CS_N = 0, 1, 2            # ui_pins bits = GPIO_1[2:0]
MISO, ADC_FB, MOD, REF90, DONE = 8, 9, 10, 11, 12   # GPIO_1 bits


def gpio(dut, n):
    v = dut.GPIO_1.value
    s = str(v)[::-1]  # LogicArray string is MSB first
    return s[n]


@cocotb.test()
async def test_wrapper_pins(dut):
    cocotb.start_soon(Clock(dut.CLOCK_50, CLK_PS, unit="ps").start())
    dut.SW.value = 0
    ui = PinBus(dut.ui_pins, init=1 << CS_N)
    spi = SpiMaster(lambda v: ui.set(SCLK, v), lambda v: ui.set(MOSI, v), lambda v: ui.set(CS_N, v),
                    lambda: int(gpio(dut, MISO)))
    dut.KEY.value = 0b10  # KEY[0] pressed = reset
    await ClockCycles(dut.CLOCK_50, 10)
    assert str(dut.LED.value)[0] == "0", "LED[7] must be off during reset"
    dut.KEY.value = 0b11
    await ClockCycles(dut.CLOCK_50, 10)
    assert str(dut.LED.value)[0] == "1", "LED[7] must be on once out of reset"

    for n in range(16, 24):
        assert gpio(dut, n) in "zZ", f"GPIO_1[{n}] (uio) must be high-Z, is {gpio(dut, n)}"
    assert gpio(dut, ADC_FB) == "1", "ADC_FB (GPIO_1[9], JP2-14) must be 1 after reset"

    assert await spi.read(R.ADDR_ID) == R.ID_VALUE, "ID over JP2 pins 2/4/5/13"
    await spi.write_le(R.addr_tw(), 1 << 18, 3)
    await spi.write(R.ADDR_CTRL, R.CTRL_RUN)
    mod, led1 = [], []
    for _ in range(256):
        await FallingEdge(dut.CLOCK_50)
        mod.append(gpio(dut, MOD))
        led1.append(str(dut.LED.value)[-2])
    rises = sum(1 for a, b in zip(mod, mod[1:]) if a == "0" and b == "1")
    assert rises in (3, 4, 5), f"MOD on GPIO_1[10] (JP2-15): {rises} periods in 256 clocks, expected 4"
    assert mod == led1, "LED[1] must mirror MOD"

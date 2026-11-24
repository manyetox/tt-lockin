"""
tb_lib.py -- shared cocotb helpers: clock/reset, an SPI mode-0 master, pin buses.

Timing: every test is written in clock cycles. The absolute clock only matters for the
SPI master and the RC-node model, and comes from the CLK_NS environment variable
(default 20 ns = 50 MHz, the template's CLOCK_PERIOD). Try CLK_NS=100 for 10 MHz.
"""
from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

# make model/ importable from every test module
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "model"))

CLK_NS = float(os.environ.get("CLK_NS", "20"))
CLK_PS = int(round(CLK_NS * 1000))
SPI_DIV = int(os.environ.get("SPI_DIV", "8"))  # SCLK = f_clk / SPI_DIV


def val(sig) -> int:
    """Integer value of a signal; raises if it contains X or Z."""
    v = sig.value
    if hasattr(v, "is_resolvable") and not v.is_resolvable:
        raise AssertionError(f"{sig._name} is not resolvable: {v}")
    return int(v)


def sval(sig, bits: int) -> int:
    """Signed integer value of a signal."""
    u = val(sig) & ((1 << bits) - 1)
    return u - (1 << bits) if u >> (bits - 1) else u


async def start_clock(clk) -> None:
    cocotb.start_soon(Clock(clk, CLK_PS, unit="ps").start())


async def reset(dut, cycles: int = 5, **idle_inputs) -> None:
    """Hold rst_n low for `cycles` rising edges with the given inputs, then release it
    at a falling edge. Returns at that falling edge (the next rising edge is the first
    functional one)."""
    for name, value in idle_inputs.items():
        getattr(dut, name).value = value
    dut.rst_n.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


class PinBus:
    """Owns an input bus (e.g. ui_in) so several coroutines can drive single bits of it."""

    def __init__(self, sig, width: int = 8, init: int = 0):
        self.sig, self.width, self.value = sig, width, init
        sig.value = init

    def set(self, bit: int, level: int) -> None:
        if level:
            self.value |= 1 << bit
        else:
            self.value &= ~(1 << bit)
        self.sig.value = self.value


class SpiMaster:
    """SPI mode 0 master (CPOL = 0, CPHA = 0), MSB first, asynchronous to the DUT clock.

    MOSI changes after falling edges, MISO is sampled at the instant of each rising
    edge (exactly what an ESP32 does), so it checks the slave's MISO setup timing for real.
    """

    def __init__(self, set_sclk, set_mosi, set_csn, get_miso, div: int = SPI_DIV, seed: int = 1):
        self.set_sclk, self.set_mosi, self.set_csn, self.get_miso = set_sclk, set_mosi, set_csn, get_miso
        self.half_ps = CLK_PS * div // 2
        self.rng = random.Random(seed)
        set_sclk(0)
        set_mosi(0)
        set_csn(1)

    async def _wait(self, ps: int) -> None:
        await Timer(int(ps), unit="ps")

    async def xfer16(self, word: int, bits: int = 16, extra_clocks: int = 0,
                     hold_ps: int | None = None) -> int:
        """Send `bits` bits of `word` (MSB first) in one CS_N low period. Returns MISO bits.
        bits < 16 aborts the frame early. hold_ps keeps CS_N low that long after the last
        falling edge (default: one half period)."""
        await self._wait(self.rng.randrange(1, CLK_PS))  # random phase to the DUT clock
        self.set_csn(0)
        rx = 0
        for i in range(bits + extra_clocks):
            self.set_mosi((word >> (15 - i)) & 1 if i < 16 else self.rng.randrange(2))
            await self._wait(self.half_ps)
            rx = (rx << 1) | (self.get_miso() & 1) if i < 16 else rx  # sample AT the rising edge
            self.set_sclk(1)
            await self._wait(self.half_ps)
            self.set_sclk(0)
        await self._wait(self.half_ps if hold_ps is None else hold_ps)
        self.set_csn(1)
        await self._wait(2 * self.half_ps)  # CS_N high time between frames
        return rx

    async def write(self, addr: int, data: int) -> None:
        from regmap import frame
        await self.xfer16(frame(False, addr, data))

    async def read(self, addr: int) -> int:
        from regmap import frame
        return (await self.xfer16(frame(True, addr))) & 0xFF

    async def write_le(self, addr: int, value: int, nbytes: int) -> None:
        for k in range(nbytes):
            await self.write(addr + k, (value >> (8 * k)) & 0xFF)

    async def read_le(self, addr: int, nbytes: int, signed: bool = False) -> int:
        v = 0
        for k in range(nbytes):
            v |= (await self.read(addr + k)) << (8 * k)
        if signed and v >> (8 * nbytes - 1):
            v -= 1 << (8 * nbytes)
        return v

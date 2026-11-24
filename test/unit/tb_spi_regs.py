"""
tb_spi_regs.py -- cocotb unit tests for src/spi_regs.sv.

The SPI master (tb_lib.SpiMaster) is asynchronous to clk and samples MISO exactly at
each SCLK rising edge, like the ESP32 does, so MISO setup time is really tested.
SCLK = f_clk / SPI_DIV (default 8). Try SPI_DIV=4 once everything passes.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

from tb_lib import CLK_PS, SpiMaster, reset, start_clock, val
import regmap as R


class StartMonitor:
    """Records every pulse on `start`: list of pulse lengths in clk cycles."""

    def __init__(self, dut):
        self.dut, self.pulses, self._len = dut, [], 0
        cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await FallingEdge(self.dut.clk)
            if val(self.dut.start):
                self._len += 1
            elif self._len:
                self.pulses.append(self._len)
                self._len = 0


async def setup(dut, seed=1):
    await start_clock(dut.clk)
    dut.done.value = 0
    dut.busy.value = 0
    dut.acc_i.value = 0
    dut.acc_q.value = 0
    spi = SpiMaster(
        set_sclk=lambda v: setattr(dut.sclk, "value", v),
        set_mosi=lambda v: setattr(dut.mosi, "value", v),
        set_csn=lambda v: setattr(dut.cs_n, "value", v),
        get_miso=lambda: val(dut.miso),
        seed=seed,
    )
    await reset(dut)
    await ClockCycles(dut.clk, 4)
    return spi


async def settle(dut):
    await ClockCycles(dut.clk, 3)


@cocotb.test()
async def test_id_and_reset_values(dut):
    spi = await setup(dut)
    mon = StartMonitor(dut)
    assert await spi.read(R.ADDR_ID) == R.ID_VALUE
    for addr in (R.ADDR_CTRL, R.ADDR_STATUS, R.ADDR_M, R.ADDR_M + 1,
                 R.addr_tw(), R.addr_tw() + 1, R.addr_tw() + 2):
        got = await spi.read(addr)
        assert got == 0, f"register 0x{addr:02x} reads 0x{got:02x} after reset, expected 0"
    assert (val(dut.run), val(dut.m_periods), val(dut.tw)) == (0, 0, 0)
    assert val(dut.miso) == 0, "MISO must be 0 while CS_N is high"
    assert mon.pulses == [], "start pulsed without a CTRL write"


@cocotb.test()
async def test_write_read_registers(dut):
    spi = await setup(dut)
    rng = random.Random(3)
    for _ in range(3):
        tw = rng.randrange(1 << 24)
        m = rng.randrange(1 << 16)
        await spi.write_le(R.addr_tw(), tw, 3)
        await spi.write_le(R.ADDR_M, m, 2)
        await settle(dut)
        assert val(dut.tw) == tw, f"tw output 0x{val(dut.tw):06x}, wrote 0x{tw:06x}"
        assert val(dut.m_periods) == m, f"m_periods output {val(dut.m_periods)}, wrote {m}"
        assert await spi.read_le(R.addr_tw(), 3) == tw
        assert await spi.read_le(R.ADDR_M, 2) == m


@cocotb.test()
async def test_ctrl_run_and_start_pulse(dut):
    spi = await setup(dut)
    mon = StartMonitor(dut)
    await spi.write(R.ADDR_CTRL, R.CTRL_RUN | R.CTRL_START)
    await settle(dut)
    assert val(dut.run) == 1
    assert mon.pulses == [1], f"start pulses {mon.pulses}, expected exactly one 1-cycle pulse"
    assert await spi.read(R.ADDR_CTRL) == R.CTRL_RUN, "CTRL must read back RUN only (START reads 0)"
    await spi.write(R.ADDR_CTRL, R.CTRL_RUN)  # no START bit: no pulse
    await settle(dut)
    assert mon.pulses == [1], f"unexpected start pulse: {mon.pulses}"
    await spi.write(R.ADDR_CTRL, R.CTRL_START)  # START with RUN = 0
    await settle(dut)
    assert val(dut.run) == 0 and mon.pulses == [1, 1], f"run={val(dut.run)}, pulses {mon.pulses}"


@cocotb.test()
async def test_status_and_accumulators(dut):
    spi = await setup(dut)
    rng = random.Random(11)
    for done, busy in ((0, 0), (0, 1), (1, 0), (1, 1)):
        dut.done.value, dut.busy.value = done, busy
        await settle(dut)
        got = await spi.read(R.ADDR_STATUS)
        assert got == done * R.STATUS_DONE + busy * R.STATUS_BUSY, f"STATUS 0x{got:02x} for done={done} busy={busy}"
    for i_val, q_val in ((123456, -654321), (-1, 1), (-(1 << 23), (1 << 23) - 1),
                         (rng.randrange(-(1 << 23), 1 << 23), rng.randrange(-(1 << 23), 1 << 23))):
        dut.acc_i.value = i_val & 0xFFFFFF
        dut.acc_q.value = q_val & 0xFFFFFF
        await settle(dut)
        assert await spi.read_le(R.addr_i(), 3, signed=True) == i_val
        assert await spi.read_le(R.addr_q(), 3, signed=True) == q_val


@cocotb.test()
async def test_miso_is_zero_outside_data_bits(dut):
    """For a read, MISO is 0 during the 8 command bits and returns the data in bits 7:0."""
    spi = await setup(dut)
    dut.acc_i.value = 0xFFFFFF
    await settle(dut)
    rx = await spi.xfer16(R.frame(True, R.addr_i()))
    assert rx == 0x00FF, f"MISO word 0x{rx:04x}, expected 0x00ff"


@cocotb.test()
async def test_aborted_frame_is_ignored(dut):
    """CS_N rising after 12 bits must not write anything; the next full frame works."""
    spi = await setup(dut)
    mon = StartMonitor(dut)
    await spi.xfer16(R.frame(False, R.addr_tw(), 0xAB), bits=12)
    await spi.xfer16(R.frame(False, R.ADDR_CTRL, R.CTRL_RUN | R.CTRL_START), bits=15)
    await settle(dut)
    assert val(dut.tw) == 0 and val(dut.run) == 0 and mon.pulses == []
    await spi.write(R.addr_tw(), 0x5A)
    await settle(dut)
    assert val(dut.tw) == 0x5A


@cocotb.test()
async def test_extra_clocks_are_ignored(dut):
    """SCLK edges after the 16th bit (same CS_N period) must not corrupt the write."""
    spi = await setup(dut)
    await spi.xfer16(R.frame(False, R.ADDR_M, 0x3C), extra_clocks=5)
    await settle(dut)
    assert val(dut.m_periods) == 0x3C
    assert await spi.read(R.ADDR_ID) == R.ID_VALUE


@cocotb.test()
async def test_write_commits_before_cs_rises(dut):
    """The write lands within 6 clk cycles of the 16th SCLK rising edge, with CS_N still low."""
    spi = await setup(dut)
    seen = {}

    async def watch():
        await FallingEdge(dut.cs_n)
        for _ in range(16):
            await RisingEdge(dut.sclk)
        await ClockCycles(dut.clk, 6)
        await FallingEdge(dut.clk)
        seen["cs_n"] = val(dut.cs_n)
        seen["m"] = val(dut.m_periods)

    cocotb.start_soon(watch())
    await spi.xfer16(R.frame(False, R.ADDR_M, 0x77), hold_ps=30 * CLK_PS)
    assert seen.get("cs_n") == 0, "watcher did not run while CS_N was low"
    assert seen.get("m") == 0x77, f"m_periods = 0x{seen.get('m', 0):02x} 6 clocks after bit 16, expected 0x77"


@cocotb.test()
async def test_readonly_and_unmapped(dut):
    """Unmapped addresses read 0. Writes to read-only/unmapped addresses change nothing."""
    spi = await setup(dut)
    mon = StartMonitor(dut)
    dut.acc_i.value = 0x123456
    for addr in (0x03, 0x06, 0x0F, 0x13, 0x17, 0x1B, 0x1F, 0x20, 0x55, 0x7F):
        got = await spi.read(addr)
        assert got == 0, f"unmapped 0x{addr:02x} reads 0x{got:02x}"
    for addr in (R.ADDR_ID, R.ADDR_STATUS, R.addr_i(), R.addr_q(), 0x03, 0x13, 0x7F):
        await spi.write(addr, 0xFF)
    await settle(dut)
    assert (val(dut.run), val(dut.m_periods), val(dut.tw)) == (0, 0, 0)
    assert mon.pulses == []
    assert await spi.read(R.ADDR_ID) == R.ID_VALUE
    assert await spi.read_le(R.addr_i(), 3) == 0x123456

# SPDX-FileCopyrightText: © 2026 Taylan Arslan
# SPDX-License-Identifier: Apache-2.0
"""
test.py -- top-level tests for tt_um_manyetox_lockin (driven through tb.v).

Uses only the chip's pins, so the same tests run on the RTL (make -B) and on the
gate-level netlist (make -B GATES=yes, also run by the GDS action).

The "bench" coroutine plays the board around the chip every clock cycle:
  * LED: follows the MOD pin (uo_out[2]), optionally delayed;
  * photodiode + TIA: Vin = V_dc + V_step * LED (+ optional slow "ambient" wobble);
  * the R1/R2/C node: model/lockin_model.py:RcNodeStepper, fed by ADC_FB (uo_out[1]),
    driving ADC_CMP (ui_in[3]).
The ESP32 is played by tb_lib.SpiMaster on ui_in[2:0] / uo_out[0].
"""
import math
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

from tb_lib import CLK_NS, PinBus, SpiMaster, reset, start_clock, val
from lockin_model import PinAdc, RcNodeStepper, square_phase_deg
import regmap as R

F_CLK = 1e9 / CLK_NS
SCLK, MOSI, CS_N, ADC_CMP = 0, 1, 2, 3  # ui_in bits
MISO, ADC_FB, MOD, REF90, DONE = 0, 1, 2, 3, 4  # uo_out bits


def bit(sig, n):
    return (val(sig) >> n) & 1


class Bench:
    """LED + photodiode/TIA + RC node around the chip. Change the attributes at any time."""

    def __init__(self, dut, ui: PinBus, v_dc=1.0, v_step=0.0, delay=0, wobble_v=0.0, wobble_cycles=1):
        c = 100 / F_CLK / 5e3  # tau = 100 clocks with R1 = R2 = 10k
        self.adc = PinAdc(r1=10e3, r2=10e3, c=c, vth=1.4, noise_rms=2e-3)
        self.node = RcNodeStepper(self.adc, F_CLK, seed=3)
        self.dut, self.ui = dut, ui
        self.v_dc, self.v_step, self.wobble_v, self.wobble_cycles = v_dc, v_step, wobble_v, wobble_cycles
        self.led = deque([0] * (delay + 1), maxlen=delay + 1)
        self.cycle = 0
        cocotb.start_soon(self._run())

    async def _run(self):
        while True:
            await FallingEdge(self.dut.clk)
            uo = val(self.dut.uo_out)
            assert uo >> 5 == 0, f"uo_out[7:5] must be 0, got {uo:08b}"
            assert val(self.dut.uio_oe) == 0, "uio_oe must be 0"
            self.led.append((uo >> MOD) & 1)
            vin = (self.v_dc + self.v_step * self.led[0]
                   + self.wobble_v * math.sin(2 * math.pi * self.cycle / self.wobble_cycles))
            self.ui.set(ADC_CMP, self.node.step((uo >> ADC_FB) & 1, vin))
            self.cycle += 1


async def setup(dut, **bench_kw):
    await start_clock(dut.clk)
    dut.ena.value = 1
    dut.uio_in.value = 0
    ui = PinBus(dut.ui_in, init=1 << CS_N)  # CS_N idles high
    spi = SpiMaster(
        set_sclk=lambda v: ui.set(SCLK, v),
        set_mosi=lambda v: ui.set(MOSI, v),
        set_csn=lambda v: ui.set(CS_N, v),
        get_miso=lambda: bit(dut.uo_out, MISO),
    )
    await reset(dut, cycles=10)
    bench = Bench(dut, ui, **bench_kw)
    return spi, bench


async def measure(dut, spi, tw, m, settle_cycles=600, timeout_cycles=None):
    """What the ESP32 firmware does for one single-shot measurement."""
    await spi.write_le(R.addr_tw(), tw, 3)
    await spi.write_le(R.ADDR_M, m, 2)
    await spi.write(R.ADDR_CTRL, R.CTRL_RUN)
    await ClockCycles(dut.clk, settle_cycles)  # LED running, ADC loop settled
    await spi.write(R.ADDR_CTRL, R.CTRL_RUN | R.CTRL_START)
    status_running = await spi.read(R.ADDR_STATUS)
    timeout = timeout_cycles or int((m + 3) * (1 << 24) / tw) + 2000
    for _ in range(timeout):
        await FallingEdge(dut.clk)
        if bit(dut.uo_out, DONE):
            break
    else:
        raise AssertionError("DONE pin never rose")
    status_done = await spi.read(R.ADDR_STATUS)
    i = await spi.read_le(R.addr_i(), 3, signed=True)
    q = await spi.read_le(R.addr_q(), 3, signed=True)
    return i, q, status_running, status_done


@cocotb.test()
async def test_reset_pins(dut):
    """After reset: MISO 0, ADC_FB 1, MOD 0, REF90 0, DONE 0, uio all inputs."""
    await start_clock(dut.clk)
    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.ui_in.value = 1 << CS_N
    await reset(dut, cycles=10)
    await FallingEdge(dut.clk)
    assert val(dut.uo_out) == 0b0000_0010, f"uo_out = {val(dut.uo_out):08b} after reset, expected 00000010"
    assert val(dut.uio_oe) == 0 and val(dut.uio_out) == 0


@cocotb.test()
async def test_spi_registers(dut):
    spi, _ = await setup(dut)
    assert await spi.read(R.ADDR_ID) == R.ID_VALUE, "ID register"
    await spi.write_le(R.addr_tw(), 0xABCDEF, 3)
    await spi.write_le(R.ADDR_M, 0x1234, 2)
    assert await spi.read_le(R.addr_tw(), 3) == 0xABCDEF
    assert await spi.read_le(R.ADDR_M, 2) == 0x1234


@cocotb.test()
async def test_modulation_outputs(dut):
    """TW = 2^18 gives a 64-clock period on MOD; REF90 leads by 16 clocks; RUN = 0 stops both low."""
    spi, _ = await setup(dut)
    await spi.write_le(R.addr_tw(), 1 << 18, 3)
    await spi.write(R.ADDR_CTRL, R.CTRL_RUN)
    hist = []
    for _ in range(640):
        await FallingEdge(dut.clk)
        hist.append((bit(dut.uo_out, MOD), bit(dut.uo_out, REF90)))
    mod = [h[0] for h in hist]
    ref = [h[1] for h in hist]
    mod_rise = [k for k in range(1, len(mod)) if mod[k] and not mod[k - 1]]
    ref_rise = [k for k in range(1, len(ref)) if ref[k] and not ref[k - 1]]
    assert len(mod_rise) in (9, 10, 11), f"{len(mod_rise)} MOD periods in 640 clocks, expected 10"
    assert all(b - a == 64 for a, b in zip(mod_rise, mod_rise[1:])), "MOD period must be 64 clocks"
    assert all((k - 16) in ref_rise for k in mod_rise if k >= 16), "REF90 must rise 16 clocks before MOD"
    await spi.write(R.ADDR_CTRL, 0)
    await ClockCycles(dut.clk, 5)
    for _ in range(200):
        await FallingEdge(dut.clk)
        assert bit(dut.uo_out, MOD) == 0 and bit(dut.uo_out, REF90) == 0, "MOD/REF90 must be low with RUN = 0"


@cocotb.test()
async def test_lockin_measurement(dut):
    """Full chain: a 0.5 V LED step on 1.0 V DC, delayed 16 clocks (45 deg at 128 clocks/period).
    |I|+|Q| must give the step to within 10 %, and the phase must show the delay."""
    v_step, delay, tw, m = 0.5, 16, 1 << 17, 16
    spi, bench = await setup(dut, v_dc=1.0, v_step=v_step, delay=delay)
    i, q, st_run, st_done = await measure(dut, spi, tw, m)
    n = m * 128
    v_est = (abs(i) + abs(q)) / n / bench.adc.gain()
    phase = square_phase_deg(i, q)
    expected_phase = 360 * (delay + 2.5) / 128  # + ~2.5 clocks of sampling/synchroniser delay
    dut._log.info(f"I={i} Q={q} N={n}: step {v_est:.3f} V (true {v_step}), "
                  f"phase {phase:.1f} deg (expected ~{expected_phase:.1f})")
    assert st_run & R.STATUS_BUSY and not st_run & R.STATUS_DONE, f"STATUS during run = 0x{st_run:02x}"
    assert st_done & R.STATUS_DONE and not st_done & R.STATUS_BUSY, f"STATUS after done = 0x{st_done:02x}"
    assert abs(v_est - v_step) < 0.1 * v_step, f"amplitude {v_est:.3f} V, expected {v_step} V"
    assert abs(phase - expected_phase) < 10, f"phase {phase:.1f} deg, expected ~{expected_phase:.1f}"


@cocotb.test()
async def test_ambient_rejection(dut):
    """No LED coupling, but 1.0 V DC plus a slow 0.3 V 'ambient' wobble (one cycle per window).
    The result must stay near the modulator's quantisation floor: < 25 mV equivalent, i.e. more
    than 12x below the wobble. (Real windows are 1000x longer, so the floor is far lower.)"""
    tw, m = 1 << 15, 16
    spi, bench = await setup(dut, v_dc=1.0, v_step=0.0, wobble_v=0.3, wobble_cycles=16 * 512)
    i, q, _, _ = await measure(dut, spi, tw, m, settle_cycles=1200)
    n = m * 512
    v_est = (abs(i) + abs(q)) / n / bench.adc.gain()
    dut._log.info(f"I={i} Q={q} N={n}: equivalent amplitude {1e3 * v_est:.2f} mV")
    assert v_est < 0.025, f"ambient leaked through: {1e3 * v_est:.2f} mV equivalent"


@cocotb.test()
async def test_restart_gives_fresh_result(dut):
    """A second START clears I/Q and measures again; a bigger step gives a bigger result."""
    tw, m = 1 << 16, 16
    spi, bench = await setup(dut, v_dc=1.0, v_step=0.2)
    i1, q1, _, _ = await measure(dut, spi, tw, m)
    bench.v_step = 0.6
    i2, q2, _, _ = await measure(dut, spi, tw, m, settle_cycles=300)
    a1, a2 = abs(i1) + abs(q1), abs(i2) + abs(q2)
    dut._log.info(f"first {a1} counts, second {a2} counts")
    assert 2.4 < a2 / a1 < 3.8, f"ratio {a2 / a1:.2f}, expected ~3 (a stale, uncleared I/Q would give ~4.3)"

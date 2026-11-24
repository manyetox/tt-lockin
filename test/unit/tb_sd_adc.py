"""
tb_sd_adc.py -- cocotb unit tests for src/sd_adc.sv.

Two kinds of test:
  * digital: 2-cycle synchroniser latency, fb = ~b, reset values;
  * closed loop: a Python model of the external R1/R2/C node (model/lockin_model.py:
    RcNodeStepper) reads fb every cycle and drives cmp_in, so the DUT really is the
    quantiser + DAC of a sigma-delta modulator. The bit density must follow
    d = (Vin + VDD - 2*Vth)/VDD, and without noise the bitstream must equal the
    vectorised model bit for bit.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge, RisingEdge, Timer

from tb_lib import CLK_NS, CLK_PS, reset, start_clock, val
from lockin_model import PinAdc, RcNodeStepper, SdAdcSync

F_CLK = 1e9 / CLK_NS


def fast_adc(**kw) -> PinAdc:
    """R1 = R2 = 10k with C chosen so tau = 100 clocks at the simulated clock (keeps sims short)."""
    c = 100 / F_CLK / 5e3
    return PinAdc(r1=10e3, r2=10e3, c=c, **kw)


@cocotb.test()
async def test_reset_values(dut):
    """During and right after reset: b = 0, fb = 1, whatever cmp_in does."""
    await start_clock(dut.clk)
    dut.cmp_in.value = 1
    dut.rst_n.value = 0
    for _ in range(4):
        await FallingEdge(dut.clk)
        assert (val(dut.b), val(dut.fb)) == (0, 1), "reset values must be b = 0, fb = 1"
    await reset(dut, cmp_in=1)
    assert (val(dut.b), val(dut.fb)) == (0, 1)


@cocotb.test()
async def test_two_cycle_latency(dut):
    """cmp_in changes at random times in the low half of the clock (asynchronous to the edge).
    b must equal the value cmp_in had at rising edge k-2, and fb = ~b, every cycle."""
    await start_clock(dut.clk)
    await reset(dut, cmp_in=0)
    model = SdAdcSync()
    rng = random.Random(5)
    for k in range(600):
        level = rng.randrange(2) if rng.random() < 0.4 else model.pipe[0]
        # change somewhere inside the low phase, before the next rising edge
        await Timer(rng.randrange(1, CLK_PS // 2 - 1), unit="ps")
        dut.cmp_in.value = level
        model.clock(level)
        await FallingEdge(dut.clk)
        assert val(dut.b) == model.b, f"cycle {k}: b = {val(dut.b)}, expected {model.b}"
        assert val(dut.fb) == 1 - val(dut.b), f"cycle {k}: fb must be ~b"


async def closed_loop(dut, adc: PinAdc, vin: float, n: int, seed: int = 0):
    """Run the DUT inside the RC loop for n cycles. Returns the b bitstream (cycle by cycle)."""
    node = RcNodeStepper(adc, F_CLK, seed=seed)
    await reset(dut, cmp_in=0)
    bits = np.empty(n, np.uint8)
    for k in range(n):
        bits[k] = val(dut.b)  # b during this cycle
        dut.cmp_in.value = node.step(val(dut.fb), vin)  # node over this cycle, sampled at next edge
        await FallingEdge(dut.clk)
    return bits


@cocotb.test()
async def test_closed_loop_density(dut):
    """Bit density tracks Vin with gain 1/VDD (R1 = R2) and offset set by the threshold."""
    await start_clock(dut.clk)
    adc = fast_adc(vth=1.4, noise_rms=2e-3)
    for vin in (-0.3, 0.3, 1.0, 1.65, 2.2, 2.6):
        bits = await closed_loop(dut, adc, vin, 3000, seed=int(vin * 100) + 50)
        d = bits[500:].mean()
        dut._log.info(f"Vin = {vin:+.2f} V: density {d:.4f}, charge-balance prediction {adc.d_ideal(vin):.4f}")
        assert abs(d - adc.d_ideal(vin)) < 0.015, f"Vin={vin}: density {d:.4f} vs {adc.d_ideal(vin):.4f}"


@cocotb.test()
async def test_closed_loop_matches_model_bit_exact(dut):
    """Noise-free: the DUT's bitstream equals PinAdc.simulate() exactly. Any extra or
    missing flip-flop in the synchroniser shows up here as a mismatch."""
    await start_clock(dut.clk)
    adc = fast_adc(vth=1.3, noise_rms=0.0)
    for vin in (0.4, 1.1, 2.0):
        n = 1500
        bits = await closed_loop(dut, adc, vin, n)
        ref = adc.simulate(np.full(n, vin), F_CLK)
        bad = np.flatnonzero(bits != ref)
        assert len(bad) == 0, f"Vin={vin}: first mismatch at cycle {bad[0]} ({len(bad)} in total)"


@cocotb.test()
async def test_threshold_moves_offset_not_gain(dut):
    """Changing the pin threshold shifts the density but leaves the slope d(d)/d(Vin) = 1/VDD."""
    await start_clock(dut.clk)
    slopes = []
    for vth in (1.1, 1.6):
        adc = fast_adc(vth=vth, noise_rms=2e-3)
        d_lo = (await closed_loop(dut, adc, 0.6, 3000, seed=1))[500:].mean()
        d_hi = (await closed_loop(dut, adc, 1.6, 3000, seed=2))[500:].mean()
        slopes.append((d_hi - d_lo) / 1.0)
        dut._log.info(f"Vth = {vth}: d(0.6 V) = {d_lo:.4f}, d(1.6 V) = {d_hi:.4f}")
    for s in slopes:
        assert abs(s - 1 / 3.3) < 0.015, f"slope {s:.4f} per volt, expected {1/3.3:.4f}"

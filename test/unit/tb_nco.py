"""
tb_nco.py -- cocotb unit tests for src/nco.sv against model/lockin_model.py:Nco.

Run:  cd test/unit && python -m pytest -k nco -v      (or: make -C test units)
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # test/ for tb_lib

import cocotb
from cocotb.triggers import FallingEdge

from tb_lib import reset, start_clock, val
from lockin_model import W, Nco

PHASE_MOD = 1 << W


async def setup(dut, clock: bool = True):
    if clock:
        await start_clock(dut.clk)
    await reset(dut, en=0, tw=0)


def check(dut, model: Nco, where: str):
    got = (val(dut.ref_i), val(dut.ref_q), val(dut.period_tick))
    exp = (model.ref_i, model.ref_q, model.tick)
    assert got == exp, (
        f"{where}: (ref_i, ref_q, period_tick) = {got}, model expects {exp} "
        f"(model phase = 0x{model.phase:06x})"
    )


async def run_against_model(dut, model: Nco, stimulus, label: str):
    """stimulus: iterable of (en, tw), one per cycle. Inputs are applied at a falling edge,
    the model is clocked with them, and both are compared at the next falling edge."""
    for k, (en, tw) in enumerate(stimulus):
        dut.en.value = en
        dut.tw.value = tw
        model.clock(en, tw)
        await FallingEdge(dut.clk)
        check(dut, model, f"{label}, cycle {k}")


@cocotb.test()
async def test_reset_state(dut):
    """After reset: phase 0, so ref_i = ref_q = period_tick = 0, and nothing moves with en = 0."""
    await setup(dut)
    model = Nco()
    check(dut, model, "right after reset")
    await run_against_model(dut, model, [(0, 0x123456)] * 20, "en = 0")


@cocotb.test()
async def test_matches_model(dut):
    """Bit-exact against the model for edge-case and random tuning words, en toggling,
    and TW changing mid-run (phase must stay continuous)."""
    await setup(dut)
    model = Nco()
    rng = random.Random(2026)
    tws = [1, 2, 3, 1 << 18, (1 << 23) - 1, 1 << 23, (1 << 23) + 1, PHASE_MOD - 1,
           0x2AAAAA, 0x155555] + [rng.randrange(1, PHASE_MOD) for _ in range(6)]
    for tw in tws:
        stim = [(1 if rng.random() < 0.9 else 0, tw) for _ in range(150)]
        await run_against_model(dut, model, stim, f"tw = 0x{tw:06x}")


@cocotb.test()
async def test_frequency(dut):
    """f_mod = TW * f_clk / 2^24: count period_ticks and ref_i rising edges over many cycles."""
    await start_clock(dut.clk)
    for tw in (1 << 18, (1 << 18) + 12345, 3 * (1 << 16) + 7, 1 << 22):
        await setup(dut, clock=False)
        n = 4000
        ticks = rises = 0
        prev = 0
        dut.en.value = 1
        dut.tw.value = tw
        for _ in range(n):
            await FallingEdge(dut.clk)
            ticks += val(dut.period_tick)
            r = val(dut.ref_i)
            rises += int(r and not prev)
            prev = r
        expected = n * tw / PHASE_MOD
        assert abs(ticks - expected) <= 1, f"tw={tw}: {ticks} ticks, expected {expected:.2f}"
        assert abs(rises - expected) <= 1, f"tw={tw}: {rises} ref_i rising edges, expected {expected:.2f}"


@cocotb.test()
async def test_quadrature_leads_by_quarter_period(dut):
    """With TW = 2^18 one period is exactly 64 clocks. ref_q must rise 16 clocks BEFORE
    ref_i (it leads by 90 deg) and period_tick must coincide with ref_i falling."""
    await setup(dut)
    dut.en.value = 1
    dut.tw.value = 1 << 18
    hist = []
    for _ in range(64 * 4):
        await FallingEdge(dut.clk)
        hist.append((val(dut.ref_i), val(dut.ref_q), val(dut.period_tick)))
    ri = [h[0] for h in hist]
    rq = [h[1] for h in hist]
    tk = [h[2] for h in hist]
    i_rise = [k for k in range(1, len(ri)) if ri[k] and not ri[k - 1]]
    q_rise = [k for k in range(1, len(rq)) if rq[k] and not rq[k - 1]]
    assert i_rise and q_rise, "no edges seen"
    for k in i_rise:
        assert (k - 16) in q_rise, f"ref_i rose at {k} but ref_q did not rise 16 cycles earlier"
    for k in range(1, len(tk)):
        if tk[k]:
            assert ri[k - 1] == 1 and ri[k] == 0, f"period_tick at {k} is not where ref_i falls"
    assert sum(tk) == 4 or sum(tk) == 3, f"expected one tick per 64 cycles, got {sum(tk)} in 256"

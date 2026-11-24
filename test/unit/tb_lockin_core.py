"""
tb_lockin_core.py -- cocotb unit tests for src/lockin_core.sv.

The references and ticks come from the NCO *model*, so this module can be tested
before nco.sv exists. Every cycle the DUT is compared with model/lockin_model.py:LockinCore:
  * busy and done exactly, every cycle;
  * acc_i / acc_q exactly whenever the model is IDLE, ARMED or DONE
    (their values during RUN are not part of the contract).
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge

from tb_lib import reset, start_clock, sval, val
from lockin_model import ACC_W, PHASE_MOD, LockinCore, nco_arrays, reference_imbalance


async def setup(dut):
    await start_clock(dut.clk)
    await reset(dut, start=0, m_periods=0, b=0, ref_i=0, ref_q=0, period_tick=0)


def check(dut, model: LockinCore, where: str):
    got_ctl = (val(dut.busy), val(dut.done))
    exp_ctl = (model.busy, model.done)
    assert got_ctl == exp_ctl, f"{where}: (busy, done) = {got_ctl}, model expects {exp_ctl} [state {model.state}]"
    if model.state != model.RUN:
        got = (sval(dut.acc_i, ACC_W), sval(dut.acc_q, ACC_W))
        exp = (model.i, model.q)
        assert got == exp, f"{where}: (I, Q) = {got}, model expects {exp} [state {model.state}]"


async def run(dut, model, tw, m, b, starts, n=None, phase0=0, label=""):
    """Drive one stretch of cycles. b: array/list of bits per cycle (or callable(k, ref_i, ref_q)).
    starts: set of cycle indices where start = 1. Returns the model after the last cycle."""
    if n is None:
        n = len(b)
    _, ri, rq, tk = nco_arrays(tw, n, phase0)
    for k in range(n):
        bk = int(b(k, ri[k], rq[k])) if callable(b) else int(b[k])
        st = 1 if k in starts else 0
        dut.start.value = st
        dut.m_periods.value = m
        dut.b.value = bk
        dut.ref_i.value = int(ri[k])
        dut.ref_q.value = int(rq[k])
        dut.period_tick.value = int(tk[k])
        model.clock(st, m, bk, int(ri[k]), int(rq[k]), int(tk[k]))
        await FallingEdge(dut.clk)
        check(dut, model, f"{label} cycle {k}")
    return model


def cycles_for(tw, m, extra_periods=3):
    return int((m + extra_periods) * PHASE_MOD / tw) + 8


@cocotb.test()
async def test_in_phase_signal(dut):
    """b = ref_i (signal exactly in phase): I = +N, Q = 0 exactly (TW = 2^18, 64 clk/period)."""
    await setup(dut)
    tw, m = 1 << 18, 8
    model = LockinCore()
    await run(dut, model, tw, m, lambda k, ri, rq: ri, starts={5}, n=cycles_for(tw, m), label="in-phase")
    n = m * 64
    assert model.done and (model.i, model.q) == (n, 0), (model.i, model.q)


@cocotb.test()
async def test_quadrature_signs(dut):
    """Signal 90 deg AHEAD of ref_i (b = ref_q):   I = 0, Q = +N.
    Signal 90 deg BEHIND ref_i (b = ref_i delayed 16 clk): I = 0, Q = -N.
    The minus sign comes from ref_q = p[23]^p[22] leading ref_i (see src/nco.sv, item 4)."""
    await setup(dut)
    tw, m = 1 << 18, 8
    n = m * 64
    model = LockinCore()
    await run(dut, model, tw, m, lambda k, ri, rq: rq, starts={3}, n=cycles_for(tw, m), label="lead")
    assert (model.i, model.q) == (0, n), f"leading signal gave {(model.i, model.q)}"

    _, ri, _, _ = nco_arrays(tw, cycles_for(tw, m) + 16)
    lagging = np.concatenate([np.zeros(16, np.uint8), ri])  # ref_i delayed by 16 clocks
    await run(dut, model, tw, m, lagging, starts={3}, n=cycles_for(tw, m), label="lag")
    assert (model.i, model.q) == (0, -n), f"lagging signal gave {(model.i, model.q)}"


@cocotb.test()
async def test_constant_b_cancels_for_power_of_two_tw(dut):
    """Constant b (pure DC) gives exactly I = Q = 0 when TW is a power of two."""
    await setup(dut)
    for tw in (1 << 17, 1 << 18, 1 << 19):
        for level in (0, 1):
            m = 5
            await reset(dut, start=0)
            model = LockinCore()
            await run(dut, model, tw, m, lambda k, ri, rq, lv=level: lv, starts={2},
                      n=cycles_for(tw, m), label=f"tw={tw} b={level}")
            assert model.done and (model.i, model.q) == (0, 0), f"tw={tw}, b={level}: {(model.i, model.q)}"


@cocotb.test()
async def test_constant_b_leaks_for_odd_period(dut):
    """Characterisation, not a bug: TW = 2^24/63 makes every period 63 clocks (odd), so the
    high and low halves differ by one sample and a constant b leaks about +/-M counts.
    The DUT must match the model exactly; the test also documents the size of the leak."""
    await setup(dut)
    tw, m = round(PHASE_MOD / 63), 20
    model = LockinCore()
    await run(dut, model, tw, m, lambda k, ri, rq: 1, starts={1}, n=cycles_for(tw, m), label="odd period")
    leak = reference_imbalance(tw, m)
    dut._log.info(f"TW={tw}: constant b=1 gives I={model.i}, Q={model.q}; window imbalance {leak}")
    assert abs(model.i) >= m // 2, "expected a visible leak for an odd period"


@cocotb.test()
async def test_m_zero_and_m_one(dut):
    """M = 0 ends at the first tick with an empty window; M = 1 integrates exactly one period."""
    await setup(dut)
    tw = 1 << 18
    model = LockinCore()
    await run(dut, model, tw, 0, lambda k, ri, rq: ri, starts={4}, n=200, label="M=0")
    assert model.done and (model.i, model.q) == (0, 0)
    await run(dut, model, tw, 1, lambda k, ri, rq: ri, starts={1}, n=200, phase0=123, label="M=1")
    assert model.done and model.i == 64, model.i


@cocotb.test()
async def test_start_rules(dut):
    """start in the same cycle as a tick: that tick is ignored. start during RUN restarts.
    After done, I/Q stay frozen while b and ticks keep coming."""
    await setup(dut)
    tw, m = 1 << 18, 3
    _, _, _, tk = nco_arrays(tw, 400)
    first_tick = int(np.flatnonzero(tk)[0])
    rng = random.Random(7)
    bits = [rng.randrange(2) for _ in range(1200)]
    model = LockinCore()
    # start exactly on a tick, then restart in the middle of the run, then let it finish
    await run(dut, model, tw, m, bits, starts={first_tick, first_tick + 64 + 20}, n=1200,
              label="start rules")
    assert model.done
    frozen = (model.i, model.q)
    await run(dut, model, tw, m, [1 - x for x in bits], starts=set(), n=300, label="frozen")
    assert (model.i, model.q) == frozen


@cocotb.test()
async def test_random_bit_exact(dut):
    """Random TW, M, start time and biased random b: exact match with the model,
    including the cycle on which done rises."""
    await setup(dut)
    rng = random.Random(1234)
    for trial in range(12):
        tw = rng.randrange(1 << 16, 1 << 20)
        m = rng.randrange(1, 6)
        p_one = rng.random()
        start_at = rng.randrange(0, int(PHASE_MOD / tw))
        n = cycles_for(tw, m) + start_at
        bits = [int(rng.random() < p_one) for _ in range(n)]
        await reset(dut, start=0)
        model = LockinCore()
        await run(dut, model, tw, m, bits, starts={start_at}, n=n, phase0=rng.randrange(PHASE_MOD),
                  label=f"trial {trial} tw={tw} m={m}")
        assert model.done, f"trial {trial}: never finished"


@cocotb.test()
async def test_random_b_is_small(dut):
    """Fair random b carries no signal: |I|, |Q| stay within 4*sqrt(N)."""
    await setup(dut)
    tw, m = 1 << 17, 20
    rng = random.Random(99)
    n = cycles_for(tw, m)
    model = LockinCore()
    await run(dut, model, tw, m, [rng.randrange(2) for _ in range(n)], starts={0}, n=n, label="noise")
    N = m * 128
    assert abs(model.i) <= 4 * math.sqrt(N) and abs(model.q) <= 4 * math.sqrt(N), (model.i, model.q, N)

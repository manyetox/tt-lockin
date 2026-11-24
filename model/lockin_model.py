"""
lockin_model.py -- reference model for tt_um_manyetox_lockin.

Two layers
==========
1. BIT-EXACT DIGITAL MODEL: Nco, SdAdcSync, LockinCore and vectorised helpers.
   The cocotb tests drive the RTL and this model with the same inputs and require
   identical outputs every cycle. The cycle conventions below ARE the contract.
   The same text is in the header of each src/*.sv stub. If RTL and model
   disagree, settle it against that contract, not against whichever one is newer.

2. PHYSICAL MODEL of the pin-based sigma-delta ADC (RC node, pin threshold,
   hysteresis, noise) and of the optical signal (LED, ambient light, 100 Hz
   flicker, TIA bandwidth). design_sweep.py uses it to choose R, C, f_mod and M.
   The closed-loop cocotb tests use it too.

Cycle conventions
=================
* "Cycle k" runs from rising edge k-1 to rising edge k. Inputs for cycle k are
  stable before edge k. Registers load at edge k and show the new value in cycle k+1.

* NCO (W = 24):
      phase[0] = 0 after reset
      if en[k]: phase[k+1] = (phase[k] + tw[k]) mod 2^W     (else phase holds)
      ref_i[k] = phase[k][W-1]
      ref_q[k] = phase[k][W-1] XOR phase[k][W-2]          -> ref_q LEADS ref_i by 90 deg
      period_tick[k+1] = en[k] AND carry_out(phase[k] + tw[k])
  So period_tick is high in exactly the cycle that holds the first phase sample of a
  new reference period.

* sd_adc: b[k] = cmp_in sampled at edge k-2 (2-FF synchroniser), fb[k] = NOT b[k].

* lockin_core: see LockinCore.clock(). Each sample adds +1 if b == ref (else -1).
  The window is the samples from the first period_tick AFTER the start cycle up to,
  but not including, the M-th tick after that. So it is exactly M whole periods.

Sign conventions you will meet again in the interview
=====================================================
* With ref_q = phase[23] ^ phase[22], ref_q leads ref_i. A signal delayed by 90 deg
  relative to ref_i therefore gives Q = -N (not +N), and positive delay gives negative Q.
  square_phase_deg() below converts (I, Q) into a delay with "positive = signal lags".
* |I| + |Q| = N * (step height in density units) holds EXACTLY only when the signal itself
  is a square wave (LED switched on/off, TIA fast compared with f_mod). For a sinusoidal
  signal use amplitude_sine().
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

try:  # numba makes the physical simulation ~50x faster; everything works without it
    from numba import njit

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover
    HAVE_NUMBA = False

    def njit(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda f: f


W = 24  # phase accumulator width
REF_Q_XNOR = False  # False: ref_q = p[23] ^ p[22] (leads ref_i).  True: XNOR (lags ref_i,
#                     textbook sign: delayed signal => +Q). Must match src/nco.sv.
PHASE_MOD = 1 << W
ACC_W = 24  # I/Q accumulator width (signed)
M_W = 16  # width of the "number of periods" register


# =============================================================================
# Small helpers
# =============================================================================
def to_signed(value: int, bits: int) -> int:
    """Interpret an unsigned bit pattern as two's complement."""
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def f_mod_from_tw(tw: int, f_clk: float, width: int = W) -> float:
    """Average NCO output frequency: f_mod = TW * f_clk / 2^W."""
    return tw * f_clk / (1 << width)


def tw_from_f_mod(f_mod: float, f_clk: float, width: int = W) -> int:
    return int(round(f_mod * (1 << width) / f_clk))


def is_power_of_two(x: int) -> bool:
    return x > 0 and (x & (x - 1)) == 0


def max_m_periods(tw: int, width: int = W, acc_w: int = ACC_W) -> int:
    """Largest M for which |I|, |Q| <= N can never overflow the signed accumulator.

    A window of M periods holds at most N = ceil(M * 2^W / TW) samples. Requiring
    N <= 2^(ACC_W-1) - 1 gives roughly M < TW/2. This rule is clock-independent:
    "integrate fewer periods than half the tuning word".
    """
    limit = (1 << (acc_w - 1)) - 1
    m = (limit - 1) * tw // (1 << width)
    while m > 0 and -(-(m * (1 << width)) // tw) > limit:  # ceil division
        m -= 1
    return min(m, (1 << M_W) - 1)


# =============================================================================
# Cycle-accurate models (one Python object per RTL module). Used by cocotb tests.
# Usage pattern per cycle:  read .outputs  ->  call .clock(inputs of this cycle)
# =============================================================================
class Nco:
    def __init__(self, width: int = W):
        self.width = width
        self.mask = (1 << width) - 1
        self.reset()

    def reset(self) -> None:
        self.phase = 0
        self.tick = 0

    @property
    def ref_i(self) -> int:
        return (self.phase >> (self.width - 1)) & 1

    @property
    def ref_q(self) -> int:
        x = ((self.phase >> (self.width - 1)) ^ (self.phase >> (self.width - 2))) & 1
        return x ^ int(REF_Q_XNOR)

    def clock(self, en: int, tw: int) -> None:
        """One rising edge. en and tw are the values during the cycle ending at this edge."""
        if en:
            s = self.phase + (tw & self.mask)
            self.tick = s >> self.width  # carry out
            self.phase = s & self.mask
        else:
            self.tick = 0


class SdAdcSync:
    """The digital part of sd_adc: an N-stage synchroniser and the feedback inverter."""

    def __init__(self, stages: int = 2):
        self.stages = stages
        self.reset()

    def reset(self) -> None:
        self.pipe = [0] * self.stages

    @property
    def b(self) -> int:
        return self.pipe[-1]

    @property
    def fb(self) -> int:
        return 1 - self.pipe[-1]

    def clock(self, cmp_in: int) -> None:
        self.pipe = [cmp_in & 1] + self.pipe[:-1]


class LockinCore:
    IDLE, ARMED, RUN, DONE = "IDLE", "ARMED", "RUN", "DONE"

    def __init__(self, acc_w: int = ACC_W):
        self.acc_w = acc_w
        self.mask = (1 << acc_w) - 1
        self.reset()

    def reset(self) -> None:
        self.state = self.IDLE
        self.acc_i = 0  # stored as unsigned bit pattern, like the RTL
        self.acc_q = 0
        self.remaining = 0

    # ---- outputs -----------------------------------------------------------
    @property
    def busy(self) -> int:
        return int(self.state in (self.ARMED, self.RUN))

    @property
    def done(self) -> int:
        return int(self.state == self.DONE)

    @property
    def i(self) -> int:
        return to_signed(self.acc_i, self.acc_w)

    @property
    def q(self) -> int:
        return to_signed(self.acc_q, self.acc_w)

    # ---- one rising edge ---------------------------------------------------
    def _accumulate(self, b: int, ref_i: int, ref_q: int) -> None:
        self.acc_i = (self.acc_i + (1 if b == ref_i else -1)) & self.mask
        self.acc_q = (self.acc_q + (1 if b == ref_q else -1)) & self.mask

    def clock(self, start: int, m_periods: int, b: int, ref_i: int, ref_q: int, tick: int) -> None:
        """
        start      1-cycle request. Clears I/Q and done, arms. Has priority over everything;
                   a tick in the same cycle as start is ignored.
        ARMED      waits for the next tick. That tick's sample is the first one integrated.
                   If m_periods == 0 the run ends right there (empty window).
        RUN        integrates every sample. At each tick: one more whole period is complete;
                   after the M-th one, go to DONE WITHOUT integrating that tick's sample
                   (it belongs to the next period).
        DONE       accumulators frozen until the next start.
        m_periods  must be held constant from start until done.
        """
        if start:
            self.acc_i = self.acc_q = 0
            self.state = self.ARMED
            return
        if self.state == self.ARMED:
            if tick:
                if m_periods == 0:
                    self.state = self.DONE
                    return
                self.remaining = m_periods
                self.state = self.RUN
                self._accumulate(b, ref_i, ref_q)
            return
        if self.state == self.RUN:
            if tick:
                self.remaining -= 1
                if self.remaining == 0:
                    self.state = self.DONE
                    return
            self._accumulate(b, ref_i, ref_q)


# =============================================================================
# Vectorised digital model (for analysis and for computing expected test results)
# =============================================================================
def nco_arrays(tw: int, n: int, phase0: int = 0, width: int = W):
    """phase, ref_i, ref_q, tick for n cycles of a free-running NCO starting at phase0."""
    k = np.arange(n, dtype=np.int64)
    unwrapped = np.int64(phase0) + k * np.int64(tw)
    phase = unwrapped & ((1 << width) - 1)
    ref_i = ((phase >> (width - 1)) & 1).astype(np.uint8)
    ref_q = ((((phase >> (width - 1)) ^ (phase >> (width - 2))) & 1) ^ int(REF_Q_XNOR)).astype(np.uint8)
    tick = np.zeros(n, dtype=np.uint8)
    tick[1:] = (unwrapped[1:] >> width) != (unwrapped[:-1] >> width)
    return phase, ref_i, ref_q, tick


def lockin_window(b, ref_i, ref_q, tick, m: int, arm_cycle: int = -1) -> dict:
    """Vectorised LockinCore. start was asserted in cycle arm_cycle.

    Returns I, Q, the window [start, stop) in cycles, N = stop - start, and done_cycle
    (the first cycle in which the done output reads 1, i.e. stop + 1).
    """
    ticks = np.flatnonzero(tick)
    ticks = ticks[ticks > arm_cycle]
    if len(ticks) < m + 1:
        raise ValueError(f"only {len(ticks)} ticks after the arm cycle, need {m + 1}")
    start, stop = int(ticks[0]), int(ticks[m])
    sl = slice(start, stop)
    i = int(np.where(b[sl] == ref_i[sl], 1, -1).sum())
    q = int(np.where(b[sl] == ref_q[sl], 1, -1).sum())
    return dict(I=i, Q=q, start=start, stop=stop, N=stop - start, done_cycle=stop + 1)


def reference_imbalance(tw: int, m: int, phase0: int = 0) -> tuple[int, int]:
    """(I, Q) produced by a CONSTANT b = 1 over one M-period window.

    This is the DC leakage of the "integrate whole periods" scheme. It is exactly
    (0, 0) when TW is a power of two, because then every half and quarter period is an
    integer number of samples. Otherwise a whole period is a non-integer number of
    clocks, the high and low halves differ by up to one sample per period, and the
    sum can grow with M. That matters when ambient light is strong (see design_sweep.py).
    """
    period = PHASE_MOD / tw
    n = int((m + 3) * period) + 4
    _, ri, rq, tk = nco_arrays(tw, n, phase0)
    r = lockin_window(np.ones(n, np.uint8), ri, rq, tk, m)
    return r["I"], r["Q"]


def amplitude_square(i: int, q: int, n: int) -> float:
    """Peak-to-peak step of a SQUARE signal, in b-density units. Exact for square x square."""
    return (abs(i) + abs(q)) / n


def amplitude_sine(i: int, q: int, n: int) -> float:
    """Peak amplitude of a SINUSOIDAL signal, in b-density units (square references
    see the fundamental with weight 2/pi in this normalisation)."""
    return math.pi / 2 * math.hypot(i, q) / n


def square_phase_deg(i: int, q: int) -> float:
    """Delay of a square signal relative to ref_i, in degrees, positive = signal lags.

    For square x square the correlation is a triangle, so (I, Q)/(|I|+|Q|) moves along
    a diamond |x| + |y| = 1. Position on the diamond is linear in delay, which makes
    this the exact square-wave counterpart of atan2.
    """
    s = abs(i) + abs(q)
    if s == 0:
        return float("nan")
    x, y = i / s, q / s
    if REF_Q_XNOR:  # lagging ref_q flips the sign of Q
        y = -y
    if x >= 0:
        frac = -y / 4
    elif y <= 0:
        frac = (2 + y) / 4
    else:
        frac = (y - 2) / 4
    return 360.0 * frac


# =============================================================================
# Physical model: the pin ADC
# =============================================================================
@njit(cache=True)
def _simulate_pin_adc(vin, a, g1, g2, gp, vdd, vth, hyst, noise, stages, v0, seed):
    np.random.seed(seed)
    n = vin.shape[0]
    b = np.empty(n, np.uint8)
    pipe = np.zeros(stages, np.uint8)
    g = g1 + g2 + gp
    v = v0
    raw = 0
    for k in range(n):
        bk = pipe[stages - 1]
        b[k] = bk
        vfb = vdd * (1 - bk)  # fb = NOT b, driven for the whole of cycle k
        vinf = (vin[k] * g1 + vfb * g2 + vdd * gp) / g
        v = vinf + (v - vinf) * a  # exact RC solution over one clock period
        vn = v + noise * np.random.standard_normal() if noise > 0.0 else v
        thr = vth - 0.5 * hyst if raw == 1 else vth + 0.5 * hyst
        raw = 1 if vn > thr else 0  # pin value sampled at edge k
        for s in range(stages - 1, 0, -1):
            pipe[s] = pipe[s - 1]
        pipe[0] = raw
    return b


@dataclass
class PinAdc:
    """External R1/R2/C network around a digital input pin used as a 1-bit quantiser.

        Vin --R1--+--R2-- fb pin (= NOT b, 0 or VDD)
                  |
                  C          node --> cmp input pin (threshold vth, optional hysteresis)
                  |
                 GND
    """

    r1: float = 10e3  # signal resistor, Vin -> node
    r2: float = 10e3  # feedback resistor, fb pin -> node
    c: float = 10e-9  # node capacitor to GND
    vdd: float = 3.3  # I/O supply = fb high level
    vth: float = 1.4  # pin switching threshold: unknown, only bounded by VIL/VIH
    hyst: float = 0.0  # input hysteresis (Schmitt), volts
    noise_rms: float = 0.0  # pin-referred noise per sample, volts
    r_pull: float = math.inf  # weak pull-up to VDD on the input pin (keep it OFF)
    stages: int = 2  # synchroniser depth

    @property
    def tau(self) -> float:
        """Node time constant: C times R1 || R2 (|| R_pull)."""
        g = 1 / self.r1 + 1 / self.r2 + (0 if math.isinf(self.r_pull) else 1 / self.r_pull)
        return self.c / g

    def gain(self) -> float:
        """d(density)/d(Vin) = R2 / (R1 * VDD). Independent of the threshold."""
        return self.r2 / (self.r1 * self.vdd)

    def d_ideal(self, vin: float) -> float:
        """Long-run density of b predicted by charge balance at the node (before clipping).

        Average node voltage sits at vth, so the average currents into C must cancel:
            (Vin - Vth)/R1 + (VDD*(1-d) - Vth)/R2 + (VDD - Vth)/Rpull = 0
        The threshold only moves the offset (the lock-in cancels it); the gain is R2/(R1*VDD).
        """
        gp = 0.0 if math.isinf(self.r_pull) else self.r2 / self.r_pull
        return (
            1
            - self.vth / self.vdd
            + (self.r2 / self.r1) * (vin - self.vth) / self.vdd
            + gp * (self.vdd - self.vth) / self.vdd
        )

    def input_range(self) -> tuple[float, float]:
        """Vin for density 0 and 1. Outside this range the modulator saturates (b stuck)."""
        g = self.gain()
        d0 = self.d_ideal(0.0)
        return ((0 - d0) / g, (1 - d0) / g)

    def step_per_clock(self, f_clk: float) -> float:
        """Node voltage change in one clock when fb is fighting the input (rough ripple scale)."""
        return (self.vdd / 2) / (self.r2 * self.c) / f_clk

    def simulate(self, vin: np.ndarray, f_clk: float, seed: int = 0, v0: float | None = None) -> np.ndarray:
        """Bit stream b[k] for an input voltage sampled once per clock (vin[k] = value in cycle k)."""
        g1, g2 = 1 / self.r1, 1 / self.r2
        gp = 0.0 if math.isinf(self.r_pull) else 1 / self.r_pull
        a = math.exp(-(g1 + g2 + gp) / (self.c * f_clk))
        v0 = self.vth if v0 is None else v0
        return _simulate_pin_adc(
            np.ascontiguousarray(vin, dtype=np.float64),
            a, g1, g2, gp, self.vdd, self.vth, self.hyst, self.noise_rms, self.stages, v0, seed,
        )


class RcNodeStepper:
    """Cycle-by-cycle version of PinAdc for cocotb: call step(fb, vin) once per clock and
    drive the returned comparator value onto the DUT's input pin."""

    def __init__(self, adc: PinAdc, f_clk: float, seed: int = 0):
        self.adc = adc
        g1, g2 = 1 / adc.r1, 1 / adc.r2
        gp = 0.0 if math.isinf(adc.r_pull) else 1 / adc.r_pull
        self.g1, self.g2, self.gp = g1, g2, gp
        self.a = math.exp(-(g1 + g2 + gp) / (adc.c * f_clk))
        self.v = adc.vth
        self.raw = 0
        self.rng = np.random.default_rng(seed)

    def step(self, fb: int, vin: float) -> int:
        adc = self.adc
        vfb = adc.vdd * (fb & 1)
        vinf = (vin * self.g1 + vfb * self.g2 + adc.vdd * self.gp) / (self.g1 + self.g2 + self.gp)
        self.v = vinf + (self.v - vinf) * self.a
        vn = self.v + (adc.noise_rms * self.rng.standard_normal() if adc.noise_rms > 0 else 0.0)
        thr = adc.vth - 0.5 * adc.hyst if self.raw else adc.vth + 0.5 * adc.hyst
        self.raw = 1 if vn > thr else 0
        return self.raw


# =============================================================================
# Physical model: the optical scene seen by the photodiode + TIA
# =============================================================================
@njit(cache=True)
def _first_order_lowpass(x, alpha):
    y = np.empty_like(x)
    acc = x[0]
    for k in range(x.shape[0]):
        acc += alpha * (x[k] - acc)
        y[k] = acc
    return y


@dataclass
class OpticalScene:
    """TIA output voltage = dark + ambient (with mains flicker) + LED signal, band-limited."""

    v_dark: float = 0.20  # TIA output in the dark, volts
    v_ambient: float = 0.80  # mean ambient-light contribution, volts
    flicker: float = 0.10  # peak-ish flicker depth as a fraction of ambient
    mains_hz: float = 50.0  # UK mains; lamps flicker at 2x this
    v_signal: float = 0.010  # LED-on minus LED-off at the TIA output, volts
    tia_bw_hz: float = 100e3  # TIA -3 dB bandwidth (first order); math.inf for ideal
    delay_cycles: int = 0  # LED -> TIA delay, in clock cycles
    noise_rms: float = 0.0  # white noise per clock sample at the TIA output, volts

    def vin(self, led: np.ndarray, f_clk: float, seed: int = 0, t0: float = 0.0) -> np.ndarray:
        n = led.shape[0]
        t = t0 + np.arange(n) / f_clk
        rect = np.abs(np.sin(2 * np.pi * self.mains_hz * t))  # full-wave: 100 Hz + harmonics
        ambient = self.v_ambient * (1 + self.flicker * (rect / (2 / np.pi) - 1))
        led_d = np.roll(led.astype(np.float64), self.delay_cycles)
        if self.delay_cycles:
            led_d[: self.delay_cycles] = 0
        v = self.v_dark + ambient + self.v_signal * led_d
        if math.isfinite(self.tia_bw_hz):
            alpha = 1 - math.exp(-2 * math.pi * self.tia_bw_hz / f_clk)
            v = _first_order_lowpass(v, alpha)
        if self.noise_rms > 0:
            v = v + np.random.default_rng(seed + 7919).normal(0, self.noise_rms, n)
        return v


def measure(scene: OpticalScene, adc: PinAdc, f_clk: float, tw: int, m: int,
            seed: int = 0, settle_periods: int = 3) -> dict:
    """End-to-end simulation of one single-shot measurement.

    NCO free-runs from reset and drives the LED with ref_i. The ESP32 issues start after
    settle_periods periods, and the core integrates M whole periods. The mains phase is
    random per seed. Returns the raw I, Q, N and the ESP32-side estimates.
    """
    period = PHASE_MOD / tw
    n = int((settle_periods + m + 2) * period) + 8
    _, ref_i, ref_q, tick = nco_arrays(tw, n)
    t0 = np.random.default_rng(seed).uniform(0, 1 / scene.mains_hz)
    vin = scene.vin(ref_i, f_clk, seed, t0=t0)
    b = adc.simulate(vin, f_clk, seed)
    r = lockin_window(b, ref_i, ref_q, tick, m, arm_cycle=int(settle_periods * period))
    amp_density = amplitude_square(r["I"], r["Q"], r["N"])
    r.update(
        v_signal_est=amp_density / adc.gain(),  # back to volts at the TIA output
        phase_deg=square_phase_deg(r["I"], r["Q"]),
        density_mean=float(b[r["start"]: r["stop"]].mean()),
        saturated=bool(b[r["start"]: r["stop"]].min() == b[r["start"]: r["stop"]].max()),
    )
    return r

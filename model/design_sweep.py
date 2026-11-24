#!/usr/bin/env python3
"""
design_sweep.py -- pick f_clk, f_mod (TW), M, R and C with numbers instead of guesses.

    python3 design_sweep.py            # full run, ~1 min with numba
    python3 design_sweep.py --quick    # fewer seeds
    python3 design_sweep.py --csv out  # also write CSVs (easy to plot in MATLAB)

Sections
  1. Frequency plan: power-of-two tuning words, max integration per clock choice.
  2. DC leakage: why TW should be a power of two.
  3. ADC network: time constant, ripple scale and usable input range vs pin threshold.
  4. Monte Carlo: amplitude error vs M for several R/C, with and without pin hysteresis,
     under room light with 100 Hz flicker.
  5. Flicker: why the integration time should be a multiple of 10 ms.
  6. Clock choice: 10 MHz vs 50 MHz at the same integration time.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import time

import numpy as np

from lockin_model import (
    PHASE_MOD, OpticalScene, PinAdc, f_mod_from_tw, is_power_of_two,
    max_m_periods, measure, reference_imbalance,
)


def table(rows, headers):
    cols = list(zip(*([headers] + [[str(c) for c in r] for r in rows])))
    widths = [max(len(c) for c in col) for col in cols]
    line = "  ".join(h.rjust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).rjust(w) for c, w in zip(r, widths)))
    print()


def write_csv(path, rows, headers):
    if path:
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(rows)


def section1(csv_dir):
    print("=" * 78)
    print("1. FREQUENCY PLAN  (power-of-two TW; N < 2^23 => M <= ~TW/2)")
    print("=" * 78)
    rows = []
    for f_clk in (10e6, 50e6):
        for k in range(8, 15):
            tw = 1 << k
            f = f_mod_from_tw(tw, f_clk)
            if not 400 <= f <= 12_000:
                continue
            m_max = max_m_periods(tw)
            rows.append([f"{f_clk/1e6:.0f} MHz", f"2^{k}", f"{f:8.1f}", PHASE_MOD // tw,
                         m_max, f"{m_max / f:6.3f}"])
    hdr = ["f_clk", "TW", "f_mod [Hz]", "clocks/period", "M_max", "T_int max [s]"]
    table(rows, hdr)
    write_csv(csv_dir and os.path.join(csv_dir, "freq_plan.csv"), rows, hdr)
    print("Max integration is 2^23 clocks either way: 0.84 s at 10 MHz, 0.17 s at 50 MHz.")
    print("Longer averages = several single-shot runs averaged on the ESP32.\n")


def section2(csv_dir):
    print("=" * 78)
    print("2. DC LEAKAGE of 'integrate whole periods' (constant b = 1, M = 1000)")
    print("=" * 78)
    rows = []
    f_clk = 10e6
    for label, tw in [("2^12 (2441.4 Hz)", 4096), ("2441 Hz nearest", 4095),
                      ("1 kHz", 1678), ("2 kHz", 3355), ("5 kHz", 8389),
                      ("odd period 4095 clk", 4097)]:
        i, q = reference_imbalance(tw, 1000)
        n = 1000 * PHASE_MOD / tw
        leak = max(abs(i), abs(q)) / n
        rej = "exact 0" if leak == 0 else f"{20 * math.log10(leak):6.1f} dB"
        rows.append([label, tw, "yes" if is_power_of_two(tw) else "no", f"{PHASE_MOD / tw:9.3f}",
                     i, q, rej])
    hdr = ["case", "TW", "pow2", "clocks/period", "I(b=1)", "Q(b=1)", "DC leak / N"]
    table(rows, hdr)
    write_csv(csv_dir and os.path.join(csv_dir, "dc_leakage.csv"), rows, hdr)
    print("Leak is (2d-1) * I(b=1) counts for a DC density d. Example: ambient puts d = 0.9,")
    print("signal is 1 mV at the TIA (3e-4 density, so N*3e-4 counts). With TW = 4097 the leak")
    print("is 0.8*M counts against 0.3*M signal counts: the 'DC' completely swamps the answer.")
    print("With a power-of-two TW it is exactly zero. Only power-of-two TWs make the whole-period")
    print("claim exact. Bonus: power-of-two frequencies are Walsh/Rademacher functions, so")
    print("multi-LED channels at f, 2f, 4f... are exactly orthogonal over the slowest period.\n")


def section3(csv_dir):
    print("=" * 78)
    print("3. ADC NETWORK  (R1 = R2 = R; usable Vin range moves with the unknown threshold)")
    print("=" * 78)
    rows = []
    for r, c in [(10e3, 10e-9), (10e3, 1e-9), (4.7e3, 1e-9), (47e3, 1e-9), (100e3, 10e-9)]:
        for f_clk in (10e6, 50e6):
            adc = PinAdc(r1=r, r2=r, c=c)
            rng = []
            for vth in (0.8, 1.4, 1.7):
                lo, hi = PinAdc(r1=r, r2=r, c=c, vth=vth).input_range()
                rng.append(f"{lo:+.2f}..{hi:+.2f}")
            rows.append([f"{r/1e3:g}k", f"{c*1e9:g}n", f"{f_clk/1e6:.0f}M", f"{adc.tau*1e6:7.2f}",
                         f"{adc.tau * f_clk:8.0f}", f"{adc.step_per_clock(f_clk)*1e3:7.3f}", *rng])
    hdr = ["R", "C", "f_clk", "tau [us]", "tau [clk]", "step [mV]",
           "Vin range @Vth=0.8", "@1.4", "@1.7"]
    table(rows, hdr)
    write_csv(csv_dir and os.path.join(csv_dir, "adc_network.csv"), rows, hdr)
    print("Guaranteed window for ANY threshold in 0.8..1.7 V is only about +0.1..+1.6 V, so the")
    print("TIA output (dark + ambient + signal) must sit inside it. Measure the real threshold on")
    print("the FPGA and the chip (Vin = 0 gives density 1 - 2*Vth/VDD) and shift with R1 != R2")
    print("or a bias resistor if needed. tau must be >> the 2-3 clock loop delay.\n")


def section4(csv_dir, seeds):
    print("=" * 78)
    print("4. MONTE CARLO: 10 mV LED step on 1.0 V of room light (10% 100 Hz flicker)")
    print("   f_clk = 10 MHz, TW = 2^12 (2441 Hz); error of the |I|+|Q| amplitude estimate")
    print("=" * 78)
    f_clk, tw = 10e6, 4096
    scene = OpticalScene(v_dark=0.2, v_ambient=0.8, flicker=0.1, v_signal=0.010,
                         tia_bw_hz=100e3, noise_rms=0.0)
    rows = []
    for (r, c) in [(10e3, 10e-9), (10e3, 1e-9), (47e3, 1e-9)]:
        for hyst in (0.0, 0.2):
            for m in (24, 122, 1000):
                adc = PinAdc(r1=r, r2=r, c=c, vth=1.4, hyst=hyst, noise_rms=1e-3)
                est = [measure(scene, adc, f_clk, tw, m, seed=s)["v_signal_est"] for s in range(seeds)]
                est = np.array(est) * 1e3
                t_int = m * PHASE_MOD / tw / f_clk
                rows.append([f"{r/1e3:g}k/{c*1e9:g}n", f"{hyst:.1f}", m, f"{t_int*1e3:6.1f}",
                             f"{est.mean():7.3f}", f"{est.std():6.3f}",
                             f"{100 * abs(est.mean() - 10) / 10:5.1f}%"])
    hdr = ["R/C", "hyst [V]", "M", "T_int [ms]", "mean [mV]", "std [mV]", "bias"]
    table(rows, hdr)
    write_csv(csv_dir and os.path.join(csv_dir, "monte_carlo.csv"), rows, hdr)
    print("True step = 10.000 mV. The 1 mV pin noise is included. M = 24 and 122 give ~10 ms and")
    print("~50 ms (whole multiples of the 100 Hz flicker period; see section 5).\n")


def section5(csv_dir, seeds):
    print("=" * 78)
    print("5. FLICKER vs INTEGRATION TIME  (10k/10n, no hysteresis, 10 MHz, TW = 2^12)")
    print("=" * 78)
    f_clk, tw = 10e6, 4096
    scene = OpticalScene(v_dark=0.2, v_ambient=0.8, flicker=0.1, v_signal=0.010, tia_bw_hz=100e3)
    dark = OpticalScene(v_dark=0.2, v_ambient=0.0, flicker=0.0, v_signal=0.010, tia_bw_hz=100e3)
    adc = PinAdc(noise_rms=1e-3)
    rows = []
    for m in (10, 12, 24, 30, 49, 61, 122, 200, 244):
        t_int = m * PHASE_MOD / tw / f_clk
        e_room = np.array([measure(scene, adc, f_clk, tw, m, seed=s)["v_signal_est"] for s in range(seeds)])
        e_dark = np.array([measure(dark, adc, f_clk, tw, m, seed=s)["v_signal_est"] for s in range(seeds)])
        rows.append([m, f"{t_int*1e3:6.2f}", f"{t_int/0.01:5.2f}",
                     f"{1e3*np.sqrt(np.mean((e_room-0.01)**2)):6.3f}",
                     f"{1e3*np.sqrt(np.mean((e_dark-0.01)**2)):6.3f}"])
    hdr = ["M", "T_int [ms]", "T_int / 10ms", "rms err room [mV]", "rms err dark [mV]"]
    table(rows, hdr)
    write_csv(csv_dir and os.path.join(csv_dir, "flicker.csv"), rows, hdr)
    print("Room-light error drops when T_int is close to a whole number of 10 ms flicker periods.")
    print("In the dark the remaining error is the modulator's own quantisation + pin noise.\n")


def section6(csv_dir, seeds):
    print("=" * 78)
    print("6. CLOCK CHOICE at equal integration time (~50 ms), 10k/10n, room light")
    print("=" * 78)
    scene = OpticalScene(v_dark=0.2, v_ambient=0.8, flicker=0.1, v_signal=0.010, tia_bw_hz=100e3)
    rows = []
    for f_clk, tw, m in [(10e6, 4096, 122), (50e6, 1024, 153), (50e6, 2048, 305)]:
        for hyst in (0.0, 0.2):
            adc = PinAdc(r1=10e3, r2=10e3, c=10e-9, hyst=hyst, noise_rms=1e-3)
            est = 1e3 * np.array([measure(scene, adc, f_clk, tw, m, seed=s)["v_signal_est"]
                                  for s in range(seeds)])
            rows.append([f"{f_clk/1e6:.0f} MHz", tw, f"{f_mod_from_tw(tw, f_clk):7.1f}", m,
                         f"{hyst:.1f}", f"{est.mean():7.3f}", f"{np.sqrt(np.mean((est - 10)**2)):6.3f}"])
    hdr = ["f_clk", "TW", "f_mod [Hz]", "M", "hyst [V]", "mean [mV]", "rms err [mV]"]
    table(rows, hdr)
    write_csv(csv_dir and os.path.join(csv_dir, "clock_choice.csv"), rows, hdr)
    print("Without hysteresis, 5x the clock gives 5x the counts per period for the same")
    print("per-boundary quantisation error: roughly 2x lower error in the same time")
    print("(run without --quick for tighter estimates).")
    print("With hysteresis the limit cycle, not the clock, sets the error: shrink R*C or use")
    print("an external comparator.\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="fewer Monte Carlo seeds")
    ap.add_argument("--csv", metavar="DIR", help="also write CSV tables to DIR")
    args = ap.parse_args()
    if args.csv:
        os.makedirs(args.csv, exist_ok=True)
    seeds = 4 if args.quick else 12
    t = time.time()
    section1(args.csv)
    section2(args.csv)
    section3(args.csv)
    section4(args.csv, seeds)
    section5(args.csv, seeds)
    section6(args.csv, max(4, seeds // 2))
    print(f"done in {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()

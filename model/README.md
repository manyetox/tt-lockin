# Reference model

`lockin_model.py` has two layers.

**Bit-exact digital model**: `Nco`, `SdAdcSync`, `LockinCore` step one clock at a time, the
same as the RTL. `nco_arrays()` and `lockin_window()` do the same thing vectorised. The
cocotb tests compare the RTL against these every cycle. The cycle conventions in the module
docstring are the contract, and they are repeated in each `src/*.sv` header.

**Physical model**:
* `PinAdc`: R1/R2/C node, pin threshold, hysteresis and noise, a 2-FF synchroniser, an
  exact RC solution per clock. `d_ideal()` is the charge-balance prediction and
  `input_range()` the usable Vin window.
* `RcNodeStepper`: the same, one cycle at a time, for cocotb closed-loop tests.
* `OpticalScene`: dark level, ambient light with 100 Hz full-wave flicker, LED step, TIA
  bandwidth, delay and noise.
* `measure()`: one complete single-shot measurement as the ESP32 would run it.

ESP32-side maths: `amplitude_square()`, `amplitude_sine()`, `square_phase_deg()`, `max_m_periods()`.

`design_sweep.py` prints six tables: the frequency plan, DC leakage vs TW, the ADC network,
Monte Carlo error vs M and R/C, flicker vs integration time, and 10 vs 50 MHz. Use
`--csv out` to get CSVs you can plot in MATLAB.

Install `numba` (`pip install numba`) for a ~50x faster physical simulation. Everything
works without it.

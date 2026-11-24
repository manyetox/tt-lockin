# DE0-Nano build of the lock-in chip

Terasic DE0-Nano, Cyclone IV E **EP4CE22F17C6**, Quartus Prime Lite 23.1 (the version
in your `counter` project). The wrapper `de0_nano_top.sv` instantiates
`tt_um_manyetox_lockin` **unchanged**, straight from `../../src`. The bitstream therefore
runs exactly the RTL that goes to the chip.

## Build

1. Open `lockin_de0nano.qpf` in Quartus Prime Lite.
2. Choose the clock in `lockin_de0nano.qsf`:
   `set_parameter -name USE_PLL_10MHZ 0` gives 50 MHz straight from `CLOCK_50`.
   `1` gives 10 MHz from the PLL.
3. Compile, then use Programmer, USB-Blaster, `output_files/lockin_de0nano.sof`.
4. LED[7] on means the clock is running and the design is out of reset. KEY[0] resets.

If `lockin_pll.v` (a hand-written `altpll`) gives errors with `USE_PLL_10MHZ = 1`,
regenerate it with the IP Catalog. The steps are at the top of the file, and the wrapper
needs no change. Before opening Quartus you can simulate the wrapper's pin mapping with
`cd test/unit && python -m pytest -k de0`.

## Header JP2 (GPIO_1): TT pins in order

| JP2 pin | GPIO_1 | FPGA | TT pin | Connect to |
|---|---|---|---|---|
| 2  | [0]  | F13 | ui_in[0]  | ESP32 SCLK |
| 4  | [1]  | T15 | ui_in[1]  | ESP32 MOSI |
| 5  | [2]  | T14 | ui_in[2]  | ESP32 CS (weak pull-up in FPGA) |
| 6  | [3]  | T13 | ui_in[3]  | **ADC node** (R1, R2, C junction). No pull-up. |
| 7  | [4]  | R13 | ui_in[4]  | external reference (stretch) |
| 13 | [8]  | T10 | uo_out[0] | ESP32 MISO |
| 14 | [9]  | R11 | uo_out[1] | **ADC feedback**, to R2 |
| 15 | [10] | P11 | uo_out[2] | MOD, to the LED/laser driver input |
| 16 | [11] | R10 | uo_out[3] | REF90, scope trigger |
| 17 | [12] | N12 | uo_out[4] | DONE, to an ESP32 GPIO (interrupt) |
| 12, 30 | | | | **GND**, common with ESP32, TIA and driver |

ui_in[7:5] are on JP2 pins 8–10, uo_out[7:5] (always 0) on 18–20, and uio[7:0] (inputs)
on 21–28. Pin 1 of JP2 is marked on the silkscreen. Pins 1 and 3 are the FPGA's
input-only GPIO_1_IN0/1 (not used), pin 11 is **5 V**, and pin 29 is 3.3 V. Check
against the silkscreen and Figure 3-x of the DE0-Nano manual before the first power-up.

**3.3 V only.** The header pins go straight to the FPGA with no protection. Never put the
5 V pin or a 5 V signal on a GPIO. MOD can't drive a laser or a bright LED directly.
Feed it into your modulation board's AC/modulation input (the external driver accepts
0–5 V, and a 3.3 V MOD gives 66 % of that range unless your second amp stage scales it).

## Breadboard ADC (start here)

```
TIA out ──R1 10k──┬──R2 10k── JP2-14 (ADC_FB)
                  │
                  ├────────── JP2-6  (ADC_CMP)
                  │
                 C 10 nF
                  │
                 GND (JP2-12)
```

* Keep the node short. C goes right at the header, and its ground goes to JP2-12.
* With R1 = R2, the bit density is `d = (Vin + 3.3 - 2*Vth)/3.3`. Equivalently, the
  average voltage on ADC_FB is `2*Vth - Vin`. The usable Vin range is
  `[2*Vth - 3.3, 2*Vth]`, so the TIA output must sit inside it. Measure Vth first:
  ground the TIA end of R1 (Vin = 0), measure ADC_FB with a multimeter in DC mode, and
  halve the reading.
* The model (`model/design_sweep.py`) shows 10k/10k/10n works well when the pin has no
  hysteresis. If ADC_FB shows long runs of identical bits (a slow limit cycle instead of
  busy toggling), the input has hysteresis. Then use a smaller C, such as 1 nF.

## ESP32 side

* SPI mode 0, MSB first, one 16-bit frame per CS low (see `docs/info.md`).
* SCLK ≤ f_clk/8: ≤ 6.25 MHz at 50 MHz and ≤ 1.25 MHz at 10 MHz. 1 MHz is plenty.
* MISO is always driven (the chip can't tri-state uo_out). Give the chip its own SPI
  bus, or its own MISO line.
* Measurement: write TW (3 bytes) and M (2 bytes). Write CTRL = RUN and wait for the
  optics and TIA to settle. Write CTRL = RUN|START, wait for DONE (pin or STATUS), then
  read I and Q (3 bytes each, little-endian, signed).

## Handy extra on this board

The DE0-Nano has an 8-channel 12-bit ADC (ADC128S022, up to 200 kS/s) on the 2x13
header. It's a cheap independent check of the TIA waveform while you debug the
sigma-delta loop.

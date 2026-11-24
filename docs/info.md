<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

A digital lock-in amplifier. It measures how much of a photodiode signal is synchronous with a
modulated LED or laser. Ambient light, mains flicker and offsets are rejected. Four blocks:

1. **NCO** (`nco.sv`). A 24-bit phase accumulator advances by the tuning word TW every clock.
   Its MSB drives the LED (`MOD`) at `f_mod = TW * f_clk / 2^24`. A copy shifted by 90 degrees
   (`REF90 = p[23] XOR p[22]`, leading `MOD`) is the quadrature reference.
2. **Sigma-delta ADC from two pins** (`sd_adc.sv`). The photodiode/TIA voltage goes through R1
   into a node with a capacitor C to ground. The chip's `ADC_FB` output feeds the same node
   through R2, and the node goes to the `ADC_CMP` input. The input buffer is a 1-bit quantiser,
   C is the integrator and `ADC_FB = NOT b` is the 1-bit DAC. The loop holds the node at the
   pin threshold Vth, so charge balance gives a ones density `d = (Vin + VDD - 2*Vth)/VDD`
   (R1 = R2). The unknown threshold only adds an offset, and the lock-in removes offsets.
3. **Correlator** (`lockin_core.sv`). With 1-bit signal and references, multiplying is XNOR and
   accumulating is an up/down counter: `I += (b == ref_i) ? 1 : -1`, and the same for Q.
   It integrates exactly M whole reference periods from the first period boundary after START,
   then freezes I and Q and raises `DONE`.
4. **SPI registers** (`spi_regs.sv`). The ESP32 sets TW and M, starts a run and reads I and Q.

For a square-wave optical signal (LED switched on/off, TIA much faster than f_mod), the
correlation is a triangle function of delay. So `|I| + |Q| = N * a` exactly, where `a` is the
on/off step in density units, N the number of samples, and the position on the
`|I| + |Q| = 1` diamond gives the delay. For a sinusoidal signal, use `(pi/2) * sqrt(I^2 + Q^2) / N`.

**Exact DC rejection needs TW to be a power of two.** Then every half and quarter period is a
whole number of clocks, so the references sum to exactly zero over the window. Other TWs leak
a small, deterministic fraction of the DC level into I and Q.

### Register map (SPI mode 0, 16-bit frames: R/W, 7-bit address, 8-bit data, MSB first)

Bit 15 = 1 for a read. During a read, the chip returns the data on MISO in bits 7:0.
Multi-byte values are little-endian. I and Q are 24-bit two's complement.

| Address | Name | Access | Description |
|---|---|---|---|
| 0x00 | ID | R | 0x4C |
| 0x01 | CTRL | RW | bit 0 RUN (NCO and MOD on), bit 1 START (write 1 to start a run; reads 0) |
| 0x02 | STATUS | R | bit 0 DONE, bit 1 BUSY |
| 0x04-0x05 | M | RW | number of whole reference periods to integrate |
| 0x10-0x12 | TW | RW | tuning word, channel 0 |
| 0x14-0x16 | I | R | in-phase result, channel 0 |
| 0x18-0x1A | Q | R | quadrature result, channel 0 |

All other addresses read 0. Keep M at or below TW/2 so that |I| and |Q| can never overflow.

## How to test

1. Connect an ESP32 to SCLK/MOSI/CS_N/MISO (SPI mode 0, SCLK at most f_clk/8) and DONE to a GPIO.
2. Build the RC network: R1 = R2 = 10 kOhm, C = 10 nF. Connect the TIA output to R1 and
   ADC_FB to R2. Their junction (with C to ground) goes to ADC_CMP.
3. Drive the LED/laser driver from MOD, and point it at the photodiode.
4. Write TW (e.g. 4096 gives 2441.4 Hz at 10 MHz; use a power of two), M (e.g. 122, about 50 ms),
   then CTRL = 0x01 (RUN). Let the optics settle, then write CTRL = 0x03 (RUN + START).
5. Wait for DONE, then read I (0x14-0x16) and Q (0x18-0x1A).
   Amplitude in volts at the TIA = `(|I| + |Q|) / N * VDD * R1/R2`, with `N = M * 2^24 / TW`.
6. Cover the photodiode, or switch room lights on and off. The result should change only with
   the LED light.

## External hardware

ESP32 (SPI master). Photodiode with a transimpedance amplifier. LED or laser driver
accepting a 3.3 V logic modulation input. Two resistors and one capacitor for the ADC.

Limits: the lock-in can't recover a clipped signal, and it can't tell you that it is
clipped either. A stuck ADC bit reads as zero signal. Keep the TIA output inside
`[2*Vth - VDD, 2*Vth]`, for example by monitoring the average of ADC_FB (`= 2*Vth - Vin`)
with an RC filter into an ESP32 ADC pin. Ambient-light shot noise at f_mod averages down
but is never removed. Use f_mod of a few kHz, away from 100 Hz flicker, and integrate for
a whole number of 10 ms periods.

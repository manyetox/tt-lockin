"""
regmap.py -- SPI register map of tt_um_manyetox_lockin.

Shared by the cocotb tests, the docs and (by copy) the ESP32 firmware. If you change
an address here, change the localparams in src/spi_regs.sv too.

SPI frame (mode 0: CPOL = 0, CPHA = 0; MSB first; one frame per CS_N low period)
    bit 15     R/W   1 = read, 0 = write
    bits 14:8  ADDR  7-bit byte address
    bits 7:0   DATA  write: data from the master on MOSI
                     read:  data from the chip on MISO (MISO is 0 during bits 15:8)
Bits after the 16th in the same CS_N period are ignored. A frame cut short by CS_N
rising has no effect.

Multi-byte values are little-endian: the lowest address holds bits 7:0.
I and Q are 24-bit two's complement. They freeze when DONE rises and stay valid until
the next START, which is why reading them byte by byte is safe without shadow registers.
"""

ID_VALUE = 0x4C  # 'L'

ADDR_ID = 0x00  # R    constant ID_VALUE
ADDR_CTRL = 0x01  # RW  bit0 RUN (NCO + modulation output on), bit1 START (write 1: pulse, reads 0)
ADDR_STATUS = 0x02  # R  bit0 DONE, bit1 BUSY
ADDR_M = 0x04  # RW  M[7:0] at 0x04, M[15:8] at 0x05: number of whole periods to integrate

CH_BASE = 0x10  # channel c registers start at CH_BASE + CH_STRIDE * c
CH_STRIDE = 0x10
OFF_TW = 0x0  # RW  TW[23:0] at +0, +1, +2
OFF_I = 0x4  # R   I[23:0]  at +4, +5, +6
OFF_Q = 0x8  # R   Q[23:0]  at +8, +9, +A

CTRL_RUN = 0x01
CTRL_START = 0x02
STATUS_DONE = 0x01
STATUS_BUSY = 0x02


def addr_tw(ch: int = 0) -> int:
    return CH_BASE + CH_STRIDE * ch + OFF_TW


def addr_i(ch: int = 0) -> int:
    return CH_BASE + CH_STRIDE * ch + OFF_I


def addr_q(ch: int = 0) -> int:
    return CH_BASE + CH_STRIDE * ch + OFF_Q


def frame(read: bool, addr: int, data: int = 0) -> int:
    """16-bit SPI frame as shifted out MSB first."""
    return ((1 if read else 0) << 15) | ((addr & 0x7F) << 8) | (data & 0xFF)

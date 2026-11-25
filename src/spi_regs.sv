/*
 * Copyright (c) 2026 Taylan Arslan
 * SPDX-License-Identifier: Apache-2.0
 */
// =============================================================================
// spi_regs.sv -- SPI mode-0 slave, oversampled by clk, plus the byte register file
// =============================================================================
// FRAME  (one frame per CS_N low period, MSB first)
//     bit 15     R/W   1 = read, 0 = write
//     bits 14:8  ADDR  7-bit byte address
//     bits 7:0   DATA  write: from MOSI.  read: on MISO (MISO = 0 during bits 15:8)
//
// REGISTER MAP  (also in model/regmap.py and docs/info.md; keep all three in sync)
//     0x00        ID       R    8'h4C
//     0x01        CTRL     RW   bit0 RUN (NCO + modulation on), bit1 START (write 1 = pulse, reads 0)
//     0x02        STATUS   R    bit0 DONE, bit1 BUSY
//     0x04-0x05   M[15:0]  RW   little-endian (0x04 = bits 7:0)
//     channel c, base = 0x10 + 0x10*c:
//       +0..+2    TW[23:0] RW
//       +4..+6    I[23:0]  R    two's complement
//       +8..+A    Q[23:0]  R    two's complement
//     Anything else reads 0x00. Writes to read-only or unmapped addresses are ignored.
//     Reset values: CTRL = 0, M = 0, TW = 0.
//
// CONTRACT  (test/unit/tb_spi_regs.py drives a real mode-0 master, async to clk)
//   * sclk, mosi, cs_n are asynchronous pins. Synchronise them before use.
//   * CS_N high resets the frame. A frame cut short by CS_N rising changes nothing.
//   * Write: takes effect when the 16th bit is sampled, i.e. before CS_N rises.
//     Within 6 clk cycles of the 16th SCLK rising edge, the target register (and run,
//     m_periods, tw outputs) hold the new value. Writing CTRL with bit1 = 1 makes `start`
//     high for EXACTLY ONE clk cycle. Writing CTRL with bit1 = 0 makes no pulse.
//   * Read: MISO must already hold each data bit at the SCLK rising edge where the master
//     samples it (bits 7..0 = 9th..16th rising edges). The first data bit (bit 7) must be
//     valid by the 9th rising edge, one SCLK period after the address is complete.
//   * Extra SCLK edges after bit 0 in the same CS_N period are ignored.
//   * The tests run SCLK = f_clk / SPI_DIV with SPI_DIV = 8 by default (env var).
//
// DESIGN QUESTIONS TO BE READY TO DEFEND
//   1. Latency budget. A 2-FF synchroniser plus an edge detector sees an SCLK edge 2-3
//      clk cycles late. If you change MISO on the detected FALLING edge, MISO becomes valid
//      ~3-4 clks after that falling edge, but the master samples half an SCLK period
//      later. At SCLK = f_clk/4 that is only 2 clks: too late. Two fixes:
//        (a) keep SCLK <= f_clk/8, or
//        (b) advance MISO right after the detected RISING edge (the master has already
//            sampled the old bit), giving almost a full SCLK period of setup.
//      Which did you pick? Set SPI_DIV=4 to see whether your design survives f_clk/4.
//   2. Why is it fine to sample the SYNCHRONISED mosi on the synchronised sclk edge,
//      even though MOSI changes only half an SCLK period after the rising edge?
//   3. uo_out pins cannot be tri-stated. MISO is therefore always driven, so this chip
//      cannot share an SPI bus with other slaves unless MISO moves to a uio pin with
//      uio_oe = ~cs_n. Is a dedicated bus on the ESP32 acceptable?
//   4. Multi-byte TW writes take three frames, so the NCO briefly runs at a mixed frequency.
//      Harmless? (Stop RUN while writing TW if you care.)
// =============================================================================
`default_nettype none

module spi_regs #(
    parameter int N_CH = 1
) (
    input  logic                 clk,
    input  logic                 rst_n,      // active low
    // SPI pins, asynchronous to clk
    input  logic                 sclk,
    input  logic                 mosi,
    input  logic                 cs_n,
    output logic                 miso,
    // control outputs (clk domain)
    output logic                 run,        // CTRL.RUN
    output logic                 start,      // one-cycle pulse on a CTRL write with bit1 = 1
    output logic [15:0]          m_periods,  // M
    output logic [24*N_CH-1:0]   tw,         // channel c tuning word = tw[24*c +: 24]
    // status inputs from the lock-in cores
    input  logic                 done,
    input  logic                 busy,
    input  logic [24*N_CH-1:0]   acc_i,      // channel c I = acc_i[24*c +: 24]
    input  logic [24*N_CH-1:0]   acc_q
);

  // Register addresses (must match model/regmap.py)
  localparam logic [7:0] ID_VALUE    = 8'h4C;
  localparam logic [6:0] ADDR_ID     = 7'h00;
  localparam logic [6:0] ADDR_CTRL   = 7'h01;
  localparam logic [6:0] ADDR_STATUS = 7'h02;
  localparam logic [6:0] ADDR_M_LO   = 7'h04;
  localparam logic [6:0] ADDR_M_HI   = 7'h05;
  localparam logic [6:0] CH_BASE     = 7'h10;  // + 7'h10 per channel
  localparam logic [3:0] OFF_TW      = 4'h0;   // +0..+2
  localparam logic [3:0] OFF_I       = 4'h4;   // +4..+6
  localparam logic [3:0] OFF_Q       = 4'h8;   // +8..+A

  // ---------------------------------------------------------------------------
  // Synchronisers. sclk gets a third flop so we can see its previous value.
  // mosi_s[1] and sclk_s[1] went through the same number of flops, so they line up.
  // ---------------------------------------------------------------------------
  logic [2:0] sclk_s;
  logic [1:0] mosi_s;
  logic [1:0] cs_s;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      sclk_s <= '0;
      mosi_s <= '0;
      cs_s   <= 2'b11;
    end else begin
      sclk_s <= {sclk_s[1:0], sclk};
      mosi_s <= {mosi_s[0], mosi};
      cs_s   <= {cs_s[0], cs_n};
    end
  end

  logic sclk_rise, cs_idle;
  assign sclk_rise = sclk_s[1] & ~sclk_s[2];
  assign cs_idle   = cs_s[1];

  // ---------------------------------------------------------------------------
  // Frame shifter. On each SCLK rising edge, frame = bits received so far plus the
  // new one, so on the 8th edge frame[7:0] = {R/W, ADDR} and on the 16th edge
  // frame[15:0] is the whole command.
  // ---------------------------------------------------------------------------
  logic [4:0]  bit_cnt;          // rising edges seen in this frame, stops at 16
  logic [14:0] shreg;
  logic [7:0]  tx;               // read data still to send, MSB first
  logic [15:0] frame;
  assign frame = {shreg, mosi_s[1]};

  logic edge_ok;                 // a rising edge that belongs to this frame
  assign edge_ok = !cs_idle && sclk_rise && (bit_cnt != 5'd16);

  // ---------------------------------------------------------------------------
  // Register file
  // ---------------------------------------------------------------------------
  logic [24*N_CH-1:0] tw_r;     // channel c = tw_r[24*c +: 24]

  // Read mux, addressed by the 7 address bits that arrive on the 8th edge.
  // Built from whole signals and continuous assigns (Icarus can't model part-selects
  // inside always_comb sensitivity): the global registers here, then one byte per
  // channel from the generate loop below.
  logic [6:0] rd_addr;
  logic [7:0] rd_data, rd_global;
  logic [7:0] m_lo, m_hi;
  assign rd_addr = frame[6:0];
  assign m_lo    = m_periods[7:0];
  assign m_hi    = m_periods[15:8];

  always_comb begin
    case (rd_addr)
      ADDR_ID:     rd_global = ID_VALUE;
      ADDR_CTRL:   rd_global = {7'b0, run};         // START always reads 0
      ADDR_STATUS: rd_global = {6'b0, busy, done};
      ADDR_M_LO:   rd_global = m_lo;
      ADDR_M_HI:   rd_global = m_hi;
      default:     rd_global = 8'h00;
    endcase
  end

  // Each channel puts its read byte (or 0) here; at most one is non-zero, so OR them.
  logic [8*N_CH-1:0] ch_rd;

  function automatic logic [7:0] or_bytes(input logic [8*N_CH-1:0] v);
    or_bytes = 8'h00;
    for (int k = 0; k < N_CH; k++) or_bytes = or_bytes | v[8*k +: 8];
  endfunction

  assign rd_data = rd_global | or_bytes(ch_rd);

  // Write strobe: the 16th bit of a write frame, CS_N still low.
  logic       wr_stb;
  logic [6:0] wr_addr;
  logic [7:0] wr_data;
  assign wr_stb  = edge_ok && (bit_cnt == 5'd15) && !frame[15];
  assign wr_addr = frame[14:8];
  assign wr_data = frame[7:0];

  // ---------------------------------------------------------------------------
  // Shifting and MISO. MISO advances right after the detected RISING edge: the
  // master has just sampled the old bit, so the new one gets almost a whole SCLK
  // period of setup. That is what lets SCLK go up to f_clk/4.
  // ---------------------------------------------------------------------------
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      bit_cnt <= '0;
      shreg   <= '0;
      tx      <= '0;
      miso    <= 1'b0;
    end else if (cs_idle) begin
      bit_cnt <= '0;
      miso    <= 1'b0;
    end else if (edge_ok) begin
      shreg   <= frame[14:0];
      bit_cnt <= bit_cnt + 1'b1;
      if (bit_cnt == 5'd7) begin                    // command byte complete
        miso <= frame[7] & rd_data[7];              // bit 7 of the read data, or 0 for a write
        tx   <= frame[7] ? {rd_data[6:0], 1'b0} : 8'h00;
      end else if (bit_cnt >= 5'd8 && bit_cnt <= 5'd14) begin
        miso <= tx[7];                              // bits 6..0
        tx   <= {tx[6:0], 1'b0};
      end else if (bit_cnt == 5'd15) begin
        miso <= 1'b0;                               // frame over
      end
    end
  end

  // Control registers
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      run       <= 1'b0;
      start     <= 1'b0;
      m_periods <= '0;
    end else begin
      start <= 1'b0;                                // one-cycle pulse
      if (wr_stb) begin
        case (wr_addr)
          ADDR_CTRL: begin
            run   <= wr_data[0];
            start <= wr_data[1];
          end
          ADDR_M_LO: m_periods[7:0]  <= wr_data;
          ADDR_M_HI: m_periods[15:8] <= wr_data;
          default:   ;
        endcase
      end
    end
  end

  // Per-channel tuning words
  genvar c;
  generate
    for (c = 0; c < N_CH; c++) begin : g_ch
      localparam logic [2:0] CH_HI = CH_BASE[6:4] + 3'(c);
      always_ff @(posedge clk) begin
        if (!rst_n) begin
          tw_r[24*c +: 24] <= '0;
        end else if (wr_stb && wr_addr[6:4] == CH_HI) begin
          case (wr_addr[3:0])
            OFF_TW + 4'd0: tw_r[24*c +: 8]      <= wr_data;
            OFF_TW + 4'd1: tw_r[24*c + 8 +: 8]  <= wr_data;
            OFF_TW + 4'd2: tw_r[24*c + 16 +: 8] <= wr_data;
            default:       ;
          endcase
        end
      end

      // This channel's read byte: TW, I or Q, or 0 when not addressed
      logic [23:0] ch_tw, ch_i, ch_q;
      logic [7:0]  ch_byte;
      assign ch_tw = tw_r[24*c +: 24];
      assign ch_i = acc_i[24*c +: 24];
      assign ch_q = acc_q[24*c +: 24];
      assign ch_byte =
          (rd_addr[6:4] != CH_HI)          ? 8'h00 :
          (rd_addr[3:0] == OFF_TW + 4'd0)  ? ch_tw[7:0]     :
          (rd_addr[3:0] == OFF_TW + 4'd1)  ? ch_tw[15:8]    :
          (rd_addr[3:0] == OFF_TW + 4'd2)  ? ch_tw[23:16]   :
          (rd_addr[3:0] == OFF_I  + 4'd0)  ? ch_i[7:0]      :
          (rd_addr[3:0] == OFF_I  + 4'd1)  ? ch_i[15:8]     :
          (rd_addr[3:0] == OFF_I  + 4'd2)  ? ch_i[23:16]    :
          (rd_addr[3:0] == OFF_Q  + 4'd0)  ? ch_q[7:0]      :
          (rd_addr[3:0] == OFF_Q  + 4'd1)  ? ch_q[15:8]     :
          (rd_addr[3:0] == OFF_Q  + 4'd2)  ? ch_q[23:16]    : 8'h00;
      assign ch_rd[8*c +: 8] = ch_byte;
    end
  endgenerate

  assign tw = tw_r;

endmodule

`default_nettype wire

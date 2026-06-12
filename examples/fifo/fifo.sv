`include "common.sv"
module fifo  (input  logic clk, rst, wr_en, rd_en,
  input  logic [`DATA_SIZE-1:0] wr_data,
  output logic full, empty,
  output logic [`DATA_SIZE-1:0] rd_data,
  output logic [$clog2(`FIFO_LOCATIONS)-1:0] wr_ptr, rd_ptr
  // pointers exposed as output port
);
 logic [`DATA_SIZE-1:0] mem [`FIFO_LOCATIONS];
 localparam PTR_WIDTH = $clog2(`FIFO_LOCATIONS);
 logic [PTR_WIDTH:0] cnt;

  always_ff @(posedge clk) begin : scrittura
    if (rst) begin
      wr_ptr <= '0;
    end else begin
      if (wr_en && !full) begin
        mem[wr_ptr] <= wr_data;
      `ifdef MUT_WR_PTR
        wr_ptr <= wr_ptr;
      `else
        wr_ptr <= wr_ptr + 1;
      `endif
      end
    end
  end : scrittura
  
  always_ff @(posedge clk) begin : lettura
    if (rst) begin
     rd_ptr <= '0;
    end else begin
      if (rd_en && !empty) begin
      `ifdef MUT_RD_PTR
        rd_ptr <= rd_ptr;
      `else
        rd_ptr <= rd_ptr + 1;
      `endif
      end
    end
  end : lettura

  always_ff @(posedge clk) begin : conteggio
    if (rst) begin
     cnt <= '0;
    end else begin
      case ({rd_en && !empty , wr_en && !full})
      `ifdef MUT_RD_CNT
        2'b10 : cnt <= cnt;
      `else
        2'b10 : cnt <= cnt - 1;
      `endif

      `ifdef MUT_WR_CNT
        2'b01 : cnt <= cnt;
      `else
        2'b01 : cnt <= cnt + 1;
      `endif

        default : cnt <= cnt;
      endcase
    end
  end : conteggio


  `ifdef MUT_EMPTY
    assign empty = (cnt == '0) || (cnt == 1);
  `else
    assign empty = (cnt == '0);
  `endif

  `ifdef MUT_FULL
    assign full  = 1'b0;
  `else
    assign full  = (cnt[PTR_WIDTH] == 1);
  `endif

  `ifdef MUT_RD_DATA
    assign rd_data = mem[wr_ptr];
  `else
    assign rd_data = mem[rd_ptr];
  `endif

endmodule

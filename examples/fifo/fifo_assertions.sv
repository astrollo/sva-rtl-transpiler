`include "common.sv"
module fifo_assertions (input  logic clk, rst, wr_en, rd_en,
  input  logic [`DATA_SIZE-1:0] wr_data,
  input logic full, empty,
  input logic [`DATA_SIZE-1:0] rd_data,
  input logic [$clog2(`FIFO_LOCATIONS)-1:0] wr_ptr, rd_ptr
);

localparam int PTR_WIDTH = $clog2(`FIFO_LOCATIONS);
localparam logic [PTR_WIDTH-1:0] PTR_ONE = {{(PTR_WIDTH-1){1'b0}}, 1'b1};

`define WR_FIRE (wr_en && !full)
`define RD_FIRE (rd_en && !empty)

default clocking @(posedge clk); endclocking
default disable iff (rst);

a1: assert property (`WR_FIRE |=> wr_ptr == $past(wr_ptr) + PTR_ONE);
a2: assert property (!`WR_FIRE |=> $stable(wr_ptr));
a3: assert property (`RD_FIRE |=> rd_ptr == $past(rd_ptr) + PTR_ONE);
a4: assert property (!`RD_FIRE |=> $stable(rd_ptr));
a5: assert property (empty && `WR_FIRE |=> !empty && (rd_data==$past(wr_data)));
a6: assert property (empty && `WR_FIRE ##1 !`RD_FIRE |=>rd_data==$past(wr_data,2));
a7: assert property (empty ##0 ((!`RD_FIRE) throughout (`WR_FIRE[->`FIFO_LOCATIONS]))
      |=>  full);
a8: assert property (full ##0 ((!`WR_FIRE) throughout (`RD_FIRE[->`FIFO_LOCATIONS]))
      |=> empty);

endmodule

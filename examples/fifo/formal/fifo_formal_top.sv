`include "common.sv"
module fifo_formal_top;
logic clk, rst, wr_en, rd_en;
logic [`DATA_SIZE-1:0] wr_data;
logic full, empty;
logic [`DATA_SIZE-1:0] rd_data;
logic [$clog2(`FIFO_LOCATIONS)-1:0] wr_ptr, rd_ptr;

fifo dut (.*);
fifo_assertions test (.*);

always_ff @(posedge clk) begin
  if ($initstate)
    assume(rst);
  else
    assume(!rst);
end

endmodule

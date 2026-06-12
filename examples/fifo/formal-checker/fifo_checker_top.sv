`include "common.sv"
module fifo_checker_top;
logic clk, rst, wr_en, rd_en;
logic [`DATA_SIZE-1:0] wr_data;
logic full, empty;
logic [`DATA_SIZE-1:0] rd_data;
logic [$clog2(`FIFO_LOCATIONS)-1:0] wr_ptr, rd_ptr;

logic a1_assert_fail, a2_assert_fail, a3_assert_fail, a4_assert_fail;
logic a5_assert_fail, a6_assert_fail, a7_assert_fail, a8_assert_fail;
logic a1_assert_pass, a2_assert_pass, a3_assert_pass, a4_assert_pass;
logic a5_assert_pass, a6_assert_pass, a7_assert_pass, a8_assert_pass;

logic any_fail;
assign any_fail =
    a1_assert_fail | a2_assert_fail | a3_assert_fail | a4_assert_fail
  | a5_assert_fail | a6_assert_fail | a7_assert_fail | a8_assert_fail;

fifo dut (.*);
fifo_assertions hwchecker (.*);

always_ff @(posedge clk) begin
  if ($initstate)
    assume(rst);
  else
    assume(!rst);
end

`ifdef TEST_ASSERT
  always_ff @(posedge clk) begin
    if (!rst)
      tot: assert(!any_fail);
  end
`endif

always_ff @(posedge clk) begin
  if (!rst) begin
    `ifdef CHECK_A1
      cover(a1_assert_fail);
    `endif
    `ifdef CHECK_A2
      cover(a2_assert_fail);
    `endif
    `ifdef CHECK_A3
      cover(a3_assert_fail);
    `endif
    `ifdef CHECK_A4
      cover(a4_assert_fail);
    `endif
    `ifdef CHECK_A5
      cover(a5_assert_fail);
    `endif
    `ifdef CHECK_A6
      cover(a6_assert_fail);
    `endif
    `ifdef CHECK_A7
      cover(a7_assert_fail);
    `endif
    `ifdef CHECK_A8
      cover(a8_assert_fail);
    `endif
  end
end
`ifdef TEST_COVER 
`endif


`ifdef TEST_COVER 
  always_ff @(posedge clk) begin
    if (!rst) begin
      cover(a1_assert_pass);
      cover(a2_assert_pass);
      cover(a3_assert_pass);
      cover(a4_assert_pass);
      cover(a5_assert_pass);
      cover(a6_assert_pass);
      cover(a7_assert_pass);
      cover(a8_assert_pass);
    end
  end
`endif

endmodule

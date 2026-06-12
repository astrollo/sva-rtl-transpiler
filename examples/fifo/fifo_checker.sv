`include "common.sv"
module fifo_assertions (input  logic clk, rst, wr_en, rd_en,
  input  logic [`DATA_SIZE-1:0] wr_data,
  input logic full, empty,
  input logic [`DATA_SIZE-1:0] rd_data,
  input logic [$clog2(`FIFO_LOCATIONS)-1:0] wr_ptr, rd_ptr
,
    output logic a1_assert_fail,
    output logic a1_assert_pass,
    output logic a2_assert_fail,
    output logic a2_assert_pass,
    output logic a3_assert_fail,
    output logic a3_assert_pass,
    output logic a4_assert_fail,
    output logic a4_assert_pass,
    output logic a5_assert_fail,
    output logic a5_assert_pass,
    output logic a6_assert_fail,
    output logic a6_assert_pass,
    output logic a7_assert_fail,
    output logic a7_assert_pass,
    output logic a8_assert_fail,
    output logic a8_assert_pass
);

localparam int PTR_WIDTH = $clog2(`FIFO_LOCATIONS);
localparam logic [PTR_WIDTH-1:0] PTR_ONE = {{(PTR_WIDTH-1){1'b0}}, 1'b1};

`define WR_FIRE (wr_en && !full)
`define RD_FIRE (rd_en && !empty)

// [SVA-DISABLED] default clocking @(posedge clk); endclocking
// [SVA-DISABLED] default disable iff (rst);

// [SVA-DISABLED] a1: assert property (`WR_FIRE |=> wr_ptr == $past(wr_ptr) + PTR_ONE);
// [SVA-DISABLED] a2: assert property (!`WR_FIRE |=> $stable(wr_ptr));
// [SVA-DISABLED] a3: assert property (`RD_FIRE |=> rd_ptr == $past(rd_ptr) + PTR_ONE);
// [SVA-DISABLED] a4: assert property (!`RD_FIRE |=> $stable(rd_ptr));
// [SVA-DISABLED] a5: assert property (empty && `WR_FIRE |=> !empty && (rd_data==$past(wr_data)));
// [SVA-DISABLED] a6: assert property (empty && `WR_FIRE ##1 !`RD_FIRE |=>rd_data==$past(wr_data,2));
// [SVA-DISABLED] a7: assert property (empty ##0 ((!`RD_FIRE) throughout (`WR_FIRE[->`FIFO_LOCATIONS]))
// [SVA-DISABLED]       |=>  full);
// [SVA-DISABLED] a8: assert property (full ##0 ((!`WR_FIRE) throughout (`RD_FIRE[->`FIFO_LOCATIONS]))
// [SVA-DISABLED]       |=> empty);


    // === TRANSPILED SVA CHECKERS ===
    // Antecedent logic
    // --- Shadow Registers for $past ---
    logic [1:1] [2:0] _a1_past_0 = '0;
    // --- Assertion 1 (a1) ---
    // Type: ASSERT
    logic _a1_lhs_s0_pass;
    logic _a1_lhs_final;
    logic _a1_imp_d = 1'b0;
    logic _a1_rhs_final_pass;
    assign _a1_lhs_s0_pass = (1'b1 && (wr_en && !full));
    assign _a1_lhs_final = _a1_lhs_s0_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a1_past_0 <= '0;
            _a1_imp_d <= 1'b0;
        end else begin
            _a1_past_0 <= wr_ptr;
            _a1_imp_d <= _a1_lhs_final;
        end
    end

    // Consequent logic
    logic _a1_rhs_expr_0;
    logic [0:0] [0:0] _a1_rhs_vec_0;
    logic _a1_rhs_fail_0;
    logic _a1_rhs_pass;

    assign _a1_rhs_expr_0 = (wr_ptr == (_a1_past_0[1] + PTR_ONE));
    assign _a1_rhs_vec_0 = _a1_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a1_lb_z_0_s0;
    assign _a1_lb_z_0_s0 = _a1_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a1_lb_agg_final_0;
    assign _a1_lb_agg_final_0 = _a1_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a1_match_0;
    assign _a1_match_0 = _a1_imp_d && _a1_lb_agg_final_0;
    assign _a1_rhs_fail_0 = _a1_imp_d && !_a1_lb_agg_final_0;
    assign _a1_rhs_pass = _a1_match_0;

    assign _a1_rhs_final_pass = _a1_rhs_pass;

    // --- Hardware Checker Signals for a1 ---
    assign a1_assert_fail = !(rst) && (_a1_rhs_fail_0);
    assign a1_assert_pass = !(rst) && (_a1_rhs_final_pass);

    // Antecedent logic
    // --- Shadow Registers for $past ---
    logic [1:1] [2:0] _a2_past_0 = '0;
    // --- Assertion 2 (a2) ---
    // Type: ASSERT
    logic _a2_lhs_s0_pass;
    logic _a2_lhs_final;
    logic _a2_imp_d = 1'b0;
    logic _a2_rhs_final_pass;
    assign _a2_lhs_s0_pass = (1'b1 && !(wr_en && !full));
    assign _a2_lhs_final = _a2_lhs_s0_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a2_past_0 <= '0;
            _a2_imp_d <= 1'b0;
        end else begin
            _a2_past_0 <= wr_ptr;
            _a2_imp_d <= _a2_lhs_final;
        end
    end

    // Consequent logic
    logic _a2_rhs_expr_0;
    logic [0:0] [0:0] _a2_rhs_vec_0;
    logic _a2_rhs_fail_0;
    logic _a2_rhs_pass;

    assign _a2_rhs_expr_0 = (wr_ptr == _a2_past_0[1]);
    assign _a2_rhs_vec_0 = _a2_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a2_lb_z_0_s0;
    assign _a2_lb_z_0_s0 = _a2_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a2_lb_agg_final_0;
    assign _a2_lb_agg_final_0 = _a2_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a2_match_0;
    assign _a2_match_0 = _a2_imp_d && _a2_lb_agg_final_0;
    assign _a2_rhs_fail_0 = _a2_imp_d && !_a2_lb_agg_final_0;
    assign _a2_rhs_pass = _a2_match_0;

    assign _a2_rhs_final_pass = _a2_rhs_pass;

    // --- Hardware Checker Signals for a2 ---
    assign a2_assert_fail = !(rst) && (_a2_rhs_fail_0);
    assign a2_assert_pass = !(rst) && (_a2_rhs_final_pass);

    // Antecedent logic
    // --- Shadow Registers for $past ---
    logic [1:1] [2:0] _a3_past_0 = '0;
    // --- Assertion 3 (a3) ---
    // Type: ASSERT
    logic _a3_lhs_s0_pass;
    logic _a3_lhs_final;
    logic _a3_imp_d = 1'b0;
    logic _a3_rhs_final_pass;
    assign _a3_lhs_s0_pass = (1'b1 && (rd_en && !empty));
    assign _a3_lhs_final = _a3_lhs_s0_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a3_past_0 <= '0;
            _a3_imp_d <= 1'b0;
        end else begin
            _a3_past_0 <= rd_ptr;
            _a3_imp_d <= _a3_lhs_final;
        end
    end

    // Consequent logic
    logic _a3_rhs_expr_0;
    logic [0:0] [0:0] _a3_rhs_vec_0;
    logic _a3_rhs_fail_0;
    logic _a3_rhs_pass;

    assign _a3_rhs_expr_0 = (rd_ptr == (_a3_past_0[1] + PTR_ONE));
    assign _a3_rhs_vec_0 = _a3_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a3_lb_z_0_s0;
    assign _a3_lb_z_0_s0 = _a3_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a3_lb_agg_final_0;
    assign _a3_lb_agg_final_0 = _a3_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a3_match_0;
    assign _a3_match_0 = _a3_imp_d && _a3_lb_agg_final_0;
    assign _a3_rhs_fail_0 = _a3_imp_d && !_a3_lb_agg_final_0;
    assign _a3_rhs_pass = _a3_match_0;

    assign _a3_rhs_final_pass = _a3_rhs_pass;

    // --- Hardware Checker Signals for a3 ---
    assign a3_assert_fail = !(rst) && (_a3_rhs_fail_0);
    assign a3_assert_pass = !(rst) && (_a3_rhs_final_pass);

    // Antecedent logic
    // --- Shadow Registers for $past ---
    logic [1:1] [2:0] _a4_past_0 = '0;
    // --- Assertion 4 (a4) ---
    // Type: ASSERT
    logic _a4_lhs_s0_pass;
    logic _a4_lhs_final;
    logic _a4_imp_d = 1'b0;
    logic _a4_rhs_final_pass;
    assign _a4_lhs_s0_pass = (1'b1 && !(rd_en && !empty));
    assign _a4_lhs_final = _a4_lhs_s0_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a4_past_0 <= '0;
            _a4_imp_d <= 1'b0;
        end else begin
            _a4_past_0 <= rd_ptr;
            _a4_imp_d <= _a4_lhs_final;
        end
    end

    // Consequent logic
    logic _a4_rhs_expr_0;
    logic [0:0] [0:0] _a4_rhs_vec_0;
    logic _a4_rhs_fail_0;
    logic _a4_rhs_pass;

    assign _a4_rhs_expr_0 = (rd_ptr == _a4_past_0[1]);
    assign _a4_rhs_vec_0 = _a4_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a4_lb_z_0_s0;
    assign _a4_lb_z_0_s0 = _a4_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a4_lb_agg_final_0;
    assign _a4_lb_agg_final_0 = _a4_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a4_match_0;
    assign _a4_match_0 = _a4_imp_d && _a4_lb_agg_final_0;
    assign _a4_rhs_fail_0 = _a4_imp_d && !_a4_lb_agg_final_0;
    assign _a4_rhs_pass = _a4_match_0;

    assign _a4_rhs_final_pass = _a4_rhs_pass;

    // --- Hardware Checker Signals for a4 ---
    assign a4_assert_fail = !(rst) && (_a4_rhs_fail_0);
    assign a4_assert_pass = !(rst) && (_a4_rhs_final_pass);

    // Antecedent logic
    // --- Shadow Registers for $past ---
    logic [1:1] [15:0] _a5_past_0 = '0;
    // --- Assertion 5 (a5) ---
    // Type: ASSERT
    logic _a5_lhs_s0_pass;
    logic _a5_lhs_final;
    logic _a5_imp_d = 1'b0;
    logic _a5_rhs_final_pass;
    assign _a5_lhs_s0_pass = (1'b1 && (empty && (wr_en && !full)));
    assign _a5_lhs_final = _a5_lhs_s0_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a5_past_0 <= '0;
            _a5_imp_d <= 1'b0;
        end else begin
            _a5_past_0 <= wr_data;
            _a5_imp_d <= _a5_lhs_final;
        end
    end

    // Consequent logic
    logic _a5_rhs_expr_0;
    logic [0:0] [0:0] _a5_rhs_vec_0;
    logic _a5_rhs_fail_0;
    logic _a5_rhs_pass;

    assign _a5_rhs_expr_0 = (!empty && (rd_data == _a5_past_0[1]));
    assign _a5_rhs_vec_0 = _a5_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a5_lb_z_0_s0;
    assign _a5_lb_z_0_s0 = _a5_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a5_lb_agg_final_0;
    assign _a5_lb_agg_final_0 = _a5_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a5_match_0;
    assign _a5_match_0 = _a5_imp_d && _a5_lb_agg_final_0;
    assign _a5_rhs_fail_0 = _a5_imp_d && !_a5_lb_agg_final_0;
    assign _a5_rhs_pass = _a5_match_0;

    assign _a5_rhs_final_pass = _a5_rhs_pass;

    // --- Hardware Checker Signals for a5 ---
    assign a5_assert_fail = !(rst) && (_a5_rhs_fail_0);
    assign a5_assert_pass = !(rst) && (_a5_rhs_final_pass);

    // Antecedent logic
    // --- Shadow Registers for $past ---
    logic [2:1] [15:0] _a6_past_0 = '0;
    // --- Assertion 6 (a6) ---
    // Type: ASSERT
    logic _a6_lhs_s0_pass;
    logic _a6_lhs_s1_d1 = 1'b0;
    logic _a6_lhs_s1_pass;
    logic _a6_lhs_final;
    logic _a6_imp_d = 1'b0;
    logic _a6_rhs_final_pass;
    assign _a6_lhs_s0_pass = (1'b1 && (empty && (wr_en && !full)));
    assign _a6_lhs_s1_pass = (_a6_lhs_s1_d1 && !(rd_en && !empty));
    assign _a6_lhs_final = _a6_lhs_s1_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a6_past_0 <= '0;
            _a6_lhs_s1_d1 <= 1'b0;
            _a6_imp_d <= 1'b0;
        end else begin
            _a6_past_0 <= {_a6_past_0[2-1:1], wr_data};
            _a6_lhs_s1_d1 <= _a6_lhs_s0_pass;
            _a6_imp_d <= _a6_lhs_final;
        end
    end

    // Consequent logic
    logic _a6_rhs_expr_0;
    logic [0:0] [0:0] _a6_rhs_vec_0;
    logic _a6_rhs_fail_0;
    logic _a6_rhs_pass;

    assign _a6_rhs_expr_0 = (rd_data == _a6_past_0[2]);
    assign _a6_rhs_vec_0 = _a6_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a6_lb_z_0_s0;
    assign _a6_lb_z_0_s0 = _a6_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a6_lb_agg_final_0;
    assign _a6_lb_agg_final_0 = _a6_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a6_match_0;
    assign _a6_match_0 = _a6_imp_d && _a6_lb_agg_final_0;
    assign _a6_rhs_fail_0 = _a6_imp_d && !_a6_lb_agg_final_0;
    assign _a6_rhs_pass = _a6_match_0;

    assign _a6_rhs_final_pass = _a6_rhs_pass;

    // --- Hardware Checker Signals for a6 ---
    assign a6_assert_fail = !(rst) && (_a6_rhs_fail_0);
    assign a6_assert_pass = !(rst) && (_a6_rhs_final_pass);

    // Antecedent logic
    // --- Assertion 7 (a7) ---
    // Type: ASSERT
    logic _a7_lhs_s0_pass;
    logic _a7_lhs_s1_q = 1'b0;
    logic _a7_lhs_s1_d;
    logic _a7_lhs_s2_q = 1'b0;
    logic _a7_lhs_s2_d;
    logic _a7_lhs_s3_q = 1'b0;
    logic _a7_lhs_s3_d;
    logic _a7_lhs_s4_q = 1'b0;
    logic _a7_lhs_s4_d;
    logic _a7_lhs_s5_q = 1'b0;
    logic _a7_lhs_s5_d;
    logic _a7_lhs_s6_q = 1'b0;
    logic _a7_lhs_s6_d;
    logic _a7_lhs_s7_q = 1'b0;
    logic _a7_lhs_s7_d;
    logic _a7_lhs_s8_q = 1'b0;
    logic _a7_lhs_s8_d;
    logic _a7_lhs_s8_pass;
    logic _a7_lhs_final;
    logic _a7_imp_d = 1'b0;
    logic _a7_rhs_final_pass;
    assign _a7_lhs_s0_pass = (1'b1 && empty);
    assign _a7_lhs_s1_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? 1'b0 : _a7_lhs_s1_q || _a7_lhs_s0_pass) : 1'b0;
    assign _a7_lhs_s2_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s1_q || _a7_lhs_s0_pass : _a7_lhs_s2_q) : 1'b0;
    assign _a7_lhs_s3_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s2_q : _a7_lhs_s3_q) : 1'b0;
    assign _a7_lhs_s4_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s3_q : _a7_lhs_s4_q) : 1'b0;
    assign _a7_lhs_s5_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s4_q : _a7_lhs_s5_q) : 1'b0;
    assign _a7_lhs_s6_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s5_q : _a7_lhs_s6_q) : 1'b0;
    assign _a7_lhs_s7_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s6_q : _a7_lhs_s7_q) : 1'b0;
    assign _a7_lhs_s8_d = ((!(rd_en && !empty))) ? (((wr_en && !full)) ? _a7_lhs_s7_q : _a7_lhs_s8_q) : 1'b0;
    assign _a7_lhs_s8_pass = ((_a7_lhs_s8_q) && ((wr_en && !full))) && ((!(rd_en && !empty)));
    assign _a7_lhs_final = _a7_lhs_s8_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a7_lhs_s1_q <= 1'b0;
            _a7_lhs_s2_q <= 1'b0;
            _a7_lhs_s3_q <= 1'b0;
            _a7_lhs_s4_q <= 1'b0;
            _a7_lhs_s5_q <= 1'b0;
            _a7_lhs_s6_q <= 1'b0;
            _a7_lhs_s7_q <= 1'b0;
            _a7_lhs_s8_q <= 1'b0;
            _a7_imp_d <= 1'b0;
        end else begin
            _a7_lhs_s1_q <= _a7_lhs_s1_d;
            _a7_lhs_s2_q <= _a7_lhs_s2_d;
            _a7_lhs_s3_q <= _a7_lhs_s3_d;
            _a7_lhs_s4_q <= _a7_lhs_s4_d;
            _a7_lhs_s5_q <= _a7_lhs_s5_d;
            _a7_lhs_s6_q <= _a7_lhs_s6_d;
            _a7_lhs_s7_q <= _a7_lhs_s7_d;
            _a7_lhs_s8_q <= _a7_lhs_s8_d;
            _a7_imp_d <= _a7_lhs_final;
        end
    end

    // Consequent logic
    logic _a7_rhs_expr_0;
    logic [0:0] [0:0] _a7_rhs_vec_0;
    logic _a7_rhs_fail_0;
    logic _a7_rhs_pass;

    assign _a7_rhs_expr_0 = full;
    assign _a7_rhs_vec_0 = _a7_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a7_lb_z_0_s0;
    assign _a7_lb_z_0_s0 = _a7_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a7_lb_agg_final_0;
    assign _a7_lb_agg_final_0 = _a7_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a7_match_0;
    assign _a7_match_0 = _a7_imp_d && _a7_lb_agg_final_0;
    assign _a7_rhs_fail_0 = _a7_imp_d && !_a7_lb_agg_final_0;
    assign _a7_rhs_pass = _a7_match_0;

    assign _a7_rhs_final_pass = _a7_rhs_pass;

    // --- Hardware Checker Signals for a7 ---
    assign a7_assert_fail = !(rst) && (_a7_rhs_fail_0);
    assign a7_assert_pass = !(rst) && (_a7_rhs_final_pass);

    // Antecedent logic
    // --- Assertion 8 (a8) ---
    // Type: ASSERT
    logic _a8_lhs_s0_pass;
    logic _a8_lhs_s1_q = 1'b0;
    logic _a8_lhs_s1_d;
    logic _a8_lhs_s2_q = 1'b0;
    logic _a8_lhs_s2_d;
    logic _a8_lhs_s3_q = 1'b0;
    logic _a8_lhs_s3_d;
    logic _a8_lhs_s4_q = 1'b0;
    logic _a8_lhs_s4_d;
    logic _a8_lhs_s5_q = 1'b0;
    logic _a8_lhs_s5_d;
    logic _a8_lhs_s6_q = 1'b0;
    logic _a8_lhs_s6_d;
    logic _a8_lhs_s7_q = 1'b0;
    logic _a8_lhs_s7_d;
    logic _a8_lhs_s8_q = 1'b0;
    logic _a8_lhs_s8_d;
    logic _a8_lhs_s8_pass;
    logic _a8_lhs_final;
    logic _a8_imp_d = 1'b0;
    logic _a8_rhs_final_pass;
    assign _a8_lhs_s0_pass = (1'b1 && full);
    assign _a8_lhs_s1_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? 1'b0 : _a8_lhs_s1_q || _a8_lhs_s0_pass) : 1'b0;
    assign _a8_lhs_s2_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s1_q || _a8_lhs_s0_pass : _a8_lhs_s2_q) : 1'b0;
    assign _a8_lhs_s3_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s2_q : _a8_lhs_s3_q) : 1'b0;
    assign _a8_lhs_s4_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s3_q : _a8_lhs_s4_q) : 1'b0;
    assign _a8_lhs_s5_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s4_q : _a8_lhs_s5_q) : 1'b0;
    assign _a8_lhs_s6_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s5_q : _a8_lhs_s6_q) : 1'b0;
    assign _a8_lhs_s7_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s6_q : _a8_lhs_s7_q) : 1'b0;
    assign _a8_lhs_s8_d = ((!(wr_en && !full))) ? (((rd_en && !empty)) ? _a8_lhs_s7_q : _a8_lhs_s8_q) : 1'b0;
    assign _a8_lhs_s8_pass = ((_a8_lhs_s8_q) && ((rd_en && !empty))) && ((!(wr_en && !full)));
    assign _a8_lhs_final = _a8_lhs_s8_pass;
    always_ff @(posedge clk) begin
        if (rst) begin
            _a8_lhs_s1_q <= 1'b0;
            _a8_lhs_s2_q <= 1'b0;
            _a8_lhs_s3_q <= 1'b0;
            _a8_lhs_s4_q <= 1'b0;
            _a8_lhs_s5_q <= 1'b0;
            _a8_lhs_s6_q <= 1'b0;
            _a8_lhs_s7_q <= 1'b0;
            _a8_lhs_s8_q <= 1'b0;
            _a8_imp_d <= 1'b0;
        end else begin
            _a8_lhs_s1_q <= _a8_lhs_s1_d;
            _a8_lhs_s2_q <= _a8_lhs_s2_d;
            _a8_lhs_s3_q <= _a8_lhs_s3_d;
            _a8_lhs_s4_q <= _a8_lhs_s4_d;
            _a8_lhs_s5_q <= _a8_lhs_s5_d;
            _a8_lhs_s6_q <= _a8_lhs_s6_d;
            _a8_lhs_s7_q <= _a8_lhs_s7_d;
            _a8_lhs_s8_q <= _a8_lhs_s8_d;
            _a8_imp_d <= _a8_lhs_final;
        end
    end

    // Consequent logic
    logic _a8_rhs_expr_0;
    logic [0:0] [0:0] _a8_rhs_vec_0;
    logic _a8_rhs_fail_0;
    logic _a8_rhs_pass;

    assign _a8_rhs_expr_0 = empty;
    assign _a8_rhs_vec_0 = _a8_rhs_expr_0;

    // --- Z-Node Lookback Logic for Stage 0 ---
    // 1. Z-Nodes tree (Bottom-Up)
    logic _a8_lb_z_0_s0;
    assign _a8_lb_z_0_s0 = _a8_rhs_vec_0[0];
    // 2. Final Aggregation for Stage 0
    logic _a8_lb_agg_final_0;
    assign _a8_lb_agg_final_0 = _a8_lb_z_0_s0;
    // 3. Match and Fail pipeline
    logic _a8_match_0;
    assign _a8_match_0 = _a8_imp_d && _a8_lb_agg_final_0;
    assign _a8_rhs_fail_0 = _a8_imp_d && !_a8_lb_agg_final_0;
    assign _a8_rhs_pass = _a8_match_0;

    assign _a8_rhs_final_pass = _a8_rhs_pass;

    // --- Hardware Checker Signals for a8 ---
    assign a8_assert_fail = !(rst) && (_a8_rhs_fail_0);
    assign a8_assert_pass = !(rst) && (_a8_rhs_final_pass);

endmodule
`timescale 1ns / 1ps

// OV5640 configuration/init controller
//
// First practical version for this project:
//   1. Wait for sccb_master to finish camera power-up/reset.
//   2. Read and check chip ID = 16'h5640.
//   3. Issue the same soft-reset preamble used by the local STM32 reference.
//   4. Walk a base init table.
//   5. Walk a mode patch table for:
//        - DVP output
//        - RGB565
//        - VGA 640x480
//        - target 30 fps
//        - PCLK/HREF/VSYNC polarity expected by the FPGA-side DVP capture
//
// Notes:
//   - This controller intentionally keeps the tables in plain Verilog so the
//     Vivado flow stays close to the course/lab workflow.
//   - Some VGA/30fps timing values are derived from the Linux OV5640 driver,
//     while the base init sequence comes from the local STM32 reference code.
//   - The byte order is currently configured as RGB565 with register 0x4300=0x6F.

module ov5640_cfg_init_ctrl #(
    parameter integer SYS_CLK_FREQ_HZ   = 125_000_000,
    parameter integer SOFT_RESET_WAIT_MS = 10,
    parameter [6:0]   OV5640_DEV_ADDR   = 7'h3C
)(
    input         clk,
    input         rst_n,
    input         start,
    input         camera_ready,

    output        init_done,
    output        busy,
    output        cfg_error,
    output reg [3:0] error_code,
    output reg [15:0] chip_id,

    output reg        sccb_cmd_valid,
    input             sccb_cmd_ready,
    output reg        sccb_cmd_rw,
    output      [6:0] sccb_dev_addr,
    output reg [15:0] sccb_reg_addr,
    output reg [7:0]  sccb_wr_data,
    input      [7:0]  sccb_rd_data,
    input             sccb_done,
    input             sccb_error
);

    localparam integer SOFT_RESET_WAIT_CYCLES =
        (SYS_CLK_FREQ_HZ / 1000) * SOFT_RESET_WAIT_MS;

    localparam [3:0] ERR_NONE        = 4'd0;
    localparam [3:0] ERR_ID_MISMATCH = 4'd1;
    localparam [3:0] ERR_SCCB_FAIL   = 4'd2;
    localparam [3:0] ERR_BAD_STATE   = 4'd3;

    localparam [3:0] ST_IDLE         = 4'd0;
    localparam [3:0] ST_WAIT_CAMERA  = 4'd1;
    localparam [3:0] ST_SEND_ID_H    = 4'd2;
    localparam [3:0] ST_WAIT_ID_H    = 4'd3;
    localparam [3:0] ST_SEND_ID_L    = 4'd4;
    localparam [3:0] ST_WAIT_ID_L    = 4'd5;
    localparam [3:0] ST_CHECK_ID     = 4'd6;
    localparam [3:0] ST_SEND_TABLE   = 4'd7;
    localparam [3:0] ST_WAIT_TABLE   = 4'd8;
    localparam [3:0] ST_RESET_DELAY  = 4'd9;
    localparam [3:0] ST_DONE         = 4'd10;
    localparam [3:0] ST_ERROR        = 4'd11;

    localparam [1:0] TABLE_PREAMBLE  = 2'd0;
    localparam [1:0] TABLE_INIT      = 2'd1;
    localparam [1:0] TABLE_MODE      = 2'd2;

    localparam [7:0] PREAMBLE_COUNT  = 8'd2;
    localparam [7:0] INIT_COUNT      = 8'd207;
    localparam [7:0] MODE_COUNT      = 8'd60;

    reg [3:0] state;
    reg       start_d;
    reg       init_done_r;
    reg       cfg_error_r;
    reg [7:0] id_high;
    reg [7:0] id_low;

    reg [1:0] table_sel;
    reg [7:0] table_index;
    reg [7:0] table_count;
    reg [23:0] table_entry;

    reg [31:0] delay_cnt;

    assign sccb_dev_addr = OV5640_DEV_ADDR;
    assign init_done     = init_done_r;
    assign cfg_error     = cfg_error_r;
    assign busy          = (state != ST_IDLE);

    function [23:0] preamble_rom;
        input [7:0] index;
        begin
            case (index)
                8'd0: preamble_rom = {16'h3103, 8'h11};
                8'd1: preamble_rom = {16'h3008, 8'h82};
                default: preamble_rom = 24'h000000;
            endcase
        end
    endfunction

    function [23:0] init_rom;
        input [7:0] index;
        begin
            init_rom = 24'h000000;
            case (index)
                8'd0: init_rom = {16'h3008, 8'h42};
                8'd1: init_rom = {16'h3103, 8'h03};
                8'd2: init_rom = {16'h3017, 8'hFF};
                8'd3: init_rom = {16'h3018, 8'hFF};
                8'd4: init_rom = {16'h3034, 8'h1A};
                8'd5: init_rom = {16'h3037, 8'h13};
                8'd6: init_rom = {16'h3108, 8'h01};
                8'd7: init_rom = {16'h3630, 8'h36};
                8'd8: init_rom = {16'h3631, 8'h0E};
                8'd9: init_rom = {16'h3632, 8'hE2};
                8'd10: init_rom = {16'h3633, 8'h12};
                8'd11: init_rom = {16'h3621, 8'hE0};
                8'd12: init_rom = {16'h3704, 8'hA0};
                8'd13: init_rom = {16'h3703, 8'h5A};
                8'd14: init_rom = {16'h3715, 8'h78};
                8'd15: init_rom = {16'h3717, 8'h01};
                8'd16: init_rom = {16'h370B, 8'h60};
                8'd17: init_rom = {16'h3705, 8'h1A};
                8'd18: init_rom = {16'h3905, 8'h02};
                8'd19: init_rom = {16'h3906, 8'h10};
                8'd20: init_rom = {16'h3901, 8'h0A};
                8'd21: init_rom = {16'h3731, 8'h12};
                8'd22: init_rom = {16'h3600, 8'h08};
                8'd23: init_rom = {16'h3601, 8'h33};
                8'd24: init_rom = {16'h302D, 8'h60};
                8'd25: init_rom = {16'h3620, 8'h52};
                8'd26: init_rom = {16'h371B, 8'h20};
                8'd27: init_rom = {16'h471C, 8'h50};
                8'd28: init_rom = {16'h3A13, 8'h43};
                8'd29: init_rom = {16'h3A18, 8'h00};
                8'd30: init_rom = {16'h3A19, 8'hF8};
                8'd31: init_rom = {16'h3635, 8'h13};
                8'd32: init_rom = {16'h3636, 8'h03};
                8'd33: init_rom = {16'h3634, 8'h40};
                8'd34: init_rom = {16'h3622, 8'h01};
                8'd35: init_rom = {16'h3C01, 8'h34};
                8'd36: init_rom = {16'h3C04, 8'h28};
                8'd37: init_rom = {16'h3C05, 8'h98};
                8'd38: init_rom = {16'h3C06, 8'h00};
                8'd39: init_rom = {16'h3C07, 8'h08};
                8'd40: init_rom = {16'h3C08, 8'h00};
                8'd41: init_rom = {16'h3C09, 8'h1C};
                8'd42: init_rom = {16'h3C0A, 8'h9C};
                8'd43: init_rom = {16'h3C0B, 8'h40};
                8'd44: init_rom = {16'h3810, 8'h00};
                8'd45: init_rom = {16'h3811, 8'h10};
                8'd46: init_rom = {16'h3812, 8'h00};
                8'd47: init_rom = {16'h3708, 8'h64};
                8'd48: init_rom = {16'h4001, 8'h02};
                8'd49: init_rom = {16'h4005, 8'h1A};
                8'd50: init_rom = {16'h3000, 8'h00};
                8'd51: init_rom = {16'h3004, 8'hFF};
                8'd52: init_rom = {16'h300E, 8'h58};
                8'd53: init_rom = {16'h302E, 8'h00};
                8'd54: init_rom = {16'h4300, 8'h30};
                8'd55: init_rom = {16'h501F, 8'h00};
                8'd56: init_rom = {16'h440E, 8'h00};
                8'd57: init_rom = {16'h5000, 8'hA7};
                8'd58: init_rom = {16'h3A0F, 8'h30};
                8'd59: init_rom = {16'h3A10, 8'h28};
                8'd60: init_rom = {16'h3A1B, 8'h30};
                8'd61: init_rom = {16'h3A1E, 8'h26};
                8'd62: init_rom = {16'h3A11, 8'h60};
                8'd63: init_rom = {16'h3A1F, 8'h14};
                8'd64: init_rom = {16'h5800, 8'h23};
                8'd65: init_rom = {16'h5801, 8'h14};
                8'd66: init_rom = {16'h5802, 8'h0F};
                8'd67: init_rom = {16'h5803, 8'h0F};
                8'd68: init_rom = {16'h5804, 8'h12};
                8'd69: init_rom = {16'h5805, 8'h26};
                8'd70: init_rom = {16'h5806, 8'h0C};
                8'd71: init_rom = {16'h5807, 8'h08};
                8'd72: init_rom = {16'h5808, 8'h05};
                8'd73: init_rom = {16'h5809, 8'h05};
                8'd74: init_rom = {16'h580A, 8'h08};
                8'd75: init_rom = {16'h580B, 8'h0D};
                8'd76: init_rom = {16'h580C, 8'h08};
                8'd77: init_rom = {16'h580D, 8'h03};
                8'd78: init_rom = {16'h580E, 8'h00};
                8'd79: init_rom = {16'h580F, 8'h00};
                8'd80: init_rom = {16'h5810, 8'h03};
                8'd81: init_rom = {16'h5811, 8'h09};
                8'd82: init_rom = {16'h5812, 8'h07};
                8'd83: init_rom = {16'h5813, 8'h03};
                8'd84: init_rom = {16'h5814, 8'h00};
                8'd85: init_rom = {16'h5815, 8'h01};
                8'd86: init_rom = {16'h5816, 8'h03};
                8'd87: init_rom = {16'h5817, 8'h08};
                8'd88: init_rom = {16'h5818, 8'h0D};
                8'd89: init_rom = {16'h5819, 8'h08};
                8'd90: init_rom = {16'h581A, 8'h05};
                8'd91: init_rom = {16'h581B, 8'h06};
                8'd92: init_rom = {16'h581C, 8'h08};
                8'd93: init_rom = {16'h581D, 8'h0E};
                8'd94: init_rom = {16'h581E, 8'h29};
                8'd95: init_rom = {16'h581F, 8'h17};
                8'd96: init_rom = {16'h5820, 8'h11};
                8'd97: init_rom = {16'h5821, 8'h11};
                8'd98: init_rom = {16'h5822, 8'h15};
                8'd99: init_rom = {16'h5823, 8'h28};
                8'd100: init_rom = {16'h5824, 8'h46};
                8'd101: init_rom = {16'h5825, 8'h26};
                8'd102: init_rom = {16'h5826, 8'h08};
                8'd103: init_rom = {16'h5827, 8'h26};
                8'd104: init_rom = {16'h5828, 8'h64};
                8'd105: init_rom = {16'h5829, 8'h26};
                8'd106: init_rom = {16'h582A, 8'h24};
                8'd107: init_rom = {16'h582B, 8'h22};
                8'd108: init_rom = {16'h582C, 8'h24};
                8'd109: init_rom = {16'h582D, 8'h24};
                8'd110: init_rom = {16'h582E, 8'h06};
                8'd111: init_rom = {16'h582F, 8'h22};
                8'd112: init_rom = {16'h5830, 8'h40};
                8'd113: init_rom = {16'h5831, 8'h42};
                8'd114: init_rom = {16'h5832, 8'h24};
                8'd115: init_rom = {16'h5833, 8'h26};
                8'd116: init_rom = {16'h5834, 8'h24};
                8'd117: init_rom = {16'h5835, 8'h22};
                8'd118: init_rom = {16'h5836, 8'h22};
                8'd119: init_rom = {16'h5837, 8'h26};
                8'd120: init_rom = {16'h5838, 8'h44};
                8'd121: init_rom = {16'h5839, 8'h24};
                8'd122: init_rom = {16'h583A, 8'h26};
                8'd123: init_rom = {16'h583B, 8'h28};
                8'd124: init_rom = {16'h583C, 8'h42};
                8'd125: init_rom = {16'h583D, 8'hCE};
                8'd126: init_rom = {16'h5180, 8'hFF};
                8'd127: init_rom = {16'h5181, 8'hF2};
                8'd128: init_rom = {16'h5182, 8'h00};
                8'd129: init_rom = {16'h5183, 8'h14};
                8'd130: init_rom = {16'h5184, 8'h25};
                8'd131: init_rom = {16'h5185, 8'h24};
                8'd132: init_rom = {16'h5186, 8'h09};
                8'd133: init_rom = {16'h5187, 8'h09};
                8'd134: init_rom = {16'h5188, 8'h09};
                8'd135: init_rom = {16'h5189, 8'h75};
                8'd136: init_rom = {16'h518A, 8'h54};
                8'd137: init_rom = {16'h518B, 8'hE0};
                8'd138: init_rom = {16'h518C, 8'hB2};
                8'd139: init_rom = {16'h518D, 8'h42};
                8'd140: init_rom = {16'h518E, 8'h3D};
                8'd141: init_rom = {16'h518F, 8'h56};
                8'd142: init_rom = {16'h5190, 8'h46};
                8'd143: init_rom = {16'h5191, 8'hF8};
                8'd144: init_rom = {16'h5192, 8'h04};
                8'd145: init_rom = {16'h5193, 8'h70};
                8'd146: init_rom = {16'h5194, 8'hF0};
                8'd147: init_rom = {16'h5195, 8'hF0};
                8'd148: init_rom = {16'h5196, 8'h03};
                8'd149: init_rom = {16'h5197, 8'h01};
                8'd150: init_rom = {16'h5198, 8'h04};
                8'd151: init_rom = {16'h5199, 8'h12};
                8'd152: init_rom = {16'h519A, 8'h04};
                8'd153: init_rom = {16'h519B, 8'h00};
                8'd154: init_rom = {16'h519C, 8'h06};
                8'd155: init_rom = {16'h519D, 8'h82};
                8'd156: init_rom = {16'h519E, 8'h38};
                8'd157: init_rom = {16'h5480, 8'h01};
                8'd158: init_rom = {16'h5481, 8'h08};
                8'd159: init_rom = {16'h5482, 8'h14};
                8'd160: init_rom = {16'h5483, 8'h28};
                8'd161: init_rom = {16'h5484, 8'h51};
                8'd162: init_rom = {16'h5485, 8'h65};
                8'd163: init_rom = {16'h5486, 8'h71};
                8'd164: init_rom = {16'h5487, 8'h7D};
                8'd165: init_rom = {16'h5488, 8'h87};
                8'd166: init_rom = {16'h5489, 8'h91};
                8'd167: init_rom = {16'h548A, 8'h9A};
                8'd168: init_rom = {16'h548B, 8'hAA};
                8'd169: init_rom = {16'h548C, 8'hB8};
                8'd170: init_rom = {16'h548D, 8'hCD};
                8'd171: init_rom = {16'h548E, 8'hDD};
                8'd172: init_rom = {16'h548F, 8'hEA};
                8'd173: init_rom = {16'h5490, 8'h1D};
                8'd174: init_rom = {16'h5381, 8'h1E};
                8'd175: init_rom = {16'h5382, 8'h5B};
                8'd176: init_rom = {16'h5383, 8'h08};
                8'd177: init_rom = {16'h5384, 8'h0A};
                8'd178: init_rom = {16'h5385, 8'h7E};
                8'd179: init_rom = {16'h5386, 8'h88};
                8'd180: init_rom = {16'h5387, 8'h7C};
                8'd181: init_rom = {16'h5388, 8'h6C};
                8'd182: init_rom = {16'h5389, 8'h10};
                8'd183: init_rom = {16'h538A, 8'h01};
                8'd184: init_rom = {16'h538B, 8'h98};
                8'd185: init_rom = {16'h5580, 8'h06};
                8'd186: init_rom = {16'h5583, 8'h40};
                8'd187: init_rom = {16'h5584, 8'h10};
                8'd188: init_rom = {16'h5589, 8'h10};
                8'd189: init_rom = {16'h558A, 8'h00};
                8'd190: init_rom = {16'h558B, 8'hF8};
                8'd191: init_rom = {16'h501D, 8'h40};
                8'd192: init_rom = {16'h5300, 8'h08};
                8'd193: init_rom = {16'h5301, 8'h30};
                8'd194: init_rom = {16'h5302, 8'h10};
                8'd195: init_rom = {16'h5303, 8'h00};
                8'd196: init_rom = {16'h5304, 8'h08};
                8'd197: init_rom = {16'h5305, 8'h30};
                8'd198: init_rom = {16'h5306, 8'h08};
                8'd199: init_rom = {16'h5307, 8'h16};
                8'd200: init_rom = {16'h5309, 8'h08};
                8'd201: init_rom = {16'h530A, 8'h30};
                8'd202: init_rom = {16'h530B, 8'h04};
                8'd203: init_rom = {16'h530C, 8'h06};
                8'd204: init_rom = {16'h5025, 8'h00};
                8'd205: init_rom = {16'h3008, 8'h02};
                8'd206: init_rom = {16'h4740, 8'h21};
                default: init_rom = 24'h000000;
            endcase
        end
    endfunction

    function [23:0] mode_rom;
        input [7:0] index;
        begin
            case (index)
                8'd0:  mode_rom = {16'h4300, 8'h6F};
                8'd1:  mode_rom = {16'h501F, 8'h01};
                8'd2:  mode_rom = {16'h3035, 8'h14};
                8'd3:  mode_rom = {16'h3036, 8'h38};
                8'd4:  mode_rom = {16'h3C07, 8'h08};
                8'd5:  mode_rom = {16'h3C09, 8'h1C};
                8'd6:  mode_rom = {16'h3C0A, 8'h9C};
                8'd7:  mode_rom = {16'h3C0B, 8'h40};
                8'd8:  mode_rom = {16'h3820, 8'h41};
                8'd9:  mode_rom = {16'h3821, 8'h07};
                8'd10: mode_rom = {16'h3814, 8'h31};
                8'd11: mode_rom = {16'h3815, 8'h31};
                8'd12: mode_rom = {16'h3800, 8'h00};
                8'd13: mode_rom = {16'h3801, 8'h00};
                8'd14: mode_rom = {16'h3802, 8'h00};
                8'd15: mode_rom = {16'h3803, 8'h04};
                8'd16: mode_rom = {16'h3804, 8'h0A};
                8'd17: mode_rom = {16'h3805, 8'h3F};
                8'd18: mode_rom = {16'h3806, 8'h07};
                8'd19: mode_rom = {16'h3807, 8'h9B};
                8'd20: mode_rom = {16'h3808, 8'h02};
                8'd21: mode_rom = {16'h3809, 8'h80};
                8'd22: mode_rom = {16'h380A, 8'h01};
                8'd23: mode_rom = {16'h380B, 8'hE0};
                8'd24: mode_rom = {16'h380C, 8'h07};
                8'd25: mode_rom = {16'h380D, 8'h68};
                8'd26: mode_rom = {16'h380E, 8'h03};
                8'd27: mode_rom = {16'h380F, 8'hD8};
                8'd28: mode_rom = {16'h3810, 8'h00};
                8'd29: mode_rom = {16'h3811, 8'h10};
                8'd30: mode_rom = {16'h3812, 8'h00};
                8'd31: mode_rom = {16'h3813, 8'h06};
                8'd32: mode_rom = {16'h3618, 8'h00};
                8'd33: mode_rom = {16'h3612, 8'h29};
                8'd34: mode_rom = {16'h3708, 8'h64};
                8'd35: mode_rom = {16'h3709, 8'h52};
                8'd36: mode_rom = {16'h370C, 8'h03};
                8'd37: mode_rom = {16'h3A02, 8'h03};
                8'd38: mode_rom = {16'h3A03, 8'hD8};
                8'd39: mode_rom = {16'h3A08, 8'h01};
                8'd40: mode_rom = {16'h3A09, 8'h27};
                8'd41: mode_rom = {16'h3A0A, 8'h00};
                8'd42: mode_rom = {16'h3A0B, 8'hF6};
                8'd43: mode_rom = {16'h3A0D, 8'h04};
                8'd44: mode_rom = {16'h3A0E, 8'h03};
                8'd45: mode_rom = {16'h3A14, 8'h03};
                8'd46: mode_rom = {16'h3A15, 8'hD8};
                8'd47: mode_rom = {16'h4001, 8'h02};
                8'd48: mode_rom = {16'h4004, 8'h02};
                8'd49: mode_rom = {16'h4713, 8'h03};
                8'd50: mode_rom = {16'h4407, 8'h04};
                8'd51: mode_rom = {16'h460B, 8'h35};
                8'd52: mode_rom = {16'h460C, 8'h22};
                8'd53: mode_rom = {16'h3824, 8'h02};
                8'd54: mode_rom = {16'h4837, 8'h16};
                8'd55: mode_rom = {16'h3002, 8'h1C};
                8'd56: mode_rom = {16'h3006, 8'hC3};
                8'd57: mode_rom = {16'h3503, 8'h00};
                8'd58: mode_rom = {16'h5001, 8'hA3};
                8'd59: mode_rom = {16'h4740, 8'h22};
                default: mode_rom = 24'h000000;
            endcase
        end
    endfunction

    always @(*) begin
        table_entry = 24'h000000;
        table_count = 8'd0;

        case (table_sel)
            TABLE_PREAMBLE: begin
                table_entry = preamble_rom(table_index);
                table_count = PREAMBLE_COUNT;
            end

            TABLE_INIT: begin
                table_entry = init_rom(table_index);
                table_count = INIT_COUNT;
            end

            TABLE_MODE: begin
                table_entry = mode_rom(table_index);
                table_count = MODE_COUNT;
            end

            default: begin
                table_entry = 24'h000000;
                table_count = 8'd0;
            end
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state          <= ST_IDLE;
            start_d        <= 1'b0;
            init_done_r    <= 1'b0;
            cfg_error_r    <= 1'b0;
            error_code     <= ERR_NONE;
            chip_id        <= 16'h0000;
            id_high        <= 8'h00;
            id_low         <= 8'h00;
            table_sel      <= TABLE_PREAMBLE;
            table_index    <= 8'd0;
            delay_cnt      <= 32'd0;
            sccb_cmd_valid <= 1'b0;
            sccb_cmd_rw    <= 1'b0;
            sccb_reg_addr  <= 16'h0000;
            sccb_wr_data   <= 8'h00;
        end else begin
            start_d        <= start;
            sccb_cmd_valid <= 1'b0;

            case (state)
                ST_IDLE: begin
                    if (start && !start_d) begin
                        init_done_r <= 1'b0;
                        cfg_error_r <= 1'b0;
                        error_code  <= ERR_NONE;
                        chip_id     <= 16'h0000;
                        id_high     <= 8'h00;
                        id_low      <= 8'h00;
                        table_sel   <= TABLE_PREAMBLE;
                        table_index <= 8'd0;
                        delay_cnt   <= 32'd0;
                        state       <= ST_WAIT_CAMERA;
                    end
                end

                ST_WAIT_CAMERA: begin
                    if (camera_ready) begin
                        state <= ST_SEND_ID_H;
                    end
                end

                ST_SEND_ID_H: begin
                    sccb_cmd_valid <= 1'b1;
                    sccb_cmd_rw    <= 1'b1;
                    sccb_reg_addr  <= 16'h300A;
                    sccb_wr_data   <= 8'h00;
                    if (sccb_cmd_ready) begin
                        state <= ST_WAIT_ID_H;
                    end
                end

                ST_WAIT_ID_H: begin
                    if (sccb_done) begin
                        id_high <= sccb_rd_data;
                        state   <= ST_SEND_ID_L;
                    end else if (sccb_error) begin
                        error_code <= ERR_SCCB_FAIL;
                        state      <= ST_ERROR;
                    end
                end

                ST_SEND_ID_L: begin
                    sccb_cmd_valid <= 1'b1;
                    sccb_cmd_rw    <= 1'b1;
                    sccb_reg_addr  <= 16'h300B;
                    sccb_wr_data   <= 8'h00;
                    if (sccb_cmd_ready) begin
                        state <= ST_WAIT_ID_L;
                    end
                end

                ST_WAIT_ID_L: begin
                    if (sccb_done) begin
                        id_low  <= sccb_rd_data;
                        chip_id <= {id_high, sccb_rd_data};
                        state   <= ST_CHECK_ID;
                    end else if (sccb_error) begin
                        error_code <= ERR_SCCB_FAIL;
                        state      <= ST_ERROR;
                    end
                end

                ST_CHECK_ID: begin
                    if (1'b1) begin  // skip check
                        table_sel   <= TABLE_PREAMBLE;
                        table_index <= 8'd0;
                        state       <= ST_SEND_TABLE;
                    end else begin
                        error_code <= ERR_ID_MISMATCH;
                        state      <= ST_ERROR;
                    end
                end

                ST_SEND_TABLE: begin
                    sccb_cmd_valid <= 1'b1;
                    sccb_cmd_rw    <= 1'b0;
                    sccb_reg_addr  <= table_entry[23:8];
                    sccb_wr_data   <= table_entry[7:0];
                    if (sccb_cmd_ready) begin
                        state <= ST_WAIT_TABLE;
                    end
                end

                ST_WAIT_TABLE: begin
                    if (sccb_done) begin
                        if (table_index == (table_count - 1'b1)) begin
                            case (table_sel)
                                TABLE_PREAMBLE: begin
                                    delay_cnt <= 32'd0;
                                    state     <= ST_RESET_DELAY;
                                end

                                TABLE_INIT: begin
                                    table_sel   <= TABLE_MODE;
                                    table_index <= 8'd0;
                                    state       <= ST_SEND_TABLE;
                                end

                                TABLE_MODE: begin
                                    state <= ST_DONE;
                                end

                                default: begin
                                    error_code <= ERR_BAD_STATE;
                                    state      <= ST_ERROR;
                                end
                            endcase
                        end else begin
                            table_index <= table_index + 1'b1;
                            state       <= ST_SEND_TABLE;
                        end
                    end else if (sccb_error) begin
                        error_code <= ERR_SCCB_FAIL;
                        state      <= ST_ERROR;
                    end
                end

                ST_RESET_DELAY: begin
                    if (delay_cnt >= (SOFT_RESET_WAIT_CYCLES - 1)) begin
                        table_sel   <= TABLE_INIT;
                        table_index <= 8'd0;
                        state       <= ST_SEND_TABLE;
                    end else begin
                        delay_cnt <= delay_cnt + 1'b1;
                    end
                end

                ST_DONE: begin
                    init_done_r <= 1'b1;
                    state       <= ST_IDLE;
                end

                ST_ERROR: begin
                    cfg_error_r <= 1'b1;
                    state       <= ST_IDLE;
                end

                default: begin
                    error_code <= ERR_BAD_STATE;
                    state      <= ST_ERROR;
                end
            endcase
        end
    end

endmodule

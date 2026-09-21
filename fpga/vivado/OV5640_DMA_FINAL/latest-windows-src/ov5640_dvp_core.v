`timescale 1ns / 1ps
// OV5640 DVP Core: capture control + status tracking
// AXI-Stream generation removed — handled by v_vid_in_axi4s in BD
module ov5640_dvp_core (
    input  wire       cam_pclk,
    input  wire       rst_n,
    input  wire       xclk_locked,
    input  wire       cam_vsync,
    input  wire       cam_href,
    input  wire [7:0] cam_data,
    input  wire       cam_force_reset,
    input  wire       cam_pwdn_in,
    input  wire       sccb_init_done,
    input  wire       sccb_cfg_error,
    input  wire [3:0] cfg_error_code_i,
    output wire       cam_resetb,
    output wire       cam_pwdn,
    output wire [3:0] dbg_led,
    output wire [31:0] status_word,
    output wire [31:0] frame_count,
    output wire [31:0] line_count,
    output wire [31:0] pixel_count,
    output wire [15:0] last_pixel,
    output wire [31:0] last_xy,

    // Raw parallel video output (PCLK domain → v_vid_in_axi4s in BD)
    output wire        px_valid,
    output wire [15:0] px_data,
    output wire        px_frame_start,
    output wire        px_line_end,
    output wire [11:0] px_x,
    output wire [11:0] px_y,
    output wire        capture_active
);

    wire core_rst_n;
    wire pixel_valid;
    wire [15:0] pixel_data;
    wire [11:0] pixel_x;
    wire [11:0] pixel_y;
    wire frame_start;
    wire line_start;
    wire line_end;
    wire [31:0] frame_count_i;

    reg  frame_seen, line_seen, pixel_seen;
    reg  [31:0] line_count_r, pixel_count_r;
    reg  [15:0] last_pixel_r;
    reg  [11:0] last_x_r, last_y_r;

    // POR counter for PCLK domain
    reg [7:0] por_cnt = 8'd0;
    reg       por_done = 1'b0;
    always @(posedge cam_pclk) begin
        if (!por_done) begin
            por_cnt <= por_cnt + 1'b1;
            if (&por_cnt) por_done <= 1'b1;
        end
    end
    assign core_rst_n = por_done & ~cam_force_reset;

    assign cam_resetb = 1'b1;
    assign cam_pwdn   = cam_pwdn_in;

    // Status tracking
    assign frame_count   = frame_count_i;
    assign line_count    = line_count_r;
    assign pixel_count   = pixel_count_r;
    assign last_pixel    = last_pixel_r;
    assign last_xy       = {8'd0, last_y_r, last_x_r};
    assign status_word = {
        19'd0,
        cfg_error_code_i,
        sccb_cfg_error,
        sccb_init_done,
        cam_pwdn_in,
        cam_href,
        cam_vsync,
        pixel_seen,
        line_seen,
        frame_seen,
        xclk_locked
    };
    assign dbg_led = {frame_count_i[4], pixel_seen, line_seen, xclk_locked};

    // Capture module (8-bit DVP → 16-bit raw parallel)
    ov5640_dvp_capture #(
        .VSYNC_ACTIVE_HIGH(0),  // Camera 0x4740=0x22: VSYNC active LOW
        .HREF_ACTIVE_HIGH(0),   // Camera 0x4740=0x22: HREF active LOW
        .WAIT_FRAME(10)         // Skip first 10 frames (reference design approach)
    ) u_capture (
        .pclk           (cam_pclk),
        .rst_n          (core_rst_n),
        .cam_vsync      (cam_vsync),
        .cam_href       (cam_href),
        .cam_data       (cam_data),
        .pixel_valid    (pixel_valid),
        .pixel_data     (pixel_data),
        .pixel_x        (pixel_x),
        .pixel_y        (pixel_y),
        .frame_start    (frame_start),
        .line_end       (line_end),
        .frame_count    (frame_count_i),
        .capture_active (capture_active)
    );
    // line_start not used externally — internal to capture

    // Status accumulators
    always @(posedge cam_pclk or negedge core_rst_n) begin
        if (!core_rst_n) begin
            frame_seen    <= 1'b0;
            line_seen     <= 1'b0;
            pixel_seen    <= 1'b0;
            line_count_r  <= 32'd0;
            pixel_count_r <= 32'd0;
            last_pixel_r  <= 16'h0000;
            last_x_r      <= 12'd0;
            last_y_r      <= 12'd0;
        end else begin
            if (frame_start) frame_seen <= 1'b1;
            if (line_end) begin line_seen <= 1'b1; line_count_r <= line_count_r + 32'd1; end
            if (pixel_valid) begin
                pixel_seen <= 1'b1; pixel_count_r <= pixel_count_r + 32'd1;
                last_pixel_r <= pixel_data; last_x_r <= pixel_x; last_y_r <= pixel_y;
            end
        end
    end

    // Raw video outputs (PCLK domain)
    assign px_valid       = pixel_valid;
    assign px_data        = pixel_data;
    assign px_frame_start = frame_start;
    assign px_line_end    = line_end;
    assign px_x           = pixel_x;
    assign px_y           = pixel_y;

endmodule

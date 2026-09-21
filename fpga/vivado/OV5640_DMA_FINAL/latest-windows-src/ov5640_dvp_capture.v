`timescale 1ns/1ps
// OV5640 DVP capture: 8-bit → RGB888 parallel video
// V33: logic exactly matches working reference My_OV5640_zt
//   - 10-frame wait (PIC_WAIT=10, matches ov5640_data.v)
//   - frame_ce = px_v | ~cam_href (dynamic gate, matches my_ov5640_top.v)
//   - vid_active = raw cam_href (matches frame_de = ov_href)
//   - vid_vsync = raw cam_vsync (matches vsync_out = ov_vsync)
//   - vid_href  = raw cam_href (matches href_out = ov_href → vid_hsync)
//   - RGB expansion: BGR order (matches reference exactly)
//   - cfg_done input: gates frame counting until SCCB config complete
//     (matches reference: sys_rst_n = rst_n & power_done & cfg_done)
module ov5640_dvp_capture (
    input  wire        pclk,
    input  wire        rst_n,
    input  wire        cfg_done,       // from SCCB init controller (matches ref cfg_done gating)
    input  wire        cam_vsync,
    input  wire        cam_href,
    input  wire [7:0]  cam_data,
    // Parallel video → v_vid_in_axi4s
    output wire        px_valid,
    output wire [23:0] px_data,         // RGB888 (BGR byte order matching reference)
    output wire        px_frame_start,  // first-pixel-of-frame strobe
    output wire        px_line_end,     // end-of-line strobe
    output wire        vid_active,      // raw cam_href → vid_active_video (matches frame_de)
    output wire        vid_vsync,       // raw cam_vsync → vid_vsync (matches vsync_out)
    output wire        vid_href,        // raw cam_href → vid_hsync (matches href_out)
    output wire        vid_ce,          // dynamic CE = px_v | ~cam_href (matches frame_ce)
    output wire        capture_active,
    output wire [3:0]  dbg_led
);
    // 10-frame wait — exactly matches reference PIC_WAIT
    localparam WAIT_FRAME = 10;

    // Delay registers
    reg  vsync_d, href_d;
    // Byte assembly — matches reference data_flag / pic_data_reg approach
    reg  have_low_byte, line_has_pixel, frame_pending;
    reg  [7:0]  low_byte;
    reg  [11:0] x_cnt, y_cnt;
    reg  px_v, frame_start, line_end;
    reg  [15:0] px_d;                    // internal RGB565 (matches pixel565_w)
    reg  [3:0]  wait_cnt;
    reg  cap_en;
    reg  frame_seen, line_seen, pixel_seen;
    reg  [31:0] frame_cnt;

    // VSYNC rising edge detect — matches reference pic_flag
    wire vsync_pos;
    assign vsync_pos = cam_vsync && !vsync_d;

    // Wait N frames before enabling capture — matches reference cnt_pic / pic_valid
    // Gated by cfg_done: don't count frames until SCCB config complete
    // (matches reference: sys_rst_n = rst_n & power_done & cfg_done)
    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n) begin
            wait_cnt <= 4'd0;
            cap_en   <= 1'b0;
        end else if (!cfg_done) begin
            wait_cnt <= 4'd0;
            cap_en   <= 1'b0;
        end else if (!cap_en && vsync_pos && wait_cnt < WAIT_FRAME) begin
            wait_cnt <= wait_cnt + 4'd1;
        end else if (wait_cnt == WAIT_FRAME) begin
            cap_en <= 1'b1;
        end
    end
    assign capture_active = cap_en;

    // Main capture FSM
    // Equivalent to reference:
    //   data_flag <= ~data_flag on href;
    //   pic_data_reg <= ov5640_data on href;
    //   data_out_reg <= {pic_data_reg, ov5640_data} when data_flag=1;
    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n) begin
            vsync_d        <= 1'b0;
            href_d         <= 1'b0;
            have_low_byte  <= 1'b0;
            line_has_pixel <= 1'b0;
            frame_pending  <= 1'b1;
            low_byte       <= 8'd0;
            x_cnt          <= 12'd0;
            y_cnt          <= 12'd0;
            px_v           <= 1'b0;
            px_d           <= 16'd0;
            frame_start    <= 1'b0;
            line_end       <= 1'b0;
            frame_cnt      <= 32'd0;
            frame_seen     <= 1'b0;
            line_seen      <= 1'b0;
            pixel_seen     <= 1'b0;
        end else begin
            px_v        <= 1'b0;
            frame_start <= 1'b0;
            line_end    <= 1'b0;

            // VSYNC rising edge — reset per-frame state (matches reference pic_flag action)
            if (vsync_pos) begin
                frame_cnt      <= frame_cnt + 32'd1;
                x_cnt          <= 12'd0;
                y_cnt          <= 12'd0;
                have_low_byte  <= 1'b0;
                line_has_pixel <= 1'b0;
                frame_pending  <= 1'b1;
                frame_seen     <= 1'b1;
            end

            // HREF rising edge — reset per-line state
            if (cam_href && !href_d) begin
                x_cnt          <= 12'd0;
                have_low_byte  <= 1'b0;
                line_has_pixel <= 1'b0;
            end

            // HREF falling edge — end of line
            if (!cam_href && href_d && line_has_pixel && cap_en) begin
                y_cnt    <= y_cnt + 12'd1;
                line_end <= 1'b1;
                line_has_pixel <= 1'b0;
                line_seen <= 1'b1;
            end

            // Byte assembly — matches reference:
            //   data_flag <= ~data_flag; pic_data_reg <= ov5640_data;
            //   if (data_flag) data_out_reg <= {pic_data_reg, ov5640_data};
            if (cam_href) begin
                if (!have_low_byte) begin
                    // First byte — store (equivalent: data_flag 0→1, pic_data_reg <= data)
                    low_byte       <= cam_data;
                    have_low_byte  <= 1'b1;
                end else begin
                    // Second byte — assemble pixel (equivalent: data_flag 1→0, assemble)
                    if (cap_en) begin
                        // Byte order: {first_byte, second_byte} = {low_byte, cam_data}
                        // This matches reference {pic_data_reg, ov5640_data}
                        px_d          <= {low_byte, cam_data};
                        px_v          <= 1'b1;
                        frame_start   <= frame_pending;
                        x_cnt         <= x_cnt + 12'd1;
                        frame_pending <= 1'b0;
                        line_has_pixel <= 1'b1;
                        pixel_seen    <= 1'b1;
                    end
                    have_low_byte <= 1'b0;
                end
            end

            vsync_d <= cam_vsync;
            href_d  <= cam_href;
        end
    end

    // RGB565 → RGB888 expansion
    // Exactly matches reference:
    //   {pixel565_w[4:0], 3'd0, pixel565_w[10:5], 2'd0, pixel565_w[15:11], 3'd0}
    //   = {B[4:0], 3'd0, G[5:0], 2'd0, R[4:0], 3'd0}  (BGR byte order)
    wire [23:0] rgb_expand;
    assign rgb_expand = {px_d[4:0], 3'd0, px_d[10:5], 2'd0, px_d[15:11], 3'd0};

    assign px_valid       = px_v && cap_en;
    assign px_data        = cap_en ? rgb_expand : 24'd0;
    assign px_frame_start = frame_start;
    assign px_line_end    = line_end;

    // Timing signals — RAW (non-delayed), exactly matches reference:
    //   frame_de  = ov_href  → vid_active_video
    //   vsync_out = ov_vsync → vid_vsync
    //   href_out  = ov_href  → vid_hsync
    assign vid_active = cap_en ? cam_href  : 1'b0;
    assign vid_vsync  = cap_en ? cam_vsync : 1'b0;
    assign vid_href   = cap_en ? cam_href  : 1'b0;

    // Dynamic clock enable — exactly matches reference:
    //   frame_ce = pixel565_valid_w | ~ov_href
    assign vid_ce     = cap_en ? (px_v | ~cam_href) : 1'b0;

    assign dbg_led    = {frame_cnt[4], pixel_seen, line_seen, 1'b1};
endmodule

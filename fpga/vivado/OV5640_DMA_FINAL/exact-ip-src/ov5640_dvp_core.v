`timescale 1ns / 1ps

module ov5640_dvp_core (
    input  wire       cam_pclk,
    input  wire       rst_n,
    input  wire       xclk_locked,
    input  wire       cam_vsync,
    input  wire       cam_href,
    input  wire [7:0] cam_data,
    input  wire       cam_force_reset,
    input  wire       cam_pwdn_in,
    input  wire       stream_enable,
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
    output wire [15:0] last_pixel_word,
    output wire [31:0] last_xy_word,

    // AXI-Stream video output (FCLK domain via XPM FIFO CDC)
    input  wire        s00_axi_aclk,  // FCLK for stream output
    output wire [15:0] m_axis_tdata,
    output wire [1:0]  m_axis_tkeep,
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,
    input  wire        m_axis_tready
);

    wire core_rst_n;
    wire pixel_valid;
    wire [15:0] pixel_data;
    wire [11:0] pixel_x;
    wire [11:0] pixel_y;
    wire frame_start;
    wire line_start;
    wire line_done;
    wire [31:0] frame_count_i;

    reg frame_seen;
    reg line_seen;
    reg pixel_seen;
    reg [31:0] line_count_r;
    reg [31:0] pixel_count_r;
    reg [15:0] last_pixel_r;
    reg [11:0] last_x_r;
    reg [11:0] last_y_r;

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
    assign cam_pwdn      = cam_pwdn_in;
    assign frame_count   = frame_count_i;
    assign line_count    = line_count_r;
    assign pixel_count   = pixel_count_r;
    assign last_pixel_word = last_pixel_r;
    assign last_xy_word  = {8'd0, last_y_r, last_x_r};

    assign status_word = {
        19'd0,
        cfg_error_code_i,
        sccb_cfg_error,
        sccb_init_done,
        cam_pwdn,
        cam_href,
        cam_vsync,
        pixel_seen,
        line_seen,
        frame_seen,
        xclk_locked
    };

    assign dbg_led = {
        frame_count_i[4],
        pixel_seen,
        line_seen,
        xclk_locked
    };

    ov5640_dvp_capture #(
        .VSYNC_ACTIVE_HIGH(0),  // Camera 0x4740=0x22: VSYNC active LOW
        .HREF_ACTIVE_HIGH(0)     // Camera 0x4740=0x22: HREF active LOW
    ) u_capture (
        .pclk        (cam_pclk),
        .rst_n       (core_rst_n),
        .cam_vsync   (cam_vsync),
        .cam_href    (cam_href),
        .cam_data    (cam_data),
        .pixel_valid (pixel_valid),
        .pixel_data  (pixel_data),
        .pixel_x     (pixel_x),
        .pixel_y     (pixel_y),
        .frame_start (frame_start),
        .line_start  (line_start),
        .line_done   (line_done),
        .frame_count (frame_count_i)
    );

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
            if (frame_start) begin frame_seen <= 1'b1; end
            if (line_done) begin line_seen <= 1'b1; line_count_r <= line_count_r + 32'd1; end
            if (pixel_valid) begin
                pixel_seen <= 1'b1; pixel_count_r <= pixel_count_r + 32'd1;
                last_pixel_r <= pixel_data; last_x_r <= pixel_x; last_y_r <= pixel_y;
            end
        end
    end

    // === Per-frame pixel counter for TLAST (PCLK domain) ===
    reg [18:0] frame_pixel_cnt;
    wire       last_pixel_frame;

    always @(posedge cam_pclk or negedge core_rst_n) begin
        if (!core_rst_n) begin
            frame_pixel_cnt <= 19'd0;
        end else begin
            if (frame_start) begin
                frame_pixel_cnt <= 19'd0;
            end else if (pixel_valid) begin
                frame_pixel_cnt <= frame_pixel_cnt + 19'd1;
            end
        end
    end
    assign last_pixel_frame = pixel_valid && (frame_pixel_cnt == 19'd307199);

    // === stream_enable CDC: FCLK �?PCLK ===
    reg  se_meta, se_pclk;
    always @(posedge cam_pclk or negedge core_rst_n) begin
        if (!core_rst_n) begin
            se_meta <= 1'b0;
            se_pclk <= 1'b0;
        end else begin
            se_meta <= stream_enable;
            se_pclk <= se_meta;
        end
    end

    // === FCLK-domain hold-off counter (prevents DMA lock from FIFO glitch) ===
    reg [3:0] fclk_init_cnt;
    reg       fclk_init_done;
    always @(posedge s00_axi_aclk or negedge rst_n) begin
        if (!rst_n) begin
            fclk_init_cnt  <= 4'd0;
            fclk_init_done <= 1'b0;
        end else begin
            if (!fclk_init_done) begin
                fclk_init_cnt <= fclk_init_cnt + 4'd1;
                if (&fclk_init_cnt) fclk_init_done <= 1'b1;
            end
        end
    end

    // === XPM Async FIFO for CDC: PCLK -> FCLK ===
    wire [17:0] fifo_din  = {frame_start && pixel_valid, last_pixel_frame, pixel_data};
    wire        fifo_wren = pixel_valid && se_pclk;
    wire [17:0] fifo_dout;
    wire        fifo_empty;
    wire        fifo_rd_en;
    wire        fifo_full;

    assign fifo_rd_en = m_axis_tready && !fifo_empty;

    xpm_fifo_async #(
        .FIFO_WRITE_DEPTH(1024),
        .WRITE_DATA_WIDTH(18),
        .READ_DATA_WIDTH(18),
        .READ_MODE("fwft"),
        .FIFO_MEMORY_TYPE("auto"),
        .CDC_SYNC_STAGES(3),
        .RELATED_CLOCKS(0)
    ) u_stream_fifo (
        .wr_clk(cam_pclk),
        .rst(~core_rst_n),
        .wr_en(fifo_wren),
        .din(fifo_din),
        .full(fifo_full),
        .wr_ack(),
        .overflow(),
        .prog_full(),
        .wr_data_count(),
        .almost_full(),
        .wr_rst_busy(),
        .rd_clk(s00_axi_aclk),
        .rd_en(fifo_rd_en),
        .dout(fifo_dout),
        .empty(fifo_empty),
        .underflow(),
        .rd_data_count(),
        .prog_empty(),
        .almost_empty(),
        .rd_rst_busy(),
        .data_valid(),
        .sleep(1'b0),
        .injectdbiterr(1'b0),
        .injectsbiterr(1'b0),
        .sbiterr(),
        .dbiterr()
    );

    assign m_axis_tdata  = fifo_dout[15:0];
    assign m_axis_tkeep  = 2'b11;
    assign m_axis_tuser  = fifo_dout[17];
    assign m_axis_tlast  = fifo_dout[16];
    assign m_axis_tvalid = fclk_init_done && !fifo_empty;

endmodule

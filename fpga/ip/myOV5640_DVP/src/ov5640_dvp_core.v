`timescale 1ns / 1ps
// OV5640 DVP Core: capture + CDC FIFO + AXI-Stream output
// PCLK domain: camera capture → data + SOF/EOL flags
// FCLK domain: CDC via XPM FIFO → AXI-Stream output (tuser/tlast/tvalid)

module ov5640_dvp_core (
    // Camera (PCLK domain)
    input  wire        cam_pclk,
    input  wire        rst_n,
    input  wire        xclk_locked,
    input  wire        cam_vsync,
    input  wire        cam_href,
    input  wire [7:0]  cam_data,
    input  wire        cam_force_reset,
    input  wire        cam_pwdn_in,
    input  wire        stream_enable,

    // SCCB status inputs
    input  wire        sccb_init_done,
    input  wire        sccb_cfg_error,
    input  wire [3:0]  cfg_error_code,

    // Camera control outputs
    output wire        cam_resetb,
    output wire        cam_pwdn,
    output wire [3:0]  dbg_led,
    output wire [31:0] status_word,
    output wire [31:0] pixel_count,
    output wire [31:0] line_count,
    output wire [15:0] last_pixel,
    output wire [31:0] last_xy,

    // AXI-Stream output (FCLK domain)
    input  wire        s00_axi_aclk,
    output wire [15:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,
    input  wire        m_axis_tready
);

    // ---- POR: hold reset ~256 PCLK cycles after FPGA config ----
    reg [7:0] por_cnt;
    reg       por_done;
    always @(posedge cam_pclk) begin
        if (!por_done) begin
            por_cnt <= por_cnt + 1;
            if (&por_cnt) por_done <= 1;
        end
    end

    // ---- cam_force_reset CDC: 2-FF sync to PCLK ----
    reg fr_meta, fr_sync;
    always @(posedge cam_pclk) begin
        fr_meta <= cam_force_reset;
        fr_sync <= fr_meta;
    end
    wire core_rst_n = por_done && !fr_sync;

    // ---- Camera control ----
    assign cam_resetb = 1'b1;
    assign cam_pwdn   = cam_pwdn_in;

    // ---- Capture module (PCLK domain) ----
    wire        px_valid;
    wire [15:0] px_data;
    wire [11:0] px_x, px_y;
    wire        frame_start, line_start, line_end;

    ov5640_dvp_capture u_cap (
        .pclk        (cam_pclk),
        .rst_n       (core_rst_n),
        .vsync_raw   (cam_vsync),
        .href_raw    (cam_href),
        .data_raw    (cam_data),
        .pixel_valid (px_valid),
        .pixel_data  (px_data),
        .pixel_x     (px_x),
        .pixel_y     (px_y),
        .frame_start (frame_start),
        .line_start  (line_start),
        .line_end    (line_end)
    );

    // ---- Status tracking (PCLK domain) ----
    reg        fs, ls, ps;
    reg [31:0] lc, pc;
    reg [15:0] lp;
    reg [11:0] lx, ly;
    reg [31:0] fc;  // PCLK domain frame count

    always @(posedge cam_pclk or negedge core_rst_n) begin
        if (!core_rst_n) begin
            fs <= 0; ls <= 0; ps <= 0;
            lc <= 0; pc <= 0; lp <= 0; lx <= 0; ly <= 0;
            fc <= 0;
        end else begin
            if (frame_start) begin fs <= 1; fc <= fc + 1; end
            if (line_end)    ls <= 1;
            if (px_valid) begin
                ps <= 1; pc <= pc + 1; lp <= px_data; lx <= px_x; ly <= px_y;
            end
        end
    end

    // Status from PCLK domain (for AXI readback)
    assign pixel_count = pc;
    assign line_count  = lc;   // increments on line_end in capture module
    assign last_pixel  = lp;
    assign last_xy     = {8'd0, ly, lx};
    assign status_word = {19'd0, cfg_error_code, sccb_cfg_error, sccb_init_done,
                          cam_pwdn_in, cam_href, cam_vsync, ps, ls, fs, xclk_locked};
    assign dbg_led     = {fc[4], ps, ls, xclk_locked};

    // ---- stream_enable CDC: FCLK → PCLK ----
    reg se_meta, se_pclk;
    always @(posedge cam_pclk or negedge core_rst_n) begin
        if (!core_rst_n) begin se_meta <= 0; se_pclk <= 0; end
        else begin se_meta <= stream_enable; se_pclk <= se_meta; end
    end

    // ---- FCLK hold-off: prevent DMA glitch after config ----
    reg [3:0] fclk_cnt;
    reg       fclk_ready;
    always @(posedge s00_axi_aclk or negedge rst_n) begin
        if (!rst_n) begin fclk_cnt <= 0; fclk_ready <= 0; end
        else if (!fclk_ready) begin
            fclk_cnt <= fclk_cnt + 1;
            if (&fclk_cnt) fclk_ready <= 1;
        end
    end

    // ---- XPM Async FIFO: PCLK → FCLK ----
    // fifo_din: {SOF, EOL, pixel_data[15:0]}
    // SOF = frame_start on valid pixel (tuser)
    // EOL = line_end on valid pixel   (tlast)
    wire [17:0] fifo_din  = {frame_start && px_valid, line_end && px_valid, px_data};
    wire        fifo_wren = px_valid && se_pclk;
    wire [17:0] fifo_dout;
    wire        fifo_empty, fifo_full;

    assign m_axis_tready_or_0 = m_axis_tready;

    xpm_fifo_async #(
        .FIFO_WRITE_DEPTH(4096), .WRITE_DATA_WIDTH(18), .READ_DATA_WIDTH(18),
        .READ_MODE("fwft"), .FIFO_MEMORY_TYPE("auto"),
        .CDC_SYNC_STAGES(4), .RELATED_CLOCKS(0)
    ) u_fifo (
        .wr_clk(cam_pclk), .rst(!core_rst_n), .wr_en(fifo_wren && !fifo_full), .din(fifo_din),
        .full(fifo_full),
        .rd_clk(s00_axi_aclk), .rd_en(m_axis_tready && !fifo_empty),
        .dout(fifo_dout), .empty(fifo_empty),
        .sleep(1'b0), .injectdbiterr(1'b0), .injectsbiterr(1'b0)
    );

    assign m_axis_tdata  = fifo_dout[15:0];
    assign m_axis_tuser  = fifo_dout[17];    // SOF (start of frame)
    assign m_axis_tlast  = fifo_dout[16];    // EOL (end of line)
    assign m_axis_tvalid = fclk_ready && !fifo_empty;

endmodule

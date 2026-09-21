`timescale 1ns / 1ps
// Standard AXI4-Lite slave for OV5640 DVP IP
// Register map:
//   0x00: CTRL      [RW] bit0=pwdn, bit1-2=force_reset, bit3=stream_enable
//   0x04: STATUS    [RO] camera status + debug flags
//   0x08: FRAME_CNT [RO] frames captured (CDC: unreliable for multi-bit)
//   0x0C: LINE_CNT  [RO] lines captured
//   0x10: PIXEL_CNT [RO] pixels captured
//   0x14: LAST_PX   [RO] last pixel value
//   0x18: LAST_XY   [RO] last pixel coordinates
//   0x1C: REG7      [RW] scratch register

module myOV5640_DVP_v1_0_S00_AXI #(
    parameter C_S_AXI_DATA_WIDTH = 32,
    parameter C_S_AXI_ADDR_WIDTH = 5
) (
    // Camera pins
    input  wire [7:0]  cam_data_i,
    input  wire        cam_pclk_i,
    input  wire        cam_href_i,
    input  wire        cam_vsync_i,
    input  wire        xclk_locked_i,
    input  wire        sccb_init_done_i,
    input  wire        sccb_cfg_error_i,
    input  wire [3:0]  cfg_error_code_i,
    output wire        cam_resetb_o,
    output wire        cam_pwdn_o,
    output wire [3:0]  dbg_led_o,

    // AXI-Stream output
    output wire [15:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,
    input  wire        m_axis_tready,

    // AXI4-Lite
    input  wire        S_AXI_ACLK,
    input  wire        S_AXI_ARESETN,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_AWADDR,
    input  wire [2:0]  S_AXI_AWPROT,
    input  wire        S_AXI_AWVALID,
    output wire        S_AXI_AWREADY,
    input  wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_WDATA,
    input  wire [(C_S_AXI_DATA_WIDTH/8)-1:0] S_AXI_WSTRB,
    input  wire        S_AXI_WVALID,
    output wire        S_AXI_WREADY,
    output wire [1:0]  S_AXI_BRESP,
    output wire        S_AXI_BVALID,
    input  wire        S_AXI_BREADY,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_ARADDR,
    input  wire [2:0]  S_AXI_ARPROT,
    input  wire        S_AXI_ARVALID,
    output wire        S_AXI_ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0] S_AXI_RDATA,
    output wire [1:0]  S_AXI_RRESP,
    output wire        S_AXI_RVALID,
    input  wire        S_AXI_RREADY
);

    // ---- Address decoding ----
    localparam ADDR_LSB = (C_S_AXI_DATA_WIDTH/32) + 1;  // = 2 for 32-bit data
    localparam ADDR_BITS = 3;  // 8 registers

    // ---- AXI write channel ----
    reg awready, wready;
    reg [1:0] bresp;
    reg bvalid;
    reg [C_S_AXI_ADDR_WIDTH-1:0] awaddr;

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            awready <= 0;
            awaddr  <= 0;
        end else begin
            if (~awready && S_AXI_AWVALID && S_AXI_WVALID) begin
                awready <= 1;
                awaddr  <= S_AXI_AWADDR;
            end else begin
                awready <= 0;
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN)
            wready <= 0;
        else if (~wready && S_AXI_WVALID && S_AXI_AWVALID)
            wready <= 1;
        else
            wready <= 0;
    end

    wire wr_en = wready && S_AXI_WVALID && awready && S_AXI_AWVALID;

    // ---- Register file ----
    reg [31:0] reg_ctrl;   // 0x00
    reg [31:0] reg_scratch; // 0x1C

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            reg_ctrl    <= 0;
            reg_scratch <= 0;
        end else if (wr_en) begin
            case (awaddr[ADDR_LSB+ADDR_BITS-1:ADDR_LSB])
                3'h0: begin
                    if (S_AXI_WSTRB[0]) reg_ctrl[7:0]   <= S_AXI_WDATA[7:0];
                    if (S_AXI_WSTRB[1]) reg_ctrl[15:8]  <= S_AXI_WDATA[15:8];
                    if (S_AXI_WSTRB[2]) reg_ctrl[23:16] <= S_AXI_WDATA[23:16];
                    if (S_AXI_WSTRB[3]) reg_ctrl[31:24] <= S_AXI_WDATA[31:24];
                end
                3'h7: begin
                    if (S_AXI_WSTRB[0]) reg_scratch[7:0]   <= S_AXI_WDATA[7:0];
                    if (S_AXI_WSTRB[1]) reg_scratch[15:8]  <= S_AXI_WDATA[15:8];
                    if (S_AXI_WSTRB[2]) reg_scratch[23:16] <= S_AXI_WDATA[23:16];
                    if (S_AXI_WSTRB[3]) reg_scratch[31:24] <= S_AXI_WDATA[31:24];
                end
                default: ;
            endcase
        end
    end

    // ---- Write response ----
    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            bvalid <= 0; bresp <= 0;
        end else begin
            if (awready && S_AXI_AWVALID && ~bvalid && wready && S_AXI_WVALID) begin
                bvalid <= 1; bresp <= 0;  // OKAY
            end else if (S_AXI_BREADY && bvalid) begin
                bvalid <= 0;
            end
        end
    end

    assign S_AXI_AWREADY = awready;
    assign S_AXI_WREADY  = wready;
    assign S_AXI_BRESP   = bresp;
    assign S_AXI_BVALID  = bvalid;

    // ---- Control signals from register ----
    wire pwdn_ctrl    = reg_ctrl[0];
    wire force_reset  = reg_ctrl[1] | reg_ctrl[2];
    wire stream_en    = reg_ctrl[3];

    // ---- DVP Core ----
    wire [31:0] status_raw, pc_raw, lc_raw;
    wire [15:0] last_px;
    wire [31:0] last_xy;

    ov5640_dvp_core u_core (
        .cam_pclk        (cam_pclk_i),
        .rst_n           (S_AXI_ARESETN),
        .xclk_locked     (xclk_locked_i),
        .cam_vsync       (cam_vsync_i),
        .cam_href        (cam_href_i),
        .cam_data        (cam_data_i),
        .cam_force_reset (force_reset),
        .cam_pwdn_in     (pwdn_ctrl),
        .stream_enable   (stream_en),
        .sccb_init_done  (sccb_init_done_i),
        .sccb_cfg_error  (sccb_cfg_error_i),
        .cfg_error_code  (cfg_error_code_i),
        .cam_resetb      (cam_resetb_o),
        .cam_pwdn        (cam_pwdn_o),
        .dbg_led         (dbg_led_o),
        .status_word     (status_raw),
        .pixel_count     (pc_raw),
        .line_count      (lc_raw),
        .last_pixel      (last_px),
        .last_xy         (last_xy),
        .s00_axi_aclk    (S_AXI_ACLK),
        .m_axis_tdata    (m_axis_tdata),
        .m_axis_tvalid   (m_axis_tvalid),
        .m_axis_tlast    (m_axis_tlast),
        .m_axis_tuser    (m_axis_tuser),
        .m_axis_tready   (m_axis_tready)
    );

    // ---- AXI read channel ----
    reg arready;
    reg [C_S_AXI_ADDR_WIDTH-1:0] araddr;

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            arready <= 0; araddr <= 0;
        end else begin
            if (~arready && S_AXI_ARVALID) begin
                arready <= 1; araddr <= S_AXI_ARADDR;
            end else begin
                arready <= 0;
            end
        end
    end

    wire rd_en = arready && S_AXI_ARVALID && ~S_AXI_RVALID;

    // ---- Read data ----
    reg [31:0] rdata;
    reg [1:0]  rresp;
    reg        rvalid;

    always @(posedge S_AXI_ACLK) begin
        if (!S_AXI_ARESETN) begin
            rvalid <= 0; rresp <= 0; rdata <= 0;
        end else begin
            if (rd_en) begin
                rvalid <= 1;
                rresp  <= 0;
                case (araddr[ADDR_LSB+ADDR_BITS-1:ADDR_LSB])
                    3'h0: rdata <= reg_ctrl;
                    3'h1: rdata <= status_raw;
                    3'h2: rdata <= 32'd0;   // frame_count (use status instead)
                    3'h3: rdata <= lc_raw;
                    3'h4: rdata <= pc_raw;
                    3'h5: rdata <= {16'd0, last_px};
                    3'h6: rdata <= last_xy;
                    3'h7: rdata <= reg_scratch;
                    default: rdata <= 0;
                endcase
            end else if (S_AXI_RREADY && rvalid) begin
                rvalid <= 0;
            end
        end
    end

    assign S_AXI_ARREADY = arready;
    assign S_AXI_RDATA   = rdata;
    assign S_AXI_RRESP   = rresp;
    assign S_AXI_RVALID  = rvalid;

endmodule

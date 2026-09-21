`timescale 1ns / 1ps

module myOV5640_DVP_v1_0 #
(
    parameter integer C_S00_AXI_DATA_WIDTH = 32,
    parameter integer C_S00_AXI_ADDR_WIDTH = 5
)
(
    input  wire [7:0] cam_data_i,
    input  wire       cam_pclk_i,
    input  wire       cam_href_i,
    input  wire       cam_vsync_i,
    input  wire       xclk_locked_i,
    input  wire       sccb_init_done_i,
    input  wire       sccb_cfg_error_i,
    input  wire [3:0] cfg_error_code_i,
    output wire       cam_resetb_o,
    output wire       cam_pwdn_o,
    output wire [3:0] dbg_led_o,

    output wire [15:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    output wire        m_axis_tlast,
    output wire        m_axis_tuser,
    input  wire        m_axis_tready,

    input  wire                                   s00_axi_aclk,
    input  wire                                   s00_axi_aresetn,
    input  wire [C_S00_AXI_ADDR_WIDTH-1:0]        s00_axi_awaddr,
    input  wire [2:0]                             s00_axi_awprot,
    input  wire                                   s00_axi_awvalid,
    output wire                                   s00_axi_awready,
    input  wire [C_S00_AXI_DATA_WIDTH-1:0]        s00_axi_wdata,
    input  wire [(C_S00_AXI_DATA_WIDTH/8)-1:0]    s00_axi_wstrb,
    input  wire                                   s00_axi_wvalid,
    output wire                                   s00_axi_wready,
    output wire [1:0]                             s00_axi_bresp,
    output wire                                   s00_axi_bvalid,
    input  wire                                   s00_axi_bready,
    input  wire [C_S00_AXI_ADDR_WIDTH-1:0]        s00_axi_araddr,
    input  wire [2:0]                             s00_axi_arprot,
    input  wire                                   s00_axi_arvalid,
    output wire                                   s00_axi_arready,
    output wire [C_S00_AXI_DATA_WIDTH-1:0]        s00_axi_rdata,
    output wire [1:0]                             s00_axi_rresp,
    output wire                                   s00_axi_rvalid,
    input  wire                                   s00_axi_rready
);

    myOV5640_DVP_v1_0_S00_AXI #(
        .C_S_AXI_DATA_WIDTH (C_S00_AXI_DATA_WIDTH),
        .C_S_AXI_ADDR_WIDTH (C_S00_AXI_ADDR_WIDTH)
    ) myOV5640_DVP_v1_0_S00_AXI_inst (
        .cam_data_i      (cam_data_i),
        .cam_pclk_i      (cam_pclk_i),
        .cam_href_i      (cam_href_i),
        .cam_vsync_i     (cam_vsync_i),
        .xclk_locked_i   (xclk_locked_i),
        .sccb_init_done_i(sccb_init_done_i),
        .sccb_cfg_error_i(sccb_cfg_error_i),
        .cfg_error_code_i(cfg_error_code_i),
        .cam_resetb_o    (cam_resetb_o),
        .cam_pwdn_o      (cam_pwdn_o),
        .dbg_led_o       (dbg_led_o),
        .m_axis_tdata    (m_axis_tdata),
        .m_axis_tvalid   (m_axis_tvalid),
        .m_axis_tlast    (m_axis_tlast),
        .m_axis_tuser    (m_axis_tuser),
        .m_axis_tready   (m_axis_tready),
        .S_AXI_ACLK      (s00_axi_aclk),
        .S_AXI_ARESETN   (s00_axi_aresetn),
        .S_AXI_AWADDR    (s00_axi_awaddr),
        .S_AXI_AWPROT    (s00_axi_awprot),
        .S_AXI_AWVALID   (s00_axi_awvalid),
        .S_AXI_AWREADY   (s00_axi_awready),
        .S_AXI_WDATA     (s00_axi_wdata),
        .S_AXI_WSTRB     (s00_axi_wstrb),
        .S_AXI_WVALID    (s00_axi_wvalid),
        .S_AXI_WREADY    (s00_axi_wready),
        .S_AXI_BRESP     (s00_axi_bresp),
        .S_AXI_BVALID    (s00_axi_bvalid),
        .S_AXI_BREADY    (s00_axi_bready),
        .S_AXI_ARADDR    (s00_axi_araddr),
        .S_AXI_ARPROT    (s00_axi_arprot),
        .S_AXI_ARVALID   (s00_axi_arvalid),
        .S_AXI_ARREADY   (s00_axi_arready),
        .S_AXI_RDATA     (s00_axi_rdata),
        .S_AXI_RRESP     (s00_axi_rresp),
        .S_AXI_RVALID    (s00_axi_rvalid),
        .S_AXI_RREADY    (s00_axi_rready)
    );

endmodule

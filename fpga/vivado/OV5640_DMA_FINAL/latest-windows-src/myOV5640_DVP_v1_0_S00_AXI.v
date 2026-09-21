`timescale 1ns / 1ps

module myOV5640_DVP_v1_0_S00_AXI #
(
    parameter integer C_S_AXI_DATA_WIDTH = 32,
    parameter integer C_S_AXI_ADDR_WIDTH = 5
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

    // Raw parallel video output (PCLK domain -> v_vid_in_axi4s in BD)
    output wire        px_valid,
    output wire [15:0] px_data,
    output wire        px_frame_start,
    output wire        px_line_end,
    output wire [11:0] px_x,
    output wire [11:0] px_y,
    output wire        capture_active,

    input  wire                                   S_AXI_ACLK,
    input  wire                                   S_AXI_ARESETN,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0]          S_AXI_AWADDR,
    input  wire [2:0]                             S_AXI_AWPROT,
    input  wire                                   S_AXI_AWVALID,
    output wire                                   S_AXI_AWREADY,
    input  wire [C_S_AXI_DATA_WIDTH-1:0]          S_AXI_WDATA,
    input  wire [(C_S_AXI_DATA_WIDTH/8)-1:0]      S_AXI_WSTRB,
    input  wire                                   S_AXI_WVALID,
    output wire                                   S_AXI_WREADY,
    output wire [1:0]                             S_AXI_BRESP,
    output wire                                   S_AXI_BVALID,
    input  wire                                   S_AXI_BREADY,
    input  wire [C_S_AXI_ADDR_WIDTH-1:0]          S_AXI_ARADDR,
    input  wire [2:0]                             S_AXI_ARPROT,
    input  wire                                   S_AXI_ARVALID,
    output wire                                   S_AXI_ARREADY,
    output wire [C_S_AXI_DATA_WIDTH-1:0]          S_AXI_RDATA,
    output wire [1:0]                             S_AXI_RRESP,
    output wire                                   S_AXI_RVALID,
    input  wire                                   S_AXI_RREADY
);

    reg [C_S_AXI_ADDR_WIDTH-1:0] axi_awaddr;
    reg                          axi_awready;
    reg                          axi_wready;
    reg [1:0]                    axi_bresp;
    reg                          axi_bvalid;
    reg [C_S_AXI_ADDR_WIDTH-1:0] axi_araddr;
    reg                          axi_arready;
    reg [C_S_AXI_DATA_WIDTH-1:0] axi_rdata;
    reg [1:0]                    axi_rresp;
    reg                          axi_rvalid;

    localparam integer ADDR_LSB          = (C_S_AXI_DATA_WIDTH/32) + 1;
    localparam integer OPT_MEM_ADDR_BITS = 2;

    reg [C_S_AXI_DATA_WIDTH-1:0] slv_reg0;
    reg [C_S_AXI_DATA_WIDTH-1:0] slv_reg7;
    wire                         slv_reg_rden;
    wire                         slv_reg_wren;
    reg [C_S_AXI_DATA_WIDTH-1:0] reg_data_out;
    integer                      byte_index;
    reg                          aw_en;

    wire [31:0] status_word_raw;
    wire [31:0] frame_count_raw;
    wire [31:0] line_count_raw;
    wire [31:0] pixel_count_raw;
    wire [15:0] last_pixel_word_raw;
    wire [31:0] last_xy_word_raw;

    (* ASYNC_REG = "TRUE" *) reg [31:0] status_word_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] status_word_sync;
    (* ASYNC_REG = "TRUE" *) reg [31:0] frame_count_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] frame_count_sync;
    (* ASYNC_REG = "TRUE" *) reg [31:0] line_count_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] line_count_sync;
    (* ASYNC_REG = "TRUE" *) reg [31:0] pixel_count_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] pixel_count_sync;
    (* ASYNC_REG = "TRUE" *) reg [15:0] last_pixel_word_meta;
    (* ASYNC_REG = "TRUE" *) reg [15:0] last_pixel_word_sync;
    (* ASYNC_REG = "TRUE" *) reg [31:0] last_xy_word_meta;
    (* ASYNC_REG = "TRUE" *) reg [31:0] last_xy_word_sync;
    wire        cam_pwdn_from_reg;
    wire        cam_force_reset;
    wire        stream_enable;

    assign cam_pwdn_from_reg = slv_reg0[0];
    assign cam_force_reset   = slv_reg0[1] | slv_reg0[2];
    assign stream_enable     = slv_reg0[3];
    assign S_AXI_WREADY  = axi_wready;
    assign S_AXI_BRESP   = axi_bresp;
    assign S_AXI_BVALID  = axi_bvalid;
    assign S_AXI_ARREADY = axi_arready;
    assign S_AXI_RDATA   = axi_rdata;
    assign S_AXI_RRESP   = axi_rresp;
    assign S_AXI_RVALID  = axi_rvalid;

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_awready <= 1'b0;
            aw_en       <= 1'b1;
        end else begin
            if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en) begin
                axi_awready <= 1'b1;
                aw_en       <= 1'b0;
            end else if (S_AXI_BREADY && axi_bvalid) begin
                aw_en       <= 1'b1;
                axi_awready <= 1'b0;
            end else begin
                axi_awready <= 1'b0;
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_awaddr <= {C_S_AXI_ADDR_WIDTH{1'b0}};
        end else begin
            if (~axi_awready && S_AXI_AWVALID && S_AXI_WVALID && aw_en) begin
                axi_awaddr <= S_AXI_AWADDR;
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_wready <= 1'b0;
        end else begin
            if (~axi_wready && S_AXI_WVALID && S_AXI_AWVALID && aw_en) begin
                axi_wready <= 1'b1;
            end else begin
                axi_wready <= 1'b0;
            end
        end
    end

    assign slv_reg_wren = axi_wready && S_AXI_WVALID && axi_awready && S_AXI_AWVALID;

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            slv_reg0 <= 32'd0;
            slv_reg7 <= 32'd0;
        end else begin
            if (slv_reg_wren) begin
                case (axi_awaddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB])
                    3'h0: begin
                        for (byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index + 1) begin
                            if (S_AXI_WSTRB[byte_index] == 1'b1) begin
                                slv_reg0[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
                            end
                        end
                    end
                    3'h7: begin
                        for (byte_index = 0; byte_index <= (C_S_AXI_DATA_WIDTH/8)-1; byte_index = byte_index + 1) begin
                            if (S_AXI_WSTRB[byte_index] == 1'b1) begin
                                slv_reg7[(byte_index*8) +: 8] <= S_AXI_WDATA[(byte_index*8) +: 8];
                            end
                        end
                    end
                    default: begin
                        slv_reg0 <= slv_reg0;
                        slv_reg7 <= slv_reg7;
                    end
                endcase
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_bvalid <= 1'b0;
            axi_bresp  <= 2'b00;
        end else begin
            if (axi_awready && S_AXI_AWVALID && ~axi_bvalid && axi_wready && S_AXI_WVALID) begin
                axi_bvalid <= 1'b1;
                axi_bresp  <= 2'b00;
            end else if (S_AXI_BREADY && axi_bvalid) begin
                axi_bvalid <= 1'b0;
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_arready <= 1'b0;
            axi_araddr  <= {C_S_AXI_ADDR_WIDTH{1'b0}};
        end else begin
            if (~axi_arready && S_AXI_ARVALID) begin
                axi_arready <= 1'b1;
                axi_araddr  <= S_AXI_ARADDR;
            end else begin
                axi_arready <= 1'b0;
            end
        end
    end

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_rvalid <= 1'b0;
            axi_rresp  <= 2'b00;
        end else begin
            if (axi_arready && S_AXI_ARVALID && ~axi_rvalid) begin
                axi_rvalid <= 1'b1;
                axi_rresp  <= 2'b00;
            end else if (axi_rvalid && S_AXI_RREADY) begin
                axi_rvalid <= 1'b0;
            end
        end
    end

    assign slv_reg_rden = axi_arready && S_AXI_ARVALID && ~axi_rvalid;

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            status_word_meta     <= 32'd0;
            status_word_sync     <= 32'd0;
            frame_count_meta     <= 32'd0;
            frame_count_sync     <= 32'd0;
            line_count_meta      <= 32'd0;
            line_count_sync      <= 32'd0;
            pixel_count_meta     <= 32'd0;
            pixel_count_sync     <= 32'd0;
            last_pixel_word_meta <= 16'd0;
            last_pixel_word_sync <= 16'd0;
            last_xy_word_meta    <= 32'd0;
            last_xy_word_sync    <= 32'd0;
        end else begin
            status_word_meta     <= status_word_raw;
            status_word_sync     <= status_word_meta;
            frame_count_meta     <= frame_count_raw;
            frame_count_sync     <= frame_count_meta;
            line_count_meta      <= line_count_raw;
            line_count_sync      <= line_count_meta;
            pixel_count_meta     <= pixel_count_raw;
            pixel_count_sync     <= pixel_count_meta;
            last_pixel_word_meta <= last_pixel_word_raw;
            last_pixel_word_sync <= last_pixel_word_meta;
            last_xy_word_meta    <= last_xy_word_raw;
            last_xy_word_sync    <= last_xy_word_meta;
        end
    end

    always @(*) begin
        case (axi_araddr[ADDR_LSB+OPT_MEM_ADDR_BITS:ADDR_LSB])
            3'h0: reg_data_out = slv_reg0;
            3'h1: reg_data_out = status_word_sync;
            3'h2: reg_data_out = frame_count_sync;
            3'h3: reg_data_out = line_count_sync;
            3'h4: reg_data_out = pixel_count_sync;
            3'h5: reg_data_out = {16'd0, last_pixel_word_sync};
            3'h6: reg_data_out = last_xy_word_sync;
            3'h7: reg_data_out = slv_reg7;
            default: reg_data_out = 32'd0;
        endcase
    end

    always @(posedge S_AXI_ACLK) begin
        if (S_AXI_ARESETN == 1'b0) begin
            axi_rdata <= {C_S_AXI_DATA_WIDTH{1'b0}};
        end else begin
            if (slv_reg_rden) begin
                axi_rdata <= reg_data_out;
            end
        end
    end

    ov5640_dvp_core u_core (
        .cam_pclk       (cam_pclk_i),
        .rst_n          (S_AXI_ARESETN),
        .xclk_locked    (xclk_locked_i),
        .cam_vsync      (cam_vsync_i),
        .cam_href       (cam_href_i),
        .cam_data       (cam_data_i),
        .cam_force_reset(cam_force_reset),
        .cam_pwdn_in    (cam_pwdn_from_reg),
        .sccb_init_done (sccb_init_done_i),
        .sccb_cfg_error (sccb_cfg_error_i),
        .cfg_error_code_i(cfg_error_code_i),
        .cam_resetb     (cam_resetb_o),
        .cam_pwdn       (cam_pwdn_o),
        .dbg_led        (dbg_led_o),
        .status_word    (status_word_raw),
        .frame_count    (frame_count_raw),
        .line_count     (line_count_raw),
        .pixel_count    (pixel_count_raw),
        .last_pixel     (last_pixel_word_raw),
        .last_xy        (last_xy_word_raw),
        .px_valid       (px_valid),
        .px_data        (px_data),
        .px_frame_start (px_frame_start),
        .px_line_end    (px_line_end),
        .px_x           (px_x),
        .px_y           (px_y),
        .capture_active (capture_active)
    );

endmodule

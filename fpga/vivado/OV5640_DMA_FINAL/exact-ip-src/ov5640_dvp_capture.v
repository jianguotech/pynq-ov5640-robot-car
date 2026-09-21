`timescale 1ns / 1ps

module ov5640_dvp_capture #(
    parameter integer X_BITS = 12,
    parameter integer Y_BITS = 12,
    parameter integer VSYNC_ACTIVE_HIGH = 1,
    parameter integer HREF_ACTIVE_HIGH  = 1
) (
    input  wire              pclk,
    input  wire              rst_n,
    input  wire              cam_vsync,
    input  wire              cam_href,
    input  wire [7:0]        cam_data,
    output reg               pixel_valid,
    output reg  [15:0]       pixel_data,
    output reg  [X_BITS-1:0] pixel_x,
    output reg  [Y_BITS-1:0] pixel_y,
    output reg               frame_start,
    output reg               line_start,
    output reg               line_done,
    output reg  [31:0]       frame_count,
    // AXI-Stream output (video to VDMA/DDR)
    input  wire              m_axis_tready,
    output reg               m_axis_tvalid,
    output reg  [15:0]       m_axis_tdata,
    output reg               m_axis_tlast,
    output reg               m_axis_tuser
);

    wire vsync_now;
    wire href_now;
    reg vsync_d;
    reg href_d;
    reg have_low_byte;
    reg line_has_pixel;
    reg frame_pending;
    reg [7:0] low_byte;
    reg [X_BITS-1:0] x_count;
    reg [Y_BITS-1:0] y_count;

    assign vsync_now = VSYNC_ACTIVE_HIGH ? cam_vsync : ~cam_vsync;
    assign href_now  = HREF_ACTIVE_HIGH  ? cam_href  : ~cam_href;

    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n) begin
            vsync_d        <= 1'b0;
            href_d         <= 1'b0;
            have_low_byte  <= 1'b0;
            line_has_pixel <= 1'b0;
            frame_pending  <= 1'b1;
            low_byte       <= 8'h00;
            x_count        <= {X_BITS{1'b0}};
            y_count        <= {Y_BITS{1'b0}};
            pixel_valid    <= 1'b0;
            pixel_data     <= 16'h0000;
            pixel_x        <= {X_BITS{1'b0}};
            pixel_y        <= {Y_BITS{1'b0}};
            frame_start    <= 1'b0;
            line_start     <= 1'b0;
            line_done      <= 1'b0;
            frame_count    <= 32'd0;
            m_axis_tvalid  <= 1'b0;
            m_axis_tdata   <= 16'h0000;
            m_axis_tlast   <= 1'b0;
            m_axis_tuser   <= 1'b0;
        end else begin
            pixel_valid <= 1'b0;
            frame_start <= 1'b0;
            line_start  <= 1'b0;
            line_done   <= 1'b0;
            m_axis_tvalid <= 1'b0;
            m_axis_tlast  <= 1'b0;
            m_axis_tuser  <= 1'b0;

            // VSYNC rising edge = new frame
            if (vsync_now && !vsync_d) begin
                frame_count    <= frame_count + 32'd1;
                x_count        <= {X_BITS{1'b0}};
                y_count        <= {Y_BITS{1'b0}};
                have_low_byte  <= 1'b0;
                line_has_pixel <= 1'b0;
                frame_pending  <= 1'b1;
            end

            // HREF rising edge = new line
            if (href_now && !href_d) begin
                x_count        <= {X_BITS{1'b0}};
                have_low_byte  <= 1'b0;
                line_has_pixel <= 1'b0;
            end

            // HREF falling edge = line done
            if (!href_now && href_d) begin
                have_low_byte <= 1'b0;
                if (line_has_pixel) begin
                    y_count   <= y_count + {{(Y_BITS-1){1'b0}}, 1'b1};
                    line_done <= 1'b1;
                    // Signal end-of-line on stream
                    if (m_axis_tready) begin
                        m_axis_tvalid <= 1'b1;
                        m_axis_tlast  <= 1'b1;
                        m_axis_tdata  <= 16'h0000;
                    end
                    line_has_pixel <= 1'b0;
                end
            end

            // Active pixels
            if (href_now) begin
                if (!have_low_byte) begin
                    low_byte      <= cam_data;
                    have_low_byte <= 1'b1;
                end else begin
                    pixel_data     <= {cam_data, low_byte};
                    pixel_x        <= x_count;
                    pixel_y        <= y_count;
                    pixel_valid    <= 1'b1;
                    frame_start    <= frame_pending;
                    line_start     <= (x_count == {X_BITS{1'b0}});
                    x_count        <= x_count + {{(X_BITS-1){1'b0}}, 1'b1};
                    have_low_byte  <= 1'b0;
                    line_has_pixel <= 1'b1;
                    frame_pending  <= 1'b0;

                    // Drive AXI-Stream output
                    if (m_axis_tready) begin
                        m_axis_tvalid <= 1'b1;
                        m_axis_tdata  <= {cam_data, low_byte};
                        m_axis_tuser  <= frame_pending;
                        m_axis_tlast  <= 1'b0;
                    end
                end
            end

            vsync_d <= vsync_now;
            href_d  <= href_now;
        end
    end

endmodule

`timescale 1ns / 1ps
// OV5640 DVP 8-bit capture → 16-bit RGB565 pixels
// HREF: active LOW  (0 = line data valid)
// VSYNC: active HIGH (1 = new frame sync pulse)
// Each pixel = 2 PCLK cycles: HIGH byte first, then LOW byte

module ov5640_dvp_capture (
    input  wire        pclk,
    input  wire        rst_n,
    input  wire        vsync_raw,    // camera VSYNC pin (active HIGH pulse)
    input  wire        href_raw,     // camera HREF pin  (active LOW = valid)
    input  wire [7:0]  data_raw,     // camera 8-bit data bus
    output wire        pixel_valid,  // 1 when full 16-bit pixel ready
    output wire [15:0] pixel_data,   // RGB565 pixel {R[4:0],G[5:0],B[4:0]}
    output wire [11:0] pixel_x,      // column (0..639)
    output wire [11:0] pixel_y,      // row (0..479)
    output wire        frame_start,  // 1 on first pixel of new frame
    output wire        line_start,   // 1 on first pixel of new line
    output wire        line_end      // 1 on last pixel of each line (for VDMA tlast)
);

    // ---- Polarity correction ----
    wire href  = ~href_raw;   // active HIGH internally
    wire vsync = vsync_raw;   // VSYNC keeps active HIGH

    // ---- VSYNC rising edge detect (new frame) ----
    reg vsync_d0, vsync_d1;
    always @(posedge pclk) begin
        vsync_d0 <= vsync;
        vsync_d1 <= vsync_d0;
    end
    wire vsync_rise = (~vsync_d1) & vsync_d0;  // 0→1 = sync pulse begins

    // ---- Byte pairing: 2 PCLK bytes → 1 pixel ----
    reg        byte_toggle;    // 0=first(HIGH) byte, 1=second(LOW) byte
    reg [7:0]  first_byte;     // stored HIGH byte
    reg [15:0] pixel_16;       // assembled 16-bit pixel

    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n) begin
            byte_toggle <= 0;
            first_byte  <= 0;
            pixel_16    <= 0;
        end else if (href) begin
            byte_toggle <= ~byte_toggle;
            first_byte  <= data_raw;
            if (byte_toggle)
                pixel_16 <= {first_byte, data_raw};  // {HIGH, LOW} = RGB565
        end else begin
            byte_toggle <= 0;
            first_byte  <= 0;
        end
    end

    assign pixel_valid = href && byte_toggle;  // valid when LOW byte arrives
    assign pixel_data  = pixel_16;

    // ---- Pixel coordinates ----
    reg [11:0] col, row;
    reg        frame_pending;
    reg        href_d;

    always @(posedge pclk or negedge rst_n) begin
        if (!rst_n) begin
            col           <= 0;
            row           <= 0;
            frame_pending <= 1;
            href_d        <= 0;
        end else begin
            href_d <= href;

            // VSYNC pulse: new frame coming
            if (vsync_rise) begin
                col           <= 0;
                row           <= 0;
                frame_pending <= 1;
            end

            // HREF rising: new line
            if (href && !href_d) col <= 0;

            // HREF falling: line done
            if (!href && href_d) row <= row + 1;

            // Pixel valid: advance column
            if (pixel_valid) begin
                col           <= col + 1;
                frame_pending <= 0;
            end
        end
    end

    assign pixel_x     = col;
    assign pixel_y     = row;
    assign frame_start = pixel_valid && frame_pending;           // SOF (tuser)
    assign line_start  = pixel_valid && (col == 0);              // first pixel of line
    assign line_end    = pixel_valid && (col == 11'd639);        // last pixel of line (EOL)

endmodule

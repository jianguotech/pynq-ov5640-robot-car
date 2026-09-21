// Testbench for clean OV5640 DVP core
// Simulates camera output: 640x480 frame, checks tuser/tlast/tvalid
`timescale 1ns / 1ps

module tb_clean;

    reg pclk = 0;
    reg fclk = 0;
    always #9 pclk = ~pclk;   // ~56 MHz (18ns period)
    always #5 fclk = ~fclk;   // 100 MHz

    reg rst_n = 0;
    reg xclk_locked = 1;
    reg cam_vsync = 1;
    reg cam_href = 1;
    reg [7:0] cam_data = 0;
    reg cam_force_reset = 0;
    reg cam_pwdn_in = 0;
    reg stream_enable = 0;
    reg sccb_init_done = 1;
    reg sccb_cfg_error = 0;
    reg [3:0] cfg_error_code = 0;
    reg m_axis_tready = 1;

    wire [15:0] m_axis_tdata;
    wire        m_axis_tvalid;
    wire        m_axis_tlast;
    wire        m_axis_tuser;
    wire [3:0]  dbg_led;
    wire [31:0] status_word;
    wire [31:0] line_count;
    wire [31:0] pixel_count;
    wire [15:0] last_pixel;
    wire [31:0] last_xy;

    ov5640_dvp_core dut (
        .cam_pclk       (pclk),
        .rst_n          (rst_n),
        .xclk_locked    (xclk_locked),
        .cam_vsync      (cam_vsync),
        .cam_href       (cam_href),
        .cam_data       (cam_data),
        .cam_force_reset(cam_force_reset),
        .cam_pwdn_in    (cam_pwdn_in),
        .stream_enable  (stream_enable),
        .sccb_init_done (sccb_init_done),
        .sccb_cfg_error (sccb_cfg_error),
        .cfg_error_code (cfg_error_code),
        .cam_resetb     (),
        .cam_pwdn       (),
        .dbg_led        (dbg_led),
        .status_word    (status_word),
        .pixel_count    (pixel_count),
        .line_count     (line_count),
        .last_pixel     (last_pixel),
        .last_xy        (last_xy),
        .s00_axi_aclk   (fclk),
        .m_axis_tdata   (m_axis_tdata),
        .m_axis_tvalid  (m_axis_tvalid),
        .m_axis_tlast   (m_axis_tlast),
        .m_axis_tuser   (m_axis_tuser),
        .m_axis_tready  (m_axis_tready)
    );

    // ---- Generate simulated camera data ----
    integer px, line, byte_flag;
    reg [7:0] test_pattern;

    initial begin
        $display("=== Clean DVP Simulation ===");
        $display("Time: %0t", $time);

        // Reset
        rst_n = 0; stream_enable = 0;
        cam_vsync = 1; cam_href = 1;  // inactive state
        #100;
        rst_n = 1;
        #200;

        // Enable streaming
        stream_enable = 1;

        // Simulate 3 frames
        for (int frame = 0; frame < 3; frame++) begin
            $display("Frame %0d start at %0t", frame, $time);

            // VSYNC pulse (4 PCLK cycles)
            cam_vsync = 0;  // active HIGH pulse
            cam_href = 1;
            #(4 * 18);
            cam_vsync = 1;

            // Wait a bit after VSYNC
            #(10 * 18);

            // 480 lines
            for (line = 0; line < 5; line++) begin  // 5 lines for quick test
                cam_vsync = 1;
                cam_href = 0;  // HREF active LOW

                // 640 pixels per line (2 bytes each = 1280 PCLKs)
                byte_flag = 0;
                for (px = 0; px < 1280; px++) begin
                    test_pattern = (line << 4) | (px & 8'h0F);  // unique per line/position
                    cam_data = test_pattern;
                    #9;  // 1 PCLK
                    byte_flag = ~byte_flag;
                end

                // End of line
                cam_href = 1;  // HREF inactive
                #(20 * 18);  // horizontal blanking
            end

            // End of frame
            #(50 * 18);  // vertical blanking
        end

        // Drain FIFO
        repeat(500) @(posedge fclk);

        $display("Final: PC=%0d LC=%0d", pixel_count, line_count);
        $display("=== Simulation Complete ===");
        $finish;
    end

    // Monitor AXI-Stream output
    integer tvalid_cnt = 0, tlast_cnt = 0, tuser_cnt = 0;
    always @(posedge fclk) begin
        if (m_axis_tvalid && m_axis_tready) begin
            tvalid_cnt++;
            if (m_axis_tuser) begin
                tuser_cnt++;
                $display("[%0t] SOF: data=0x%04X", $time, m_axis_tdata);
            end
            if (m_axis_tlast) begin
                tlast_cnt++;
                $display("[%0t] EOL: data=0x%04X line_end=%0d", $time, m_axis_tdata, tlast_cnt);
            end
        end
    end

endmodule

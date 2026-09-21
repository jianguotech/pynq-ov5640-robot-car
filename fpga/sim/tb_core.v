// Testbench for ov5640_dvp_core with all fixes
// Tests: FCLK hold-off, stream_enable CDC, TUSER, TLAST (per-pixel counter)
`timescale 1ns / 1ps

module tb_core;

    // ====== Clock generation ======
    reg pclk = 0;
    reg fclk = 0;
    always #5 pclk = ~pclk;  // 100 MHz PCLK (for fast sim)
    always #5 fclk = ~fclk;  // 100 MHz FCLK

    // ====== DUT signals ======
    reg         rst_n = 0;
    reg         xclk_locked = 1;
    reg         cam_vsync = 1;
    reg         cam_href = 1;
    reg  [7:0]  cam_data = 0;
    reg         cam_force_reset = 0;
    reg         cam_pwdn_in = 0;
    reg         stream_enable = 0;
    reg         sccb_init_done = 1;
    reg         sccb_cfg_error = 0;
    reg  [3:0]  cfg_error_code = 0;
    reg         m_axis_tready = 1;

    wire [15:0] m_axis_tdata;
    wire        m_axis_tvalid;
    wire        m_axis_tlast;
    wire        m_axis_tuser;
    wire [3:0]  dbg_led;
    wire        cam_resetb;
    wire        cam_pwdn;
    wire [31:0] status_word;
    wire [31:0] frame_count;
    wire [31:0] line_count;
    wire [31:0] pixel_count;

    // ====== DUT ======
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
        .cfg_error_code_i(cfg_error_code),
        .cam_resetb     (cam_resetb),
        .cam_pwdn       (cam_pwdn),
        .dbg_led        (dbg_led),
        .status_word    (status_word),
        .frame_count    (frame_count),
        .line_count     (line_count),
        .pixel_count    (pixel_count),
        .last_pixel_word(),
        .last_xy_word   (),
        .s00_axi_aclk   (fclk),
        .m_axis_tdata   (m_axis_tdata),
        .m_axis_tvalid  (m_axis_tvalid),
        .m_axis_tlast   (m_axis_tlast),
        .m_axis_tuser   (m_axis_tuser),
        .m_axis_tready  (m_axis_tready)
    );

    // ====== Monitor ======
    integer pixels_rx = 0;
    integer tuser_cnt = 0;
    integer tlast_cnt = 0;
    integer errors    = 0;
    integer tlast_pos = -1;
    integer tuser_pos = -1;

    always @(posedge fclk) begin
        if (m_axis_tvalid && m_axis_tready) begin
            pixels_rx = pixels_rx + 1;
            if (m_axis_tuser) begin
                tuser_cnt = tuser_cnt + 1;
                tuser_pos = pixels_rx;
            end
            if (m_axis_tlast) begin
                tlast_cnt = tlast_cnt + 1;
                tlast_pos = pixels_rx;
            end
        end
    end

    // ====== Tasks ======
    task send_byte(input [7:0] b);
        @(posedge pclk);
        cam_data = b;
    endtask

    task send_pixel(input [15:0] rgb);
        send_byte(rgb[15:8]);
        send_byte(rgb[7:0]);
    endtask

    // ====== Test sequence ======
    initial begin
        integer line, pix;
        integer timeout, prev_rx;

        $dumpfile("tb_core.vcd");
        $dumpvars(0, tb_core);

        $display("=== OV5640 DVP Core Testbench ===");

        // ====== Test 1: Hold-off ======
        $display("\n[TEST 1] FCLK hold-off after reset");
        rst_n = 0;
        #50;
        rst_n = 1;
        // Hold-off should keep tvalid low for 16 FCLK cycles (160ns)
        #40;  // 4 FCLK cycles — should still be low
        if (m_axis_tvalid) begin
            $display("  FAIL: tvalid high at 4 FCLK cycles (should be in hold-off)");
            errors = errors + 1;
        end else begin
            $display("  PASS: tvalid low at 4 FCLK cycles");
        end
        #60;  // 6 more cycles = 10 total
        if (m_axis_tvalid) begin
            $display("  FAIL: tvalid high at 10 FCLK cycles (hold-off = 16)");
            errors = errors + 1;
        end else begin
            $display("  PASS: tvalid low at 10 FCLK cycles");
        end
        #80;  // 8 more = 18 total — well past 16
        if (dut.fclk_init_done) begin
            $display("  PASS: hold-off completed after 16 FCLK cycles");
        end else begin
            $display("  FAIL: hold-off never completed");
            errors = errors + 1;
        end
        // tvalid should still be low (no data yet) but fclk_init_done is high
        if (m_axis_tvalid) begin
            $display("  FAIL: tvalid high with no data in FIFO");
            errors = errors + 1;
        end else begin
            $display("  PASS: tvalid low (FIFO empty) after hold-off");
        end

        // ====== Test 2: Short burst (TUSER) ======
        $display("\n[TEST 2] TUSER on first pixel");
        // Wait for POR (256 PCLK cycles = 2560ns)
        wait(dut.core_rst_n);
        stream_enable = 1;
        #500;

        // Start a frame (active-low VSYNC)
        @(posedge pclk); cam_vsync = 0;  // frame start
        @(posedge pclk); cam_href = 0;   // line start

        // Send just 10 pixels
        for (pix = 0; pix < 10; pix = pix + 1) begin
            send_pixel(16'hF800);  // red
        end

        // End line
        @(posedge pclk); cam_href = 1;
        @(posedge pclk);

        // End frame
        cam_vsync = 1;

        // Drain FIFO
        #200;

        // Check TUSER
        if (tuser_cnt == 1) begin
            $display("  PASS: TUSER seen exactly once (pixel %d)", tuser_pos);
        end else begin
            $display("  FAIL: TUSER count = %d (expected 1)", tuser_cnt);
            errors = errors + 1;
        end
        if (tuser_pos == 1) begin
            $display("  PASS: TUSER on first pixel");
        end else begin
            $display("  FAIL: TUSER on pixel %d (expected 1)", tuser_pos);
            errors = errors + 1;
        end

        // ====== Test 3: Full frame (TLAST) ======
        $display("\n[TEST 3] Full frame TLAST verification");
        $display("  Sending 480x640 = %0d pixels...", 480*640);

        // Reset counters
        pixels_rx = 0;
        tuser_cnt = 0;
        tlast_cnt = 0;
        tuser_pos = -1;
        tlast_pos = -1;

        // Start frame
        @(posedge pclk); cam_vsync = 0;

        for (line = 0; line < 480; line = line + 1) begin
            @(posedge pclk); cam_href = 0;
            for (pix = 0; pix < 640; pix = pix + 1) begin
                send_pixel({pix[7:0], 8'h00});  // pixel value = column number
            end
            @(posedge pclk); cam_href = 1;
            @(posedge pclk); // horizontal blanking gap
        end

        @(posedge pclk); cam_vsync = 1;

        // Drain FIFO - wait for all pixels to appear
        timeout = 0;
        while (pixels_rx < 480*640 && timeout < 50000) begin
            @(posedge fclk);
            timeout = timeout + 1;
        end

        // ====== Results ======
        $display("\n=== RESULTS ===");
        $display("  Pixels received: %0d", pixels_rx);
        $display("  TUSER count: %0d (at pixel %0d)", tuser_cnt, tuser_pos);
        $display("  TLAST count: %0d (at pixel %0d)", tlast_cnt, tlast_pos);
        $display("  DUT frame_count: %0d", dut.frame_count_i);
        $display("  DUT pixel_count: %0d", dut.pixel_count_r);
        $display("  DUT line_count:  %0d", dut.line_count_r);
        $display("  DUT last pixel:  0x%04X at (%0d,%0d)",
            dut.last_pixel_r, dut.last_x_r, dut.last_y_r);

        if (pixels_rx == 480*640) begin
            $display("  PASS: All %0d pixels received", 480*640);
        end else begin
            $display("  FAIL: Expected %0d pixels, got %0d", 480*640, pixels_rx);
            errors = errors + 1;
        end

        if (tuser_cnt == 1 && tuser_pos == 1) begin
            $display("  PASS: TUSER correct");
        end else begin
            $display("  FAIL: TUSER issue (cnt=%0d, pos=%0d)", tuser_cnt, tuser_pos);
            errors = errors + 1;
        end

        if (tlast_cnt == 1 && tlast_pos == 480*640) begin
            $display("  PASS: TLAST on last pixel");
        end else begin
            $display("  FAIL: TLAST issue (cnt=%0d, pos=%0d, expected pos=%0d)",
                tlast_cnt, tlast_pos, 480*640);
            errors = errors + 1;
        end

        // ====== Test 4: stream_enable CDC ======
        $display("\n[TEST 4] stream_enable CDC");
        // With stream_enable=0, no more pixels should flow
        stream_enable = 0;
        @(posedge pclk); cam_vsync = 0;
        @(posedge pclk); cam_href = 0;
        send_pixel(16'hFFFF);
        @(posedge pclk); cam_href = 1;
        @(posedge pclk); cam_vsync = 1;

        #100;
        prev_rx = pixels_rx;
        #200;
        if (pixels_rx == prev_rx) begin
            $display("  PASS: No pixels received after stream_enable=0");
        end else begin
            $display("  FAIL: Got %0d extra pixels after stream_enable=0",
                pixels_rx - prev_rx);
            errors = errors + 1;
        end

        // ====== Summary ======
        $display("\n=== SUMMARY ===");
        if (errors == 0) begin
            $display("  ALL TESTS PASSED");
        end else begin
            $display("  %0d TEST(S) FAILED", errors);
        end
        $display("  VCD: tb_core.vcd");
        $finish;
    end

endmodule

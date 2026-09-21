# 仿真指南 — OV5640 DVP 采集

## 一、仿真策略

建议分三个层次逐步仿真：

### 层次 1：独立模块仿真 (最简单)

**ov5640_dvp_capture** 可以独立仿真，不需要 AXI 总线。

**Testbench 思路：**
1. 产生 cam_pclk 时钟 (~56MHz)
2. 复位
3. 模拟 VSYNC 下降沿 (新帧)
4. 模拟 HREF 高电平 + 交替发送高低字节
5. 检查 pixel_valid、pixel_data、pixel_x/pixel_y
6. 检查 frame_start、line_start、line_done

**sccb_master** 可以独立仿真。

**Testbench 思路：**
1. 给 clk (100MHz)
2. 等待 power-up FSM 完成后 camera_ready=1
3. 发送写命令
4. 检查 sioc_t/siod_t 时序是否符合 SCCB 协议
5. 检查 done 信号

### 层次 2：核心模块仿真

**ov5640_dvp_core** 需要 capture + XPM FIFO。

需要同时提供两个时钟：
- cam_pclk (~56MHz)
- s00_axi_aclk (100MHz)

XPM FIFO 需要 Vivado 仿真库。

### 层次 3：完整系统仿真 (在 Vivado 中)

需要：
1. **AXI Verification IP (VIP)** 模拟 PS7 发起 AXI-Lite 读写
2. **OV5640 DVP 时序模型** 产生摄像头数据 (用 Verilog 写一个简单的 camera model)
3. 检查 DMA 输出到 HP0 的数据是否正确

---

## 二、ov5640_dvp_capture 仿真 Testbench (可直接使用)

```verilog
// dvp_capture_tb.v — DVP 采集模块仿真
`timescale 1ns / 1ps

module dvp_capture_tb;

    // 参数
    localparam PCLK_PERIOD = 17.857;  // ~56MHz
    localparam X_BITS = 12;
    localparam Y_BITS = 12;
    localparam IMG_W = 640;
    localparam IMG_H = 480;

    // 信号
    reg pclk = 0;
    reg rst_n = 0;
    reg cam_vsync = 1;   // 注意: VSYNC_ACTIVE_HIGH=0, 所以初始=1
    reg cam_href = 1;    // 注意: HREF_ACTIVE_HIGH=0, 所以初始=1
    reg [7:0] cam_data = 0;
    reg m_axis_tready = 1;

    wire pixel_valid;
    wire [15:0] pixel_data;
    wire [X_BITS-1:0] pixel_x;
    wire [Y_BITS-1:0] pixel_y;
    wire frame_start;
    wire line_start;
    wire line_done;
    wire [31:0] frame_count;

    // 实例化 DUT
    ov5640_dvp_capture #(
        .VSYNC_ACTIVE_HIGH(0),
        .HREF_ACTIVE_HIGH(0)
    ) u_dut (
        .pclk(pclk), .rst_n(rst_n),
        .cam_vsync(cam_vsync), .cam_href(cam_href), .cam_data(cam_data),
        .pixel_valid(pixel_valid), .pixel_data(pixel_data),
        .pixel_x(pixel_x), .pixel_y(pixel_y),
        .frame_start(frame_start), .line_start(line_start), .line_done(line_done),
        .frame_count(frame_count),
        .m_axis_tready(m_axis_tready)
    );

    // 生成时钟
    always #(PCLK_PERIOD/2) pclk = ~pclk;

    initial begin
        // 第 1 步：复位
        rst_n = 0;
        #100 rst_n = 1;
        #100;

        // 第 2 步：发送一帧
        send_frame(IMG_W, IMG_H);

        // 第 3 步：发送第二帧
        #1000;
        send_frame(IMG_W, IMG_H);

        #10000;
        $finish;
    end

    // 发送一帧的任务
    task send_frame(input integer width, height);
        integer x, y;
        reg [7:0] low_byte, high_byte;

        // VSYNC 下降沿 = 新帧开始
        cam_vsync = 0;  // 低有效: 0=帧有效
        #(PCLK_PERIOD * 4);

        for (y = 0; y < height; y = y + 1) begin
            // HREF 下降沿 = 行有效 (低有效: 0=行有效)
            cam_href = 0;

            for (x = 0; x < width; x = x + 1) begin
                // 发送 2 个字节组成一个像素
                low_byte  = $random;  // RGB565 低字节 (G[2:0]B[4:0])
                high_byte = $random;  // RGB565 高字节 (R[4:3]G[5:3])

                // 第 1 个 pclk: 低字节
                cam_data = low_byte;
                #(PCLK_PERIOD);

                // 第 2 个 pclk: 高字节
                cam_data = high_byte;
                #(PCLK_PERIOD);

                // 预期: pixel_data = {high_byte, low_byte}
                // pixel_x = x, pixel_y = y
            end

            // HREF 上升沿 = 行结束 (回到非有效电平)
            cam_href = 1;
            #(PCLK_PERIOD * 2);
        end

        // VSYNC 上升沿 = 帧结束
        cam_vsync = 1;
        #(PCLK_PERIOD * 4);
    endtask

    // 监视输出
    always @(posedge pclk) begin
        if (pixel_valid) begin
            $display("PIXEL: frame=%d x=%d y=%d data=0x%04x",
                     frame_count, pixel_x, pixel_y, pixel_data);
        end
        if (frame_start) $display("FRAME START: frame=%d", frame_count);
        if (line_done)   $display("LINE DONE: y=%d", pixel_y);
    end

endmodule
```

---

## 三、sccb_master 仿真注意事项

1. 上电时序 (power-up FSM) 需要等待 PWDN_HOLD + RESET_HOLD + POST_RESET
   - 在 100MHz 时钟下: 1ms + 1ms + 20ms = 22ms ≈ 2.2M 时钟周期
   - 仿真时建议调小参数以加速: `#(.PWDN_HOLD_US(1), .RESET_HOLD_US(1), .POST_RESET_US(10))`
2. 需要模拟 SCCB 从设备回复
   - 在 DC 位时检查 SDA 是否被释放
   - 在读阶段驱动 SDA 输入到 siod_i
3. 检查 siod_t/sioc_t 时序
   - Phase 0: 设置 SDA (sioc_t=1/0, siod_t=1/0)
   - Phase 1: 释放 SCL (sioc_t=0), 采样 SDA
   - Phase 2: 保持
   - Phase 3: 拉低 SCL (sioc_t=1)

---

## 四、在 Vivado 中运行仿真

```tcl
# 1. 打开 Vivado 工程
# 2. 设置仿真文件
add_files -norecurse "$REPO_ROOT/fpga/sim/dvp_capture_tb.v"

# 3. 运行行为仿真
# Tools → Simulation → Run Behavioral Simulation
# 或 TCL:
launch_simulation

# 4. 添加波形观察信号
# 右键要观察的信号 → Add to Wave Window

# 5. 运行指定时间
run 10 us
```

---

## 五、常见仿真问题

1. **XPM FIFO 仿真失败**
   - 需要添加 Xilinx 的 xpm 库
   - Vivado 会自动处理，但在 Modelsim 中需要编译 xpm 库

2. **时序违例但不是 Bug**
   - cam_pclk 和 FCLK 是异步时钟域
   - Vivado 报告中会有异步 CDC 路径标记
   - XPM FIFO 本身处理了跨时钟域

3. **仿真速度慢**
   - sccb_master 的 SCCB 频率只有 100kHz
   - 22ms 上电时序 = 2.2M 时钟周期
   - 建议：仿真时修改参数加速，正式综合时用实际参数

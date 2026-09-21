# Vivado ILA 调试 OV5640 DVP AXI-Stream 操作指南

## 原理
ILA (Integrated Logic Analyzer) 是 Vivado 内置的逻辑分析仪 IP。把它加到 Block Design 里，把想观察的 FPGA 内部信号接到它的 probe 上，综合实现后生成带 debug 的 bitstream。板子运行时通过 JTAG 或 XVC（Xilinx Virtual Cable）在 Vivado Hardware Manager 里看实时波形。

## 添加 ILA 的 TCL 方法

在我们的 `build_video_pipeline.tcl` 中添加：

```tcl
# ILA for debugging AXI-Stream
create_bd_cell -type ip -vlnv xilinx.com:ip:ila:6.2 ila_0
set_property -dict [list \
    CONFIG.C_PROBE0_WIDTH {64} \
    CONFIG.C_NUM_OF_PROBES {1} \
    CONFIG.C_MONITOR_TYPE {Native} \
] [get_bd_cells ila_0]

# Connect probe signals (64-bit wide)
# {tlast, tuser, tvalid, tready, tdata[15:0], href, vsync, 28'b0}
connect_bd_net [get_bd_pins ila_0/clk] [get_bd_pins processing_system7_0/FCLK_CLK0]
connect_bd_net [get_bd_pins myOV5640_DVP_0/m_axis_tlast]  ... /* 连到probe0 */
```

实际做法更好：在 Verilog 里用 `(* mark_debug = "true" *)` 标注想观察的信号，让 Vivado 自动识别。

## 观察哪些信号

| 信号 | 含义 | 在哪个位置 |
|------|------|-----------|
| `m_axis_tvalid` | DVP 正在输出有效数据 | DVP core 输出 |
| `m_axis_tready` | 下游(DMA)准备接收 | DMA→DVP |
| `m_axis_tdata[15:0]` | 16位像素数据 | DVP core 输出 |
| `m_axis_tlast` | 帧末标志 | DVP core 输出 |
| `m_axis_tuser` | 帧首标志(SOF) | DVP core 输出 |
| `cam_href` | 摄像头行有效 | 原始引脚 |
| `cam_vsync` | 摄像头帧同步 | 原始引脚 |
| `fifo_empty` | FIFO空 | DVP core 内部 |
| `fifo_full` | FIFO满 | DVP core 内部 |

## 操作流程

1. **Windows 上**：修改 build 脚本加入 ILA，tscr 构建
2. **生成 bitstream**：约 5-8 分钟（ILA 会多用些资源）
3. **部署到 PYNQ**：scp bit + hwh 到板子
4. **加载 overlay**：PYNQ Overlay() 加载
5. **连接 Vivado Hardware Manager**：
   - Windows 上打开 Vivado → Hardware Manager
   - Open Target → Auto Connect（需要 JTAG 连接）
6. **设置触发**：Trigger on `tvalid=1 && tuser=1`（捕获帧首）
7. **抓波形**：Run Trigger，等数据到达

## JTAG 连接问题

PYNQ-Z1 有板载 USB-JTAG。如果是远程调试，可以用 XVC（Xilinx Virtual Cable）通过 TCP/IP 转接 JTAG：
- PYNQ 上运行 `hw_server` 或 XVC server
- Windows Vivado 通过网络连接

## 参考链接
- [ILA 官方文档 UG908](https://docs.amd.com/r/2023.2-English/ug908-vivado-programming-debugging)
- [OV5640 + ILA 调试实战](https://www.cnblogs.com/LiamJacob/p/17476056.html)
- [AXI-Stream 视频流硬件连接](https://www.cnblogs.com/hanhuang/p/19127508)

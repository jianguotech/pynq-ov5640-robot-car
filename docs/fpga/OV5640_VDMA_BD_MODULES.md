# OV5640 VDMA Block Design 模块说明

## 架构图

```
Camera (OV5640)
  │  DVP 8-bit data + pclk
  ▼
┌─────────────────────────────────┐
│ ov5640_dvp_capture (our IP)     │  纯并行输出
│  8→16, polarity, wait 10 frames │
└────────┬────────────────────────┘
         │ px_valid, px_data[15:0], px_frame_start
         ▼
┌─────────────────────────────────┐
│ v_vid_in_axi4s (Xilinx IP)      │  并行→AXI-Stream
│  TUSER/TLAST/TKEEP/CDC (PCLK→FCLK)│
└────────┬────────────────────────┘
         │ m_axis_video (16-bit AXI-Stream)
         ▼
┌─────────────────────────────────┐
│ axis_dwidth_converter           │  位宽转换
│  S_TDATA=16 → M_TDATA=64        │
└────────┬────────────────────────┘
         │ 64-bit AXI-Stream (TKEEP auto)
         ▼
┌─────────────────────────────────┐
│ AXI VDMA (S2MM channel)         │  写 DDR
│  GenLock: S2MM=Master, MM2S=Slave│
└──┬──────┬───────────────────────┘
   │      │ M_AXI_S2MM + M_AXI_MM2S (64-bit AXI4)
   │      ▼
   │ ┌──────────────────────────┐
   │ │ SmartConnect (AXI4→AXI3) │  AXI 互联
   │ └───────────┬──────────────┘
   │             │ AXI3 64-bit
   │             ▼
   │ ┌─── Zynq PS ──────────────┐
   │ │ S_AXI_HP0 → DDR Controller│
   │ │ AXI_GP0 → AXI Interconnect│
   │ │   ├─ M00 → VDMA S_AXI_LITE│
   │ │   └─ M01 → axi_intc S_AXI │
   │ │ IRQ_F2P ← axi_intc irq    │
   │ └──────────────────────────┘
   │
   └─ s2mm_introut + mm2s_introut → xlconcat → axi_intc → PS IRQ_F2P

SCCB (I2C via FPGA pins):
  cfg_start_one → cfg_init_ctrl → sccb_master → OV5640
  init_done / cfg_error → (not used by capture)
  
Clock Tree:
  IO_PLL(1000MHz) → FCLK_CLK0(100MHz) → VDMA, SmartConnect, vid_in, intc, periph
  FCLK_CLK0 → clk_wiz → XCLK(24MHz) → OV5640
  OV5640 → cam_pclk(~48MHz) → capture, vid_in(vid_io_in_clk)
```

---

## 模块详细说明

### 1. processing_system7_0 (Zynq PS7)

| 信号 | 方向 | 连接 | 说明 |
|------|------|------|------|
| FCLK_CLK0 | 输出 | 所有模块的 aclk | 100MHz PL 主时钟 |
| FCLK_RESET0_N | 输出 | rst_ps7_0_100M/ext_reset_in | 复位源 |
| M_AXI_GP0 | 主设备 | ps7_0_axi_periph/S00_AXI | PS 访问 PL 外设的总线 |
| S_AXI_HP0 | 从设备 | SmartConnect/M00_AXI | PL 访问 DDR 的入口（AXI3, 64-bit） |
| IRQ_F2P[0:0] | 输入 | axi_intc_0/irq | PL→PS 中断 |
| DDR | 外部 | DDR 端口 | 片外 DDR 内存 |
| FIXED_IO | 外部 | 固定 IO | USB/UART/SD 等 |

**配置关键参数：**
- `PCW_USE_FABRIC_INTERRUPT {1}` — 使能 PL 中断
- `PCW_S_AXI_HP0_DATA_WIDTH {64}` — HP0 64-bit
- `PCW_FPGA0_PERIPHERAL_FREQMHZ {100}` — FCLK0=100MHz

---

### 2. ps7_0_axi_periph (AXI Interconnect)

连接 PS GP0 到 PL 外设寄存器。NUM_MI=2。

| 端口 | 连接 |
|------|------|
| S00_AXI | processing_system7_0/M_AXI_GP0 |
| M00_AXI | axi_vdma_0/S_AXI_LITE |
| M01_AXI | axi_intc_0/S_AXI |
| ACLK/S00_ACLK/M00_ACLK/M01_ACLK | FCLK_CLK0 |
| ARESETN/S00_ARESETN/M00_ARESETN/M01_ARESETN | rst_ps7_0_100M/peripheral_aresetn |

---

### 3. rst_ps7_0_100M (Processor System Reset)

| 端口 | 连接 |
|------|------|
| ext_reset_in | FCLK_RESET0_N |
| slowest_sync_clk | FCLK_CLK0 |
| peripheral_aresetn | 所有 PL 模块复位 |

---

### 4. ov5640_dvp_capture_0 (our DVP Capture IP)

**单模块 IP — 132 行 Verilog，无 AXI 接口**

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| pclk | 输入 | cam_pclk_i (~48MHz) | 摄像头像素时钟 |
| rst_n | 输入 | FCLK_RESET0_N | 复位（高有效） |
| cam_vsync | 输入 | cam_vsync_i | 场同步（active-low per 0x4740=0x22） |
| cam_href | 输入 | cam_href_i | 行有效（active-low） |
| cam_data[7:0] | 输入 | cam_data_i | DVP 8-bit 数据 |
| px_valid | 输出 | vid_in_0/vid_active_video | 像素有效 |
| px_data[15:0] | 输出 | vid_in_0/vid_data | RGB565 像素 |
| px_frame_start | 输出 | vid_in_0/vid_vsync | 帧起始脉冲 |
| px_line_end | 输出 | (悬空) | 行结束脉冲 |
| capture_active | 输出 | (悬空) | 10 帧等待后拉高 |
| dbg_led[3:0] | 输出 | 外部 LED 端口 | 调试指示灯 |

**内部行为：**
- 上电复位后跳过前 10 帧（等待 SCCB 配置生效）
- byte_flag 脉冲将 2 个 8-bit DVP 字节拼为 1 个 16-bit RGB565
- VSYNC rising edge → frame_start 脉冲
- HREF falling edge → line_end 脉冲

---

### 5. clk_wiz_0 (Clock Wizard)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| clk_in1 | 输入 | FCLK_CLK0 (100MHz) | 参考时钟 |
| clk_out1 | 输出 | cam_xclk_o | 24MHz XCLK 给 OV5640 |
| locked | 输出 | (未连接) | PLL 锁定标志 |
| reset | 输入 | GND | 不复位 |

**配置：** 100MHz in → 24MHz out, No Buffer

---

### 6. sccb_master_0 (SCCB I2C Master — wyf IP)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| clk | 输入 | FCLK_CLK0 | 驱动时钟 |
| rst_n | 输入 | peripheral_aresetn | 复位 |
| cmd_valid | 输入 | cfg_init_ctrl_0/sccb_cmd_valid | 命令有效 |
| cmd_ready | 输出 | cfg_init_ctrl_0/sccb_cmd_ready | 准备接受 |
| cmd_rw | 输入 | cfg_init_ctrl/sccb_cmd_rw | 读写选择 |
| dev_addr[7:0] | 输入 | cfg_init_ctrl/sccb_dev_addr | OV5640 地址(0x3c) |
| reg_addr[15:0] | 输入 | cfg_init_ctrl/sccb_reg_addr | 寄存器地址 |
| wr_data[7:0] | 输入 | cfg_init_ctrl/sccb_wr_data | 写数据 |
| rd_data[7:0] | 输出 | cfg_init_ctrl/sccb_rd_data | 读数据 |
| done | 输出 | cfg_init_ctrl/sccb_done | 传输完成 |
| error | 输出 | cfg_init_ctrl/sccb_error | 传输失败 |
| camera_ready | 输出 | cfg_init_ctrl/camera_ready | OV5640 上电就绪 |
| ov_reset_n | 输出 | cam_resetb_o | 摄像头复位 |
| ov_pwdn | 输出 | cam_pwdn_o | 摄像头掉电 |
| sioc_o | 输出 | scl_obuft_0/scl_i | SCL 输出 |
| sioc_t | 输出 | scl_obuft_0/scl_t | SCL 三态 |
| siod_o | 输出 | sda_iobuf_0/sda_i_fpga | SDA 输出 |
| siod_t | 输出 | sda_iobuf_0/sda_t_fpga | SDA 三态 |
| siod_i | 输入 | sda_iobuf_0/sda_o_fpga | SDA 输入 |

---

### 7. cfg_init_ctrl_0 (OV5640 Config Controller — wyf IP)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| clk | 输入 | FCLK_CLK0 | 驱动时钟 |
| rst_n | 输入 | peripheral_aresetn | 复位 |
| start | 输入 | cfg_start_one/dout (常量1) | 自动启动 |
| init_done | 输出 | (悬空) | 配置完成 |
| cfg_error | 输出 | (悬空) | 配置失败 |
| sccb_* | — | → sccb_master_0 | SCCB 总线抽象 |

**包含 3 张 ROM 表：Preamble(2条) + Init(207条) + Mode(60条)**

---

### 8. vid_in_0 (v_vid_in_axi4s v5.0)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| vid_active_video | 输入 | capture/px_valid | 像素有效 |
| vid_data[15:0] | 输入 | capture/px_data | 像素数据 |
| vid_vsync | 输入 | capture/px_frame_start | 帧同步 |
| aclk | 输入 | FCLK_CLK0 | AXI 时钟 |
| aresetn | 输入 | peripheral_aresetn | AXI 复位 |
| video_out | 主设备 | axis_dwidth_0/S_AXIS | AXI-Stream 输出（16-bit） |

**配置：** `C_M_AXIS_TDATA_WIDTH=16`, `C_HAS_ACTIVE_VIDEO=1`, `C_HAS_VSYNC=1`, `C_HAS_HSYNC=0`

**内部行为：**
- vid_vsync 边沿 → TUSER(SOF) 脉冲
- vid_active_video 下降 → TLAST(EOL)
- vid_data → TDATA
- vid_io_in_clk 域 → aclk 域 CDC 转换
- TKEEP 自动生成（所有字节有效）

---

### 9. axis_dwidth_0 (axis_dwidth_converter)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| S_AXIS | 从设备 | vid_in_0/video_out | 16-bit AXI-Stream 输入 |
| M_AXIS | 主设备 | axi_vdma_0/S_AXIS_S2MM | 64-bit AXI-Stream 输出 |
| aclk | 输入 | FCLK_CLK0 | 时钟 |
| aresetn | 输入 | peripheral_aresetn | 复位 |

**配置：** `S_TDATA_NUM_BYTES=2, M_TDATA_NUM_BYTES=8`

**已知警告：** TUSER_BITS_PER_BYTE forced to 0（在 free-run 模式下不影响）

---

### 10. axi_vdma_0 (AXI VDMA v6.3)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| S_AXIS_S2MM | 从设备 | axis_dwidth_0/M_AXIS | 64-bit 视频流输入 |
| M_AXIS_MM2S | 主设备 | (悬空) | MM2S 流输出（不用） |
| M_AXI_S2MM | 主设备 | SmartConnect/S00_AXI | S2MM 写 DDR 端口 |
| M_AXI_MM2S | 主设备 | SmartConnect/S01_AXI | MM2S 读 DDR 端口 |
| S_AXI_LITE | 从设备 | ps7_0_axi_periph/M00_AXI | 寄存器接口 |
| s2mm_introut | 输出 | vdma_intr_concat/In0 | S2MM 中断 |
| mm2s_introut | 输出 | vdma_intr_concat/In1 | MM2S 中断 |
| m_axi_s2mm_aclk | 输入 | FCLK_CLK0 | S2MM mem 时钟 |
| m_axi_mm2s_aclk | 输入 | FCLK_CLK0 | MM2S mem 时钟 |
| m_axis_mm2s_aclk | 输入 | FCLK_CLK0 | MM2S stream 时钟 |
| s_axi_lite_aclk | 输入 | FCLK_CLK0 | 寄存器时钟 |
| s_axis_s2mm_aclk | 输入 | FCLK_CLK0 | S2MM stream 时钟 |
| axi_resetn | 输入 | peripheral_aresetn | 复位 |

**S2MM 寄存器关键偏移：**
| 偏移 | 名称 | 说明 |
|------|------|------|
| 0x30 | DMACR | 控制（RS=bit0, GenLockEn=bit3, IOC_Irq=bit12） |
| 0x34 | DMASR | 状态（Halted=bit0, SlvErr=bit4, IOC_Irq=bit12） |
| 0xA0 | VSIZE | 垂直大小（行数），须最后写 |
| 0xA4 | HSIZE | 水平大小（字节/行） |
| 0xA8 | STRIDE | 步长（字节/行） |
| 0xAC-0xB8 | START1-4 | 帧缓冲区起始地址 |

**MM2S 寄存器关键偏移（同结构，基址=0x00）：**
| 偏移 | 名称 |
|------|------|
| 0x00 | DMACR |
| 0x04 | DMASR |
| 0x50 | VSIZE |
| 0x54 | HSIZE |
| 0x58 | STRIDE |
| 0x5C-0x68 | START1-4 |

---

### 11. axi_mem_intercon (SmartConnect)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| S00_AXI | 从设备 | axi_vdma_0/M_AXI_S2MM | S2MM 写 |
| S01_AXI | 从设备 | axi_vdma_0/M_AXI_MM2S | MM2S 读 |
| M00_AXI | 主设备 | processing_system7_0/S_AXI_HP0 | 接 DDR 控制器 |
| aclk | 输入 | FCLK_CLK0 | 时钟 |
| aresetn | 输入 | peripheral_aresetn | 复位 |

**配置：** NUM_SI=2, NUM_MI=1
**已知问题：** AXI4→AXI3 转换在高版本 Vivado 中可能产生死锁

---

### 12. axi_intc_0 (AXI Interrupt Controller)

| 端口 | 方向 | 连接 | 说明 |
|------|------|------|------|
| intr[1:0] | 输入 | vdma_intr_concat/dout | VDMA 中断（bit0=S2MM, bit1=MM2S） |
| irq | 输出 | processing_system7_0/IRQ_F2P[0:0] | 中断输出到 PS |
| S_AXI | 从设备 | ps7_0_axi_periph/M01_AXI | 寄存器接口 |
| s_axi_aclk | 输入 | FCLK_CLK0 | 时钟 |
| s_axi_aresetn | 输入 | peripheral_aresetn | 复位 |

---

### 13. vdma_intr_concat (xlconcat)

| 端口 | 连接 |
|------|------|
| In0 | axi_vdma_0/s2mm_introut |
| In1 | axi_vdma_0/mm2s_introut |
| dout[1:0] | axi_intc_0/intr |

---

### 14. SCCB I2C 物理层模块

| 模块 | 端口 | 连接 |
|------|------|------|
| sda_iobuf_0 | sda_pin → ov_sda, sda_o_fpga → siod_i, sda_i_fpga ← siod_o, sda_t_fpga ← siod_t | I2C SDA 双向 |
| scl_obuft_0 | scl_pin → ov_scl, scl_i ← sioc_o, scl_t ← sioc_t | I2C SCL 开漏输出 |

---

## 时钟域

| 域 | 频率 | 模块 |
|----|------|------|
| FCLK (PS) | 100MHz | VDMA, SmartConnect, vid_in(aclk), axis_dwidth, periph, intc, SCCB |
| PCLK (Camera) | ~48MHz | ov5640_dvp_capture |
| XCLK (PLL) | 24MHz | OV5640 主时钟 |

## 复位树

```
FCLK_RESET0_N → rst_ps7_0_100M/ext_reset_in
                  └→ peripheral_aresetn → 所有 PL 模块
ov5640_dvp_capture/rst_n ← FCLK_RESET0_N (直连，PCLK 域内用 WAIT_FRAME 处理异步)
```

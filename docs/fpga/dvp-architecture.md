# Clean DVP IP 架构说明

## 文件结构

```
ov5640_dvp_capture.v          ← 手写：摄像头原始信号 → 像素拼接 + 行帧跟踪
ov5640_dvp_core.v             ← 手写：XPM FIFO 跨时钟域 + AXI-Stream 输出
myOV5640_DVP_v1_0_S00_AXI.v   ← Vivado标准：AXI4-Lite slave（不要手改！）
myOV5640_DVP_v1_0.v           ← Vivado自动生成：顶层 wrapper
component.xml                 ← IP 定义文件
```

## 数据流

```
OV5640 摄像头
  │ PCLK, VSYNC, HREF, D[7:0]
  ▼
ov5640_dvp_capture  (PCLK 域)
  │ 8bit → 16bit RGB565 拼接
  │ 行/帧边界检测
  │ 输出：pixel_valid, pixel_data[15:0], SOF, EOL
  ▼
ov5640_dvp_core     (PCLK → FCLK CDC)
  │ XPM_FIFO_ASYNC (4096 deep, FWFT mode)
  │ FCLK 域保持计数器
  │ 输出：AXI-Stream (tdata, tvalid, tlast, tuser)
  ▼
myOV5640_DVP_v1_0_S00_AXI  (FCLK 域)
  │ AXI4-Lite slave (GP0)
  │ 寄存器读写 (CTRL, STATUS, counters)
  │ 顶层 wrapper 集成
  ▼
AXI DMA / VDMA  (FCLK 域)
  │ S_AXIS_S2MM 接收像素流
  │ M_AXI_S2MM → SmartConnect → HP0 → DDR
  ▼
PS DDR (PYNQ Python 读取)
```

## 各模块详解

### 1. ov5640_dvp_capture.v（PCLK 域，~56MHz）

**输入**：摄像头原始信号
- `vsync_raw`：VSYNC 引脚（active HIGH 脉冲标记新帧）
- `href_raw`：HREF 引脚（active LOW = 行数据有效）
- `data_raw[7:0]`：8 位数据

**输出**：
- `pixel_valid`：每 2 个 PCLK 产生一个完整像素
- `pixel_data[15:0]`：RGB565 = {R[4:0], G[5:0], B[4:0]}
- `pixel_x[11:0]`, `pixel_y[11:0]`：像素坐标
- `frame_start`, `line_start`, `line_end`

**核心逻辑**：
```verilog
// HREF 低有效 → 内部转为高有效
wire href = ~href_raw;

// 字节拼接：HIGH byte 先到
// PCLK N:   first_byte = data_raw  (HIGH byte)
// PCLK N+1: pixel_data = {first_byte, data_raw}  (LOW byte)
assign pixel_valid = href && byte_toggle;

// 行尾检测 (640px/line)
assign line_end = pixel_valid && (col == 11'd639);
```

### 2. ov5640_dvp_core.v（PCLK + FCLK 域）

**CDC**：XPM_FIFO_ASYNC (4096 deep, FWFT, 4-stage sync)
- 写侧(PCLK)：`fifo_wren = px_valid && se_pclk && !fifo_full`
- 读侧(FCLK)：`fifo_rd_en = m_axis_tready && !fifo_empty`
- FIFO 数据：`{SOF, EOL, pixel_data[15:0]}` = 18-bit

**AXI-Stream 输出**：
- `tdata`：像素数据
- `tvalid`：FCLK 稳定(16周期)后 + FIFO 非空
- `tlast`：每行最后一个像素（EOL）
- `tuser`：每帧第一个像素（SOF）

**cam_force_reset CDC**：2-FF 同步到 PCLK 域，避免毛刺

### 3. myOV5640_DVP_v1_0_S00_AXI.v（FCLK 域，100MHz）

**AXI4-Lite slave**（GP0，地址 0x40000000）：
| 偏移 | 名称 | R/W | 内容 |
|------|------|-----|------|
| 0x00 | CTRL | RW | bit0=pwdn, bit1-2=force_reset, bit3=stream_enable |
| 0x04 | STATUS | RO | xclk_locked, sccb_done, cam_vsync, cam_href, pixel_seen... |
| 0x08 | - | RO | 保留(返回0) |
| 0x0C | LINE_CNT | RO | 行计数 |
| 0x10 | PIXEL_CNT | RO | 像素计数 |
| 0x14 | LAST_PX | RO | 最后一个像素值 |
| 0x18 | LAST_XY | RO | 最后像素坐标 |
| 0x1C | REG7 | RW | 暂存寄存器 |

**关键**：此文件是 Vivado 自动生成的标准 AXI4-Lite 模板，**不要手动修改**。
修改必须通过 Vivado 的 "Create and Package IP" 流程。

## PYNQ 测试流程

```python
from pynq import Overlay, allocate
import numpy as np

# 1. 加载 overlay
ol = Overlay('/home/xilinx/design.bit')
dvp = ol.myOV5640_DVP_0  # CTRL/STATUS 寄存器
dma = ol.axi_dma_0        # DMA 控制

# 2. 等待 SCCB 初始化
while not (dvp.read(0x04) >> 7) & 1:
    time.sleep(0.5)

# 3. 关 stream → 配置 DMA → 开 stream
dvp.write(0x00, 0x00)     # stream off
buf = allocate(shape=(307200,), dtype=np.uint16)
dma.recvchannel.transfer(buf)
dvp.write(0x00, 0x08)     # stream on (bit3=1)

# 4. 等待 DMA 完成
dma.recvchannel.wait(timeout=5)

# 5. 检查数据
print(np.count_nonzero(buf))  # 非零像素数
```

## SCCB 配置（同学 wyf 的 IP）

- `cfg_init_ctrl` + `sccb_master`：自动在 FPGA 配置后初始化 OV5640
- 配置 0x4300 = 0x6F（RGB565, 字节序）
- 配置 0x4740 = 0x22（PCLK=HIGH, HREF=LOW, VSYNC=HIGH）
- 目标分辨率：640×480@30fps
- `sccb_init_done=1` 表示配置完成

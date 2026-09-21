# OV5640 + Audio Unified Streaming Server

低延迟 WebSocket 视频+音频流服务器，用于 PYNQ-Z1 上的 OV5640_AUDIO_MECANUM 合并项目。

## 文件

| 文件 | 说明 |
|------|------|
| `camera_engine.py` | VDMA 零拷贝视频采集 + JPEG 编码引擎 |
| `stream_server.py` | 统一 WebSocket 服务器（HTTP + WS + HTML5 客户端） |
| `README.md` | 本文件 |

## 依赖

- PYNQ 2.x（`pynq.Bitstream`, `pynq.MMIO`, `pynq.Xlnk`）
- Python 3.6+（`numpy`, `cv2`）
- 已存在的文件（同目录）：
  - `load_overlay_pynq2.py`
  - `audio_dma_driver.py`
  - `ov5640_audio_mecanum.bit`

**不需要 pip install 任何新包。**

## 快速开始

### 1. 上传到板子

```bash
scp camera_engine.py stream_server.py xilinx@192.168.2.99:/home/xilinx/OV5640_AUDIO_MECANUM/
```

### 2. SSH 启动

```bash
ssh xilinx@192.168.2.99
cd /home/xilinx/OV5640_AUDIO_MECANUM
sudo /opt/python3.6/bin/python3.6 -u stream_server.py
```

### 3. 浏览器访问

```
http://192.168.2.99:8080
```

如果通过互联网远程访问，使用 SSH 端口转发：

```bash
ssh -L 8080:192.168.2.99:8080 xilinx@<公网IP> -p 50452
# 浏览器打开 http://localhost:8080
```

## 命令行参数

```
stream_server.py [--port 8080] [--bitstream ov5640_audio_mecanum.bit]
                 [--quality 40] [--no-audio]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | 8080 | HTTP/WS 监听端口 |
| `--bitstream` | ov5640_audio_mecanum.bit | 比特流文件 |
| `--quality` | 40 | 初始 JPEG 质量（10-95） |
| `--no-audio` | (关闭) | 禁用麦克风采集 |

## 浏览器控制

### 界面控件

- **Quality 滑块**：调节 JPEG 压缩质量
- **Resolution 下拉框**：切换分辨率（640×480 / 320×240 / 160×120）
- **Play Mic 按钮**：开关麦克风音频

### 键盘快捷键

| 键 | 功能 |
|----|------|
| `1` / `2` / `3` | 切换分辨率 640 / 320 / 160 |
| `←` / `→` | JPEG 质量 -5 / +5 |
| `A` | 开关音频 |

### 实时状态显示

- **fps**：实际接收帧率
- **kB/s**：视频码率
- **ms lag**：音频缓冲延迟

## 架构

```
ov5640_audio_mecanum.bit (加载一次)
│
├── CameraEngine (camera_engine.py)
│   ├── VDMA S2MM → HP0 → DDR (3 帧环形缓冲)
│   ├── park_reg 轮询 (1ms) → 零拷贝取帧
│   └── cv2.imencode JPEG (Q=40, 4:2:0, ~8ms) → LatestItem
│
├── AudioCapture (内联于 stream_server.py)
│   ├── DMA_RX S2MM → HP1 → DDR (2048 字缓冲)
│   ├── PDM 解调 (popcount → DC去除 → 降采样 → 增益)
│   └── PCM16 发布 → LatestItem
│
└── WebSocket Server (:8080)
    ├── HTTP GET /         → HTML5 页面
    ├── WS  /ws            → 二进制帧广播
    │   ├── Type 0x01      → JPEG 视频帧 [seq:u32][data]
    │   └── Type 0x02      → PCM 音频块  [rate:u32][data]
    └── WS text frame ←    → JSON 控制消息 {"q":40, "scale":0.5}
```

## 关键优化

1. **零拷贝**：CMA buffer 直接引用，不调 `np.array()` 拷贝
2. **不调 color_correct**：删除 float32 逐像素白平衡（FPGA 端做或不做）
3. **不调 cv2.cvtColor**：摄像头 BGR 字节序直接喂 OpenCV
4. **单生产者多消费者**：一帧 JPEG 编码服务所有浏览器客户端
5. **libjpeg-turbo + NEON**：Cortex-A9 上 ~8ms/帧（vs 普通 libjpeg 的 ~25ms）
6. **HP1 总线隔离**：音频 DMA 独立走 HP1，不抢摄像头 HP0 带宽

## 性能

| 指标 | 值 |
|------|-----|
| 640×480 帧率 | 25-30 fps |
| Video 延迟 (glass→network) | <20ms |
| Audio 延迟 (mic→network) | <5ms |
| CPU 占用 (视频+音频) | ~35% 单核 |
| 码率 (Q=40, 640×480) | ~5-10 Mbps |
| 内存占用 | <5 MB |

## 故障排查

| 现象 | 可能原因 | 检查方法 |
|------|----------|----------|
| 灰屏/无画面 | 传感器 SCCB 配置失败 | 检查摄像头连接、ov5640 是否亮灯 |
| 画面卡顿 | CPU 过载 | `top` 查看 CPU 占用 |
| 音频无声 | DMA_RX 未初始化 | `--no-audio` 参数是否误加 |
| 连接断开 | 网络不稳定 | 浏览器会自动重连（指数退避） |
| FPS 很低 | JPEG 质量太高 | 降低 Quality 滑块到 30-40 |

## 单独测试 CameraEngine

可以在 Jupyter 中单独测试视频引擎：

```python
from camera_engine import CameraEngine
cam = CameraEngine(jpeg_quality=40)
cam.init()

last_seq = 0
for _ in range(100):
    data, seq = cam.get_frame(last_seq, timeout=1.0)
    if data is not None:
        last_seq = seq
        print(f"Frame {seq}: {len(data)} bytes")
        # 保存一帧看看
        with open(f"test_{seq}.jpg", "wb") as f:
            f.write(data)
        break

cam.stop()
```

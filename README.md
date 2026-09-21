# PYNQ-Z1 OV5640 智能小车

这是一个基于 PYNQ-Z1 的智能小车项目，包含 OV5640 视频采集、PL 侧 DMA/VDMA 通路、PS 端图像处理与运动控制、WebSocket 中继、浏览器遥控台、自动循迹和语音助手。

## 系统链路

```text
OV5640
  | DVP + SCCB
  v
自定义 DVP IP -> AXI4-Stream -> AXI DMA/VDMA -> Zynq HP 端口 -> DDR
                                                         |
                                                         v
                         PYNQ Python -> JPEG/共享帧 -> WebSocket
                                                         |
                                                         v
                                              浏览器视频与遥控台
```

运动控制、自动循迹和语音链路通过 PS 端共享运行目录及 UDP/AXI 寄存器协同工作。Web 中继负责视频、控制、状态和音频通道。

## 目录

- `fpga/ip`：DVP、SCCB 和 OV5640 初始化自定义 IP。
- `fpga/scripts`：Vivado Block Design 构建与 IP 打包脚本。
- `fpga/sim`：DVP 捕获与 CDC FIFO 仿真。
- `fpga/tools`：PYNQ 上的寄存器、bitstream 和单帧 DMA 诊断工具。
- `fpga/vivado/OV5640_DMA_FINAL`：从 Windows 工作站导出的 AXI DMA 工程快照、精确 IP 源码和可部署 bit/hwh。
- `pynq`：板端启动、图传、运动控制、循迹、音频和测试代码。
- `web`：轻量视频服务、中继服务和浏览器控制台。
- `docs`：架构、状态机、仿真和数据流说明。

## 关键硬件约束

### DVP 控制寄存器

自定义 DVP IP 的控制寄存器位于偏移 `0x00`：

| 位 | 名称 | 含义 |
|---|---|---|
| bit 0 | `cam_pwdn` | `1` 使摄像头进入掉电状态，`0` 正常工作 |
| bit 1-2 | `force_reset` | 强制复位 |
| bit 3 | `stream_enable` | `1` 允许视频流进入 AXI-Stream |

正常开启视频流应写 `0x08`，不要把 bit 0 当成使能位。

### DMA 启动顺序

AXI DMA 使用 Direct Register/Simple S2MM 模式。安全的单帧顺序是：

1. 保持 DVP `stream_enable=0`。
2. 复位 DMA 并清除状态。
3. 设置 `S2MM_DMACR.RS=1`。
4. 写入已分配缓冲区的物理地址到 `S2MM_DA` (`0x48`)。
5. 最后写 `S2MM_LENGTH` (`0x58`)；写 LENGTH 会启动传输。
6. 确认 DMA 已运行后，再向 DVP 控制寄存器写 `0x08`。
7. 传输完成或超时后关闭视频流。

DMA 目标地址必须来自 CMA 或明确保留的连续物理内存。不要使用未经保留的固定 DDR 地址；错误地址可能覆盖 Linux 内核或用户空间内存并导致 SSH、网络或系统失去响应。

## Vivado 工程

工程目标器件为 `xc7z020clg400-1`，原工程使用 Vivado 2023.2。`OV5640_DMA_FINAL` 目录保留了 Windows 上完成综合的工程快照：

- `exact-ip-src` 是该次构建生成目录中提取出的精确自定义 IP 源码。
- `latest-windows-src` 是工作站上时间更新的实验源码，不保证与随附 bitstream 对应。
- `output/design_1_wrapper.bit` 与 `output/design_1.hwh` 是同一工程的配套产物。
- `fpga/ip/myOV5640_DVP` 是后续 clean IP 版本，和 DMA_FINAL 快照分开保留。

打开工程前请阅读 [Vivado 快照说明](fpga/vivado/OV5640_DMA_FINAL/README.md)。

## 当前验证状态

- Python 文件可通过 `compileall`，Shell 启动脚本可通过 `bash -n`。
- DMA_FINAL RTL 可由 Icarus Verilog 编译；完整帧仿真收到 `307200` 个像素，TUSER 位于首像素。
- 同一仿真当前未观察到 TLAST，说明帧末计数/打包逻辑仍需修复后再进行 DMA 实板测试。
- clean IP 仿真仍会出现未初始化状态值；不能仅凭现有仿真认定视频链路已经稳定。

在修复并重新生成 bitstream 前，随附输出应视为可复现的工程快照，而不是最终稳定发布版。

## 板端运行

板端代码按功能拆分在 `pynq` 中。部署时需要：

- 将实际使用的 `.bit` 与 `.hwh` 放到板端约定目录。
- 根据各子目录 README 安装 Python 依赖并配置 systemd/启动脚本。
- 使用环境变量配置中继地址、鉴权 token 和语音云服务密钥。
- 真实凭据只保存在未纳入 Git 的 `.env` 文件中；参考 `pynq/06_语音助手/audio_keys.env.example`。

仓库不包含课程报告、答辩文件、过程交接记录、下载的参考工程、Vivado 缓存或真实凭据。

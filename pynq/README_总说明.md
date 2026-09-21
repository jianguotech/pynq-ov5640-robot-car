# 小车 PS 端最终有效代码包

这里保留最终使用的 PS 端、网页端和服务器端代码。PL/Vivado 工程位于仓库顶层的 `fpga` 目录。

## 目录说明

- 01_一键启动和停止：小车端整套程序启动、停止、前台运行和重新加载 PL 的脚本。
- 02_车端通信和共享数据：小车与中继服务器通信，以及共享状态/图像/命令文件。
- 03_运动控制和状态机：遥控、急停、循迹切换、控制优先级和 AXI 电机写寄存器。
- 04_摄像头图传和底层音频：VDMA 图像采集、JPEG 压缩、图传共享帧和底层音频 DMA。
- 05_自动循迹：传统视觉循迹算法、运行入口、调参 JSON 和安全停车。
- 06_语音助手：车载语音识别、问答、TTS 播报和板端音频服务。
- 07_网页和中继服务器：网页控制台和 relay 服务端代码。
- 08_上电自启动加载：PYNQ 开机自动加载最终 bitstream 的脚本和 systemd 服务。
- 09_测试工具：电机、遥控、dry-run、模拟数据和日志查看工具。

## 主运行链路

网页控制台发出遥控/急停/循迹/语音指令，quick_relay_server.py 负责中继和权限判断；小车端 car_net_client.py 接收命令并写入共享目录/UDP；remote_motion_receiver_demo.py 负责状态机和 AXI 电机控制；pynq_compressed_camera_writer.py 负责图像帧采集并交给 car_net_client.py 上传；line_follow_drive.py 在自动循迹时根据图像计算运动控制量；car_audio_assistant.py 负责语音助手链路。

## 注意事项

- 真实语音云服务密钥 audio_keys.env 未打包，避免泄露；请参考 06_语音助手/audio_keys.env.example 自行填写。
- 中继地址和鉴权 token 通过 `CAR_RELAY_URL`、`CAR_RELAY_WEB_URL` 和 `CAR_RELAY_TOKEN` 配置。
- 完整运行需要将对应的 bitstream 和 HWH 部署到板端约定目录。

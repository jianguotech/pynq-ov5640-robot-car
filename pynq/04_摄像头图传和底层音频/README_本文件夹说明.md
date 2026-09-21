# 04 摄像头图传和底层音频

这个文件夹负责摄像头图像采集、JPEG 压缩、共享帧写入，以及视频/音频底层 DMA 相关代码。

- pynq_compressed_camera_writer.py：正式图传写入程序。调用 CameraEngine 从 VDMA 取帧并压缩为 JPEG，写入 /dev/shm/car_runtime/latest_frame.jpg，供 car_net_client.py 上传。
- run_camera_compressed.sh：启动图传写入程序的脚本，指定 hp1 bitstream、共享目录、图像大小、JPEG 质量、缩放比例和帧率。
- stream_server_pack/camera_engine.py：摄像头采集核心。负责 VDMA 零拷贝取帧、JPEG 编码、最新帧缓存和帧率统计。
- stream_server_pack/stream_server.py：独立视频/音频 WebSocket 流服务器，可用于单独调试本地图传和音频流。
- stream_server_pack/audio_dma_driver.py：音频 DMA 驱动，封装播放和录音所需的 DMA 缓冲、采样率校准和 PCM/PDM 转换。
- stream_server_pack/load_overlay_pynq2.py：PYNQ 2.x 兼容的 overlay 加载和 MMIO/DMA 封装工具。
- stream_server_pack/README_原始说明.md：原始视频/音频流服务说明。

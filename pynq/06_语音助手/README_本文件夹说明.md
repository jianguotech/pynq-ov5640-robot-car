# 06 语音助手

这个文件夹负责车载语音助手：麦克风输入、阿里云 ASR、DeepSeek 问答、阿里云 TTS、车载喇叭播报，以及和网页语音面板通信。

- run_audio_assistant.sh：语音助手启动脚本。启动板端 board_server，再启动 car_audio_assistant.py，可使用 attach 模式避免重复下载 bitstream。
- car_audio_assistant.py：语音助手主程序。连接中继服务器 /car/audio，处理网页开启/关闭音频、文字播报、ASR 结果、DeepSeek 回答和 TTS 播放。
- asr_aliyun.py：阿里云实时语音识别客户端，把麦克风 PCM 流转换成中文文字，支持中间结果和一句话定稿。
- tts_aliyun.py：阿里云语音合成客户端，把文字转换为 16k PCM 音频，供板端喇叭播放。
- deepseek_chat.py：DeepSeek 对话客户端，根据语音识别文本生成简短口语化回答。
- board_server_stable.py：稳定启动入口，导入 jupyter_notebooks/pynq_app/board_server.py 后启动板端音频服务。
- board_audio_server/board_server.py：板端音频服务，提供 MIC 和 PLAY 两个 TCP 角色，实现录音流和喇叭播报的半双工控制。
- board_audio_server/audio_dma_driver.py：板端音频 DMA 驱动，负责音频播放/录音缓冲和速率校准。
- board_audio_server/load_overlay_pynq2.py：音频 overlay 和 DMA MMIO 加载工具。
- audio_keys.env.example：密钥模板。真实 audio_keys.env 不打包，避免泄露阿里云和 DeepSeek 密钥。

# 01 一键启动和停止

这个文件夹放的是 PYNQ 小车端整套程序的启动、停止和前台守护脚本。

- start_live_pipeline.sh：正式启动脚本。先停止旧进程，再启动摄像头图传、运动控制接收器、车端网络客户端，并尝试启动语音助手。
- stop_pipeline.sh：正式停止脚本。先向运动接收器发送 STOP，再结束网络、摄像头、运动控制和语音相关进程，避免 Ctrl+C 后车继续动。
- run_live_pipeline_foreground.sh：前台运行版启动脚本。适合调试，能监控子进程是否退出，任一关键进程退出后会自动停止整套 pipeline。
- load_final_pl.sh：重新调用 systemd 服务加载最终 hp1 bitstream，并显示加载服务状态。

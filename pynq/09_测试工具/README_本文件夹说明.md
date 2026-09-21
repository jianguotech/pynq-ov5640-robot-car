# 09 测试工具

这个文件夹保留最终有用的测试和验收工具，不包含历史备份。

- motor_quick_test.py：电机快速测试工具。直接通过 /dev/mem 写 AXI 寄存器，测试前进、后退、左右平移、旋转和版本号/心跳。
- remote_control_sender_example.py：远程控制命令发送示例，给对接同学或本地测试用。
- sim_car_memory_writer.py：模拟车端状态/图像写入共享目录，用于不接真实小车时测试网页和中继链路。
- start_dry_pipeline.sh：dry-run 测试启动脚本，不实际写 AXI，适合先验证网络和状态机。
- run_motion_receiver_dry.sh：dry-run 运动接收器启动脚本，只打印控制数据，不驱动电机。
- tail_logs.sh：查看运行日志的辅助脚本。

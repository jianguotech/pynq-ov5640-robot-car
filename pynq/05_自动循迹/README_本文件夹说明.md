# 05 自动循迹

这个文件夹负责视觉循迹，从图像中提取跑道目标位置，再转换成小车运动控制量。

- line_follow_drive.py：循迹运行主程序。读取共享 JPEG 或摄像头帧，调用视觉算法得到偏差，计算 vy/wz 和四轮速度，并直接写 AXI 电机寄存器。
- ps_line_follow_prototype.py：循迹算法核心。包含图像预处理、阈值分割、滑窗/目标点检测、滤波、丢线处理、控制量计算和障碍检测接口。
- line_follow_camera_debug.py：循迹图像调试工具。用于从板端摄像头取帧、保存调试图，帮助现场看阈值和 ROI 效果。
- run_line_follow_safe.sh：安全运行循迹脚本。以 root 运行 line_follow_drive.py，使用共享 JPEG，不限时运行，退出时强制 stop_car.py 停车。
- stop_car.py：安全停车脚本。直接写 AXI 寄存器让电机停止/保持，防止程序退出后轮子继续转。
- config/line_follow_fast_tuning.json：当前主用快速循迹参数，适配蓝白/跑道场景，包含速度、偏航、滤波、丢线搜索等参数。
- config/line_follow_safe_tuning.json：保守慢速测试参数，适合首次上地或室内安全测试。
- config/line_follow_tuning.json：通用基础参数，偏向原型/默认测试。

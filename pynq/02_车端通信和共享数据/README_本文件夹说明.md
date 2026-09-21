# 02 车端通信和共享数据

这个文件夹负责“小车 PS 端”和“网页中继服务器”之间的数据交换。

- car_net_client.py：小车端网络客户端。把状态和 JPEG 图像上传到中继服务器，接收网页控制命令，并通过 UDP 转发给运动控制程序。
- shared_runtime.py：共享运行目录封装。用 /dev/shm/car_runtime 保存 latest_status.json、latest_frame.jpg、latest_cmd.json，让摄像头、网络、运动程序之间交换数据。
- run_car_net_client.sh：启动 car_net_client.py 的脚本，指定服务器地址、token、共享目录、UDP 端口、视频按需上传和状态频率。

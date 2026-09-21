# 08 上电自启动加载

这个文件夹负责 PYNQ 重启后自动加载最终 bitstream，并验证电机 AXI IP 是否在线。

- load_hp1_bitstream.py：上电加载脚本。用 PYNQ Bitstream 或 /dev/xdevcfg 下载 hp1 bitstream，然后读取 0x40000000 的版本号和心跳寄存器验证电机 IP。
- pynq-load-hp1-bitstream.service：systemd 服务文件。开机后以 root 执行 load_hp1_bitstream.py，避免每次手动下载 bitstream。

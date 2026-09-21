#!/bin/bash
set -e
sudo sh -c 'mkdir -p /dev/shm/car_runtime && chmod 0777 /dev/shm/car_runtime && chmod 0666 /dev/shm/car_runtime/* 2>/dev/null || true' >/dev/null 2>&1 || true
cd /home/xilinx/car_relay_client
sudo -E /opt/python3.6/bin/python3.6 -u pynq_compressed_camera_writer.py   --bitstream /home/xilinx/jupyter_notebooks/ov5640_audio_mecanum_hp1.bit   --runtime /dev/shm/car_runtime   --engine-dir ./stream_server_pack   --width 640   --height 480   --quality 35   --scale 0.5   --fps 30   --status-hz 2

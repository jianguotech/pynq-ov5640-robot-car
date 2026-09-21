#!/bin/bash
set -e

cd /home/xilinx/car_relay_client

sudo -E /opt/python3.6/bin/python3.6 -u \
  motion_control/remote_motion_receiver_demo.py \
  --write-axi \
  --base 0x40000000 \
  --port 50009 \
  --max-linear 2.0 \
  --max-rotate 2.0 \
  --accel-linear 0.8 \
  --accel-rotate 1.0 \
  --min-period 10000 \
  --max-period 80000 \
  --line-follow-process-pattern '[l]ine_follow_drive.py'

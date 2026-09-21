#!/bin/bash
set -e
cd /home/xilinx/car_relay_client
exec /opt/python3.6/bin/python3.6 -u motion_control/remote_motion_receiver_demo.py \
  --dry-run \
  --port 50009 \
  --accel-linear 0.8 \
  --accel-rotate 1.0 \
  --line-follow-process-pattern '[l]ine_follow_drive.py'

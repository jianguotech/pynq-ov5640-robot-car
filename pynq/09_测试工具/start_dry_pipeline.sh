#!/bin/bash
set -e
cd /home/xilinx/car_relay_client
mkdir -p logs
./stop_pipeline.sh || true
nohup ./run_motion_receiver_dry.sh > logs/motion_dry.log 2>&1 &
nohup ./run_camera_compressed.sh > logs/camera.log 2>&1 &
nohup ./run_car_net_client.sh > logs/car_client.log 2>&1 &
echo "started dry pipeline"
echo "logs: /home/xilinx/car_relay_client/logs"

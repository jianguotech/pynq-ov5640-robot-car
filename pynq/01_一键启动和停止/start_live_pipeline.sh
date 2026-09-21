#!/bin/bash
set -e
cd /home/xilinx/car_relay_client
mkdir -p logs
./stop_pipeline.sh || true
sudo sh -c 'mkdir -p /dev/shm/car_runtime && chmod 0777 /dev/shm/car_runtime && chmod 0666 /dev/shm/car_runtime/* 2>/dev/null || true' >/dev/null 2>&1 || true
nohup ./run_camera_compressed.sh > logs/camera.log 2>&1 &
sleep 5
nohup ./run_motion_receiver_axi.sh > logs/motion_axi.log 2>&1 &
nohup ./run_car_net_client.sh > logs/car_client.log 2>&1 &
echo "started live pipeline"

# === 音频语音助手 (板载麦→阿里云ASR→DeepSeek→阿里云TTS→喇叭, 连公网中继) ===
# attach 模式: board_server 附着到摄像头已加载的 bit, 不重下、不冲掉摄像头。
sleep 3
nohup /home/xilinx/audio_assistant/run_audio_assistant.sh attach > /home/xilinx/audio_assistant/logs/launch.log 2>&1 &
echo "started audio assistant"

echo "logs: /home/xilinx/car_relay_client/logs"
echo "web: ${CAR_RELAY_WEB_URL:-http://127.0.0.1:8000/}"

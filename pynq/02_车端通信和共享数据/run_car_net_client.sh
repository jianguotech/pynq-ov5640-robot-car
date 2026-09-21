#!/bin/bash
set -e
sudo sh -c 'mkdir -p /dev/shm/car_runtime && chmod 0777 /dev/shm/car_runtime && chmod 0666 /dev/shm/car_runtime/* 2>/dev/null || true' >/dev/null 2>&1 || true
cd /home/xilinx/car_relay_client
relay_server="${CAR_RELAY_URL:-ws://127.0.0.1:8000}"
relay_token="${CAR_RELAY_TOKEN:-change_this_token}"
exec /opt/python3.6/bin/python3.6 -u car_net_client.py   --server "$relay_server"   --token "$relay_token"   --runtime /dev/shm/car_runtime   --motion-udp-host 127.0.0.1   --motion-udp-port 50009   --video-on-demand   --status-hz 2   --video-fps 30   --max-image-bytes 220000   --command-timeout 1.0

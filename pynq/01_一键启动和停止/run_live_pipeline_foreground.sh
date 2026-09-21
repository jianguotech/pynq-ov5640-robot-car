#!/bin/bash
set -euo pipefail

cd /home/xilinx/car_relay_client
mkdir -p logs

prepare_runtime() {
  sudo sh -c 'mkdir -p /dev/shm/car_runtime && chmod 0777 /dev/shm/car_runtime && chmod 0666 /dev/shm/car_runtime/* 2>/dev/null || true' >/dev/null 2>&1 || true
}

shutdown_pipeline() {
  set +e
  ./stop_pipeline.sh >/dev/null 2>&1 || true
}

trap shutdown_pipeline TERM INT

prepare_runtime
./stop_pipeline.sh >/dev/null 2>&1 || true
prepare_runtime

./run_camera_compressed.sh > logs/camera.log 2>&1 &
camera_pid=$!

sleep 8

./run_motion_receiver_axi.sh > logs/motion_axi.log 2>&1 &
motion_pid=$!

./run_car_net_client.sh > logs/car_client.log 2>&1 &
net_pid=$!

echo "car live pipeline started"
echo "camera_pid=${camera_pid} motion_pid=${motion_pid} net_pid=${net_pid}"

while true; do
  for item in "camera:${camera_pid}" "motion:${motion_pid}" "net:${net_pid}"; do
    name="${item%%:*}"
    pid="${item##*:}"
    if ! kill -0 "${pid}" >/dev/null 2>&1; then
      echo "child process exited: ${name} pid=${pid}" >&2
      shutdown_pipeline
      exit 1
    fi
  done
  sleep 3
done

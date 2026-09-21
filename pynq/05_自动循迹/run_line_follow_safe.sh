#!/bin/bash
set -u

cd /home/xilinx/line_follow || exit 1

PY=/opt/python3.6/bin/python3.6
CONFIG=/home/xilinx/line_follow/line_follow_fast_tuning.json

force_stop() {
  echo "RUN_WRAPPER_FORCE_STOP"
  "$PY" -B /home/xilinx/line_follow/stop_car.py || true
}

if [ "$(id -u)" != "0" ]; then
  exec sudo -E "$0" "$@"
fi

trap force_stop INT TERM EXIT

"$PY" -B /home/xilinx/line_follow/line_follow_drive.py \
  --drive \
  --camera-source shared-jpeg \
  --shared-frame /dev/shm/car_runtime/latest_frame.jpg \
  --shared-frame-timeout 3.0 \
  --duration 0 \
  --control-hz 10 \
  --config "$CONFIG" \
  --reload-config

status=$?
force_stop
trap - EXIT
exit "$status"

#!/bin/bash
set +e

cd /home/xilinx/car_relay_client 2>/dev/null || true

# Tell the motion receiver to hold before processes are torn down.
/opt/python3.6/bin/python3.6 - <<'PY' >/dev/null 2>&1 || true
import json
import socket
import time

packet = {
    "type": "remote_cmd",
    "seq": 0,
    "manual_enable": 1,
    "action": "STOP",
    "vx": 0.0,
    "vy": 0.0,
    "wz": 0.0,
    "speed": 0.0,
    "estop": 0,
    "source": "stop_pipeline",
}
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = json.dumps(packet, separators=(",", ":")).encode("utf-8")
for _ in range(3):
    sock.sendto(payload, ("127.0.0.1", 50009))
    time.sleep(0.05)
sock.close()
PY

# car_net_client handles SIGTERM and writes a final STOP.
pkill -TERM -u xilinx -f '[c]ar_net_client.py' >/dev/null 2>&1 || true
pkill -TERM -u xilinx -f '[r]un_car_net_client.sh' >/dev/null 2>&1 || true

# The PYNQ 2.x Xlnk/OpenCV cleanup path can print "double free" on exit.
# Kill the camera writer hard and suppress the expected runtime noise.
sudo pkill -KILL -f '[p]ynq_compressed_camera_writer.py' >/dev/null 2>&1 || true
pkill -KILL -u xilinx -f '[r]un_camera_compressed.sh' >/dev/null 2>&1 || true

pkill -TERM -u xilinx -f '[r]emote_motion_receiver_demo.py' >/dev/null 2>&1 || true
sudo pkill -TERM -f '[r]emote_motion_receiver_demo.py' >/dev/null 2>&1 || true
pkill -TERM -u xilinx -f '[r]un_motion_receiver' >/dev/null 2>&1 || true

sleep 0.5

pkill -KILL -u xilinx -f '[c]ar_net_client.py' >/dev/null 2>&1 || true
sudo pkill -KILL -f '[r]emote_motion_receiver_demo.py' >/dev/null 2>&1 || true
pkill -KILL -u xilinx -f '[r]un_motion_receiver' >/dev/null 2>&1 || true

# === 停音频语音助手 + board_server ===
pkill -KILL -u xilinx -f '[c]ar_audio_assistant.py' >/dev/null 2>&1 || true
sudo pkill -KILL -f '[b]oard_server.py' >/dev/null 2>&1 || true

exit 0

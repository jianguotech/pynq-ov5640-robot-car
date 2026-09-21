#!/usr/bin/env python3
"""
远程遥控发送端示例。

这个脚本给负责遥控的同学参考：真实遥控端可以是网页、手柄、
手机 App 或电脑程序，只要最终持续给出 vx/vy/wz、speed、estop
这些数据即可。JSON 只是网络传输时的一种包装方式，不是必须概念。
"""

import argparse
import json
import socket
import time


ACTION_TO_VECTOR = {
    "FORWARD": (0.0, 1.0, 0.0),
    "BACKWARD": (0.0, -1.0, 0.0),
    "LEFT": (-1.0, 0.0, 0.0),
    "RIGHT": (1.0, 0.0, 0.0),
    "ROTATE_LEFT": (0.0, 0.0, 1.0),
    "ROTATE_RIGHT": (0.0, 0.0, -1.0),
    "STOP": (0.0, 0.0, 0.0),
    "ESTOP": (0.0, 0.0, 0.0),
}


def clamp(value, low, high):
    return max(low, min(high, value))


def build_packet(seq, action, speed, manual_enable):
    action = action.upper()
    vx, vy, wz = ACTION_TO_VECTOR[action]
    safe_speed = clamp(speed, 0.0, 1.0)
    return {
        "type": "remote_cmd",
        "seq": seq,
        "manual_enable": 1 if manual_enable else 0,
        "action": action,
        "vx": vx,
        "vy": vy,
        "wz": wz,
        "speed": safe_speed,
        "estop": 1 if action == "ESTOP" else 0,
        "timestamp_ms": int(time.time() * 1000),
    }


def main():
    parser = argparse.ArgumentParser(description="Send remote-control packets to PYNQ.")
    parser.add_argument("--host", required=True, help="PYNQ IP address, for example 172.26.3.24")
    parser.add_argument("--port", type=int, default=50009, help="UDP port, default 50009")
    parser.add_argument("--action", default="FORWARD", choices=sorted(ACTION_TO_VECTOR.keys()))
    parser.add_argument("--speed", type=float, default=0.3, help="0.0 ~ 1.0")
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--manual-enable", type=int, default=1)
    args = parser.parse_args()

    interval = 1.0 / max(args.rate_hz, 1.0)
    deadline = time.time() + max(args.seconds, 0.0)
    seq = 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)

    print(f"REMOTE_SEND_BEGIN target={target} action={args.action} speed={args.speed}")
    while time.time() < deadline:
        packet = build_packet(seq, args.action, args.speed, args.manual_enable)
        payload = json.dumps(packet, separators=(",", ":")).encode("utf-8")
        sock.sendto(payload, target)
        print(payload.decode("utf-8"))
        seq += 1
        time.sleep(interval)

    # 主动补发 STOP，避免遥控端退出后还要等接收端超时。
    for _ in range(5):
        packet = build_packet(seq, "STOP", 0.0, args.manual_enable)
        sock.sendto(json.dumps(packet, separators=(",", ":")).encode("utf-8"), target)
        seq += 1
        time.sleep(interval)

    print("REMOTE_SEND_END")


if __name__ == "__main__":
    main()

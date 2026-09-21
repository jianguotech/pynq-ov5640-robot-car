"""
sim_car_memory_writer.py

电脑模拟小车用。

它不连接服务器，只负责模拟真实小车内部其他模块：
1. 持续写 latest_status.json；
2. 持续写 latest_frame.jpg；
3. 持续读取 latest_cmd.json，并把收到的控制命令打印出来。

配合 car_net_client.py 使用：

终端1：
    python quick_relay_server.py

终端2：
    python sim_car_memory_writer.py --runtime ./runtime

终端3：
    python car_net_client.py --server ws://127.0.0.1:8000 --runtime ./runtime

这样可以模拟：
    假小车数据 → car_net_client → relay_server → 网页端
    网页端控制 → relay_server → car_net_client → latest_cmd.json → sim_car_memory_writer打印
"""

import argparse
import math
import time
from typing import Optional

from shared_runtime import RuntimeStore


def describe_cmd(cmd: dict) -> str:
    """Return a compact text description for legacy cmd and new remote_cmd."""
    msg_type = cmd.get("type")
    if msg_type == "remote_cmd":
        action = cmd.get("action", "VECTOR")
        if cmd.get("estop"):
            action = "ESTOP"
        return (
            f"{action} manual={cmd.get('manual_enable')} "
            f"vx={cmd.get('vx')} vy={cmd.get('vy')} wz={cmd.get('wz')} "
            f"speed={cmd.get('speed')}"
        )

    return f"{cmd.get('cmd')} vx={cmd.get('vx')} vy={cmd.get('vy')} wz={cmd.get('wz')}"


def cmd_is_moving(cmd: dict) -> bool:
    if cmd.get("type") == "remote_cmd":
        if cmd.get("estop"):
            return False
        if str(cmd.get("action", "")).upper() in {"STOP", "ESTOP"}:
            return False
        return max(
            abs(float(cmd.get("vx", 0) or 0)),
            abs(float(cmd.get("vy", 0) or 0)),
            abs(float(cmd.get("wz", 0) or 0)),
        ) > 0.01 and float(cmd.get("speed", 0) or 0) > 0.0

    return cmd.get("cmd") == "move"


def try_import_cv2_numpy():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except Exception as e:
        raise RuntimeError(
            "sim_car_memory_writer.py 需要 opencv-python 和 numpy。\n"
            "请先运行：pip install opencv-python numpy\n"
            f"原始错误：{e}"
        )


def make_fake_frame(cv2, np, width: int, height: int, t: float, last_cmd_text: str) -> bytes:
    """生成一张模拟摄像头画面，并编码成 JPEG。"""
    img = np.zeros((height, width, 3), dtype=np.uint8)

    # 背景渐变
    for y in range(height):
        value = int(40 + 40 * y / max(height - 1, 1))
        img[y, :, :] = (value, value, value)

    # 模拟跑道线：随时间左右摆动
    center_x = width // 2 + int(math.sin(t * 1.5) * width * 0.2)
    line_width = max(12, width // 30)

    cv2.line(
        img,
        (center_x, height),
        (center_x + int(math.sin(t) * 80), 0),
        (255, 255, 255),
        line_width,
    )

    # 画一些调试文字
    cv2.putText(img, "SIMULATED CAR CAMERA", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.putText(img, time.strftime("%H:%M:%S"), (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(img, f"last cmd: {last_cmd_text[:40]}", (20, height - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 0), 2)

    ok, jpg = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return jpg.tobytes()


def read_camera_frame(cv2, cap, width: int, height: int) -> Optional[bytes]:
    """从真实摄像头读取一帧，编码成 JPEG。"""
    ret, frame = cap.read()
    if not ret:
        return None

    frame = cv2.resize(frame, (width, height))
    ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    if not ok:
        return None

    return jpg.tobytes()


def main():
    parser = argparse.ArgumentParser(description="模拟小车内部状态和图像写入程序")
    parser.add_argument("--runtime", default=None, help="共享内存目录；Windows建议 ./runtime")
    parser.add_argument("--fps", type=float, default=8.0, help="模拟图像帧率")
    parser.add_argument("--status-hz", type=float, default=2.0, help="状态写入频率")
    parser.add_argument("--width", type=int, default=320, help="图像宽度")
    parser.add_argument("--height", type=int, default=240, help="图像高度")
    parser.add_argument("--camera", type=int, default=-1, help="使用真实摄像头编号，例如 0；默认 -1 表示生成假图")
    args = parser.parse_args()

    cv2, np = try_import_cv2_numpy()
    store = RuntimeStore(args.runtime)
    store.ensure_initial_files()

    print("[runtime]", store.root)
    print("[status ]", store.status_path)
    print("[frame  ]", store.frame_path)
    print("[cmd    ]", store.cmd_path)

    cap = None
    if args.camera >= 0:
        cap = cv2.VideoCapture(args.camera)
        if not cap.isOpened():
            raise RuntimeError(f"无法打开摄像头：{args.camera}")
        print(f"[camera] using camera {args.camera}")
    else:
        print("[camera] using generated fake image")

    frame_interval = 1.0 / max(args.fps, 0.1)
    status_interval = 1.0 / max(args.status_hz, 0.1)

    last_frame_time = 0.0
    last_status_time = 0.0
    last_cmd_mtime = 0
    last_cmd_text = "none"
    last_cmd_moving = False
    start_time = time.time()

    try:
        while True:
            t = time.time()
            elapsed = t - start_time

            # 读取控制命令，模拟电机控制模块看到命令
            try:
                cmd_mtime = store.cmd_path.stat().st_mtime_ns
            except FileNotFoundError:
                cmd_mtime = 0

            if cmd_mtime != 0 and cmd_mtime != last_cmd_mtime:
                cmd = store.read_cmd()
                if cmd:
                    last_cmd_mtime = cmd_mtime
                    last_cmd_text = describe_cmd(cmd)
                    last_cmd_moving = cmd_is_moving(cmd)
                    print("[sim motor] read cmd:", cmd)

            # 写状态
            if t - last_status_time >= status_interval:
                fake_battery = 12.2 - 0.05 * math.sin(elapsed / 20.0)
                fake_speed = 0.3 if last_cmd_moving else 0.0

                store.write_status({
                    "type": "status",
                    "mode": "remote",
                    "online": True,
                    "battery": round(fake_battery, 2),
                    "speed": round(fake_speed, 2),
                    "camera_fps": args.fps,
                    "sim_elapsed": round(elapsed, 1),
                    "last_cmd": last_cmd_text,
                })

                last_status_time = t

            # 写图像
            if t - last_frame_time >= frame_interval:
                if cap is not None:
                    jpg = read_camera_frame(cv2, cap, args.width, args.height)
                else:
                    jpg = make_fake_frame(cv2, np, args.width, args.height, elapsed, last_cmd_text)

                if jpg:
                    store.write_frame_jpg(jpg)

                last_frame_time = t

            time.sleep(0.005)

    except KeyboardInterrupt:
        print("KeyboardInterrupt")
    finally:
        if cap is not None:
            cap.release()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
pynq_compressed_camera_writer.py

Bridge the teammate-provided stream_server_pack/CameraEngine into the relay
runtime used by car_net_client.py.

CameraEngine handles the expensive part:
  VDMA frame buffer -> JPEG compression

This script handles the handoff:
  compressed JPEG -> /dev/shm/car_runtime/latest_frame.jpg
  camera stats     -> /dev/shm/car_runtime/latest_status.json

Run on PYNQ. It does not start an HTTP/WebSocket camera server; the public relay
path is still handled by car_net_client.py, so video remains on-demand.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from shared_runtime import RuntimeStore


def maybe_load_bitstream(bitstream_path: Optional[str]) -> None:
    if not bitstream_path:
        return

    from pynq import Bitstream

    path = Path(bitstream_path)
    if not path.exists():
        raise FileNotFoundError(f"bitstream not found: {path}")

    print(f"[bitstream] loading {path}")
    Bitstream(str(path)).download()
    time.sleep(0.3)
    print("[bitstream] loaded")


def import_camera_engine(engine_dir: str):
    engine_path = Path(engine_dir).resolve()
    if not engine_path.exists():
        raise FileNotFoundError(f"engine dir not found: {engine_path}")

    sys.path.insert(0, str(engine_path))
    from camera_engine import CameraEngine

    return CameraEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Write compressed CameraEngine frames into latest_frame.jpg.")
    parser.add_argument("--runtime", default=None, help="共享内存目录；PYNQ 默认 /dev/shm/car_runtime")
    parser.add_argument("--engine-dir", default="./stream_server_pack", help="包含 camera_engine.py 的目录")
    parser.add_argument("--bitstream", default="", help="可选：启动时加载的 bitstream 路径；留空不加载")
    parser.add_argument("--width", type=int, default=640, help="VDMA 输入宽度")
    parser.add_argument("--height", type=int, default=480, help="VDMA 输入高度")
    parser.add_argument("--quality", type=int, default=35, help="JPEG 质量，建议 25~45")
    parser.add_argument("--scale", type=float, default=0.5, help="输出缩放，1.0/0.5/0.25")
    parser.add_argument("--fps", type=float, default=30.0, help="写入 runtime 的最高帧率")
    parser.add_argument("--status-hz", type=float, default=1.0, help="状态写入频率")
    args = parser.parse_args()

    maybe_load_bitstream(args.bitstream or None)
    CameraEngine = import_camera_engine(args.engine_dir)

    store = RuntimeStore(args.runtime)
    store.ensure_initial_files()

    cam = CameraEngine(width=args.width, height=args.height, jpeg_quality=args.quality)
    cam.init()
    cam.set_scale(args.scale)

    frame_interval = 1.0 / max(args.fps, 0.1)
    status_interval = 1.0 / max(args.status_hz, 0.1)
    last_frame_write = 0.0
    last_status_write = 0.0
    last_seq = 0
    written = 0
    start = time.time()

    print("[runtime]", store.root)
    print("[frame  ]", store.frame_path)
    print("[status ]", store.status_path)
    print(
        "[camera ]",
        f"{args.width}x{args.height}",
        f"scale={args.scale}",
        f"quality={args.quality}",
        f"fps_limit={args.fps}",
    )

    try:
        while True:
            now = time.time()
            data, seq = cam.get_frame(last_seq, timeout=0.1)
            if data is not None:
                last_seq = seq
                if now - last_frame_write >= frame_interval:
                    store.write_frame_jpg(data)
                    written += 1
                    last_frame_write = now

            if now - last_status_write >= status_interval:
                stats = cam.get_stats()  # type: Dict[str, Any]
                store.write_status({
                    "type": "status",
                    "mode": "remote",
                    "online": True,
                    "camera_source": "camera_engine_jpeg",
                    "camera_fps": stats.get("fps"),
                    "camera_encode_ms": round(float(stats.get("encode_ms", 0.0)), 2),
                    "camera_frames": stats.get("frames"),
                    "camera_drops": stats.get("drops"),
                    "camera_park_fallbacks": stats.get("park_fallbacks"),
                    "relay_written_frames": written,
                    "relay_elapsed": round(now - start, 1),
                    "jpeg_quality": cam.jpeg_quality,
                    "jpeg_scale": cam.scale,
                })
                last_status_write = now

    except KeyboardInterrupt:
        print("KeyboardInterrupt")
    finally:
        cam.stop()


if __name__ == "__main__":
    main()

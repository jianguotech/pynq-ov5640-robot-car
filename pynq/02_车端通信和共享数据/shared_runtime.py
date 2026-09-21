"""
shared_runtime.py

用于在不同程序之间交换“小车状态、图像帧、控制命令”。

默认使用 /dev/shm/car_runtime：
- /dev/shm 是 Linux 的内存文件系统，读写速度快，重启后自动清空；
- PYNQ/Linux 上适合做课程设计中的“共享内存位置”；
- Windows 上自动退回到当前目录 runtime/。

文件约定：
- latest_status.json：状态信息，其他程序写，小车通信程序读
- latest_frame.jpg：JPEG图像，其他程序写，小车通信程序读
- latest_cmd.json：控制命令，小车通信程序写，运动控制程序读
- cmd_history.jsonl：命令历史，调试用
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional, Union


def default_runtime_dir() -> Path:
    """返回默认运行时目录。Linux 优先使用 /dev/shm，Windows 使用 ./runtime。"""
    shm = Path("/dev/shm")
    if shm.exists() and os.access(shm, os.W_OK):
        return shm / "car_runtime"
    return Path.cwd() / "runtime"


class RuntimeStore:
    def __init__(self, runtime_dir: Optional[Union[str, Path]] = None):
        self.root = Path(runtime_dir) if runtime_dir else default_runtime_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._relax_runtime_permissions()

        self.status_path = self.root / "latest_status.json"
        self.frame_path = self.root / "latest_frame.jpg"
        self.cmd_path = self.root / "latest_cmd.json"
        self.cmd_history_path = self.root / "cmd_history.jsonl"

    def _relax_runtime_permissions(self) -> None:
        """Allow sudo camera writer and normal-user network client to share files."""
        try:
            os.chmod(str(self.root), 0o777)
        except OSError:
            pass

    def _relax_file_permission(self, path: Path) -> None:
        try:
            os.chmod(str(path), 0o666)
        except OSError:
            pass

    def _atomic_write_bytes(self, path: Path, data: bytes) -> None:
        """原子写文件，避免读到半截文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(path.parent), 0o777)
        except OSError:
            pass
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_name, 0o666)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass

    def write_json(self, path: Path, obj: Dict[str, Any]) -> None:
        obj = dict(obj)
        obj.setdefault("local_time", time.time())
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._atomic_write_bytes(path, data)

    def read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            return None
        except OSError:
            return None

    def write_status(self, status: Dict[str, Any]) -> None:
        self.write_json(self.status_path, status)

    def read_status(self) -> Optional[Dict[str, Any]]:
        return self.read_json(self.status_path)

    def write_frame_jpg(self, jpg_bytes: bytes) -> None:
        self._atomic_write_bytes(self.frame_path, jpg_bytes)

    def read_frame_jpg(self) -> Optional[bytes]:
        try:
            return self.frame_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def frame_mtime_ns(self) -> int:
        try:
            return self.frame_path.stat().st_mtime_ns
        except FileNotFoundError:
            return 0
        except OSError:
            return 0

    def status_mtime_ns(self) -> int:
        try:
            return self.status_path.stat().st_mtime_ns
        except FileNotFoundError:
            return 0
        except OSError:
            return 0

    def write_cmd(self, cmd: Dict[str, Any]) -> None:
        """写入最新控制命令，同时追加到历史日志。"""
        cmd = dict(cmd)
        cmd.setdefault("received_at", time.time())
        self.write_json(self.cmd_path, cmd)

        try:
            with self.cmd_history_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(cmd, ensure_ascii=False) + "\n")
            self._relax_file_permission(self.cmd_history_path)
        except OSError:
            pass

    def read_cmd(self) -> Optional[Dict[str, Any]]:
        return self.read_json(self.cmd_path)

    def ensure_initial_files(self) -> None:
        """没有文件时写入初始值，方便第一次运行。"""
        if not self.status_path.exists():
            self.write_status({
                "type": "status",
                "mode": "boot",
                "online": True,
                "battery": None,
                "speed": 0.0,
                "camera_fps": 0
            })

        if not self.cmd_path.exists():
            self.write_cmd({
                "type": "cmd",
                "cmd": "stop",
                "reason": "initial"
            })

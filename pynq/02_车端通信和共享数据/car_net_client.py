"""
car_net_client.py

真实小车端网络客户端。

功能：
1. 从共享内存文件读取 latest_status.json，发送到服务器；
2. 从共享内存文件读取 latest_frame.jpg，发送到服务器；
3. 从服务器接收控制命令，规范化为 remote_cmd，写入 latest_cmd.json；
4. 可选地把 remote_cmd 通过 UDP 转发给运动控制接收端，默认 127.0.0.1:50009；
5. 根据服务器 video_demand 事件按需上传视频，避免无人观看时持续消耗流量；
6. 断线/长时间无控制命令时写入 stop 命令，保证小车安全停车。

对应服务器接口：
- 控制/状态通道：ws://server/car/control?token=...
- 图像通道：    ws://server/car/video?token=...

适合运行在 PYNQ-Z1 的 PS 端 Linux 中。
"""

import argparse
import asyncio
import json
import signal
import socket
import time
from typing import Any, Dict, Optional

try:
    import websockets
except ImportError:
    websockets = None

from shared_runtime import RuntimeStore


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

STOP_ACTIONS = {
    "STOP",
    "HOLD",
    "STOP_AUTO",
    "STOP_LINE_FOLLOW",
}

CLEAR_ESTOP_ACTIONS = {
    "CLEAR_ESTOP",
    "RESET_ESTOP",
    "RELEASE_ESTOP",
}

AUTO_ACTIONS = {
    "START_LINE_FOLLOW",
    "LINE_FOLLOW",
    "AUTO_LINE_FOLLOW",
    "FOLLOW_LINE",
    "START_AUTO",
    "AUTO",
}


def now() -> float:
    return time.time()


def now_ms() -> int:
    return int(time.time() * 1000)


def create_task(coro):
    return asyncio.ensure_future(coro)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
    return default


def _base_remote_packet(raw: Dict[str, Any], seq: int) -> Dict[str, Any]:
    packet = dict(raw)
    packet["type"] = "remote_cmd"
    packet["seq"] = as_int(packet.get("seq"), seq)
    packet["manual_enable"] = 1 if truthy(packet.get("manual_enable"), True) else 0
    packet["speed"] = clamp(as_float(packet.get("speed"), 1.0), 0.0, 1.0)
    packet["estop"] = 1 if truthy(packet.get("estop"), False) else 0
    packet.setdefault("timestamp_ms", now_ms())
    return packet


def normalize_remote_command(raw: Dict[str, Any], seq: int) -> Dict[str, Any]:
    """Normalize web commands into the movement teammate's remote_cmd protocol.

    The current motion-control interface expects:
      vx: left/right, positive right
      vy: forward/back, positive forward
      wz: rotation, positive left

    Older test pages used cmd/move with vx=forward, vy=right, wz=right-rotation.
    Those legacy messages are still accepted and remapped here.
    """
    msg_type = str(raw.get("type", "")).lower()

    if msg_type == "remote_cmd":
        packet = _base_remote_packet(raw, seq)
        action = str(packet.get("action", "")).upper()
        if action in CLEAR_ESTOP_ACTIONS:
            packet["action"] = action
            packet["manual_enable"] = 1
            packet["speed"] = 0.0
            packet["estop"] = 0
            vx, vy, wz = 0.0, 0.0, 0.0
        elif action in AUTO_ACTIONS:
            packet["action"] = action
            packet["manual_enable"] = 0
            packet["speed"] = 0.0
            packet["estop"] = 0
            vx, vy, wz = 0.0, 0.0, 0.0
        elif action in STOP_ACTIONS:
            packet["action"] = action
            packet["manual_enable"] = 1
            packet["speed"] = 0.0
            packet["estop"] = 0
            vx, vy, wz = 0.0, 0.0, 0.0
        elif action in ACTION_TO_VECTOR:
            vx, vy, wz = ACTION_TO_VECTOR[action]
            packet["action"] = action
            if action in {"STOP", "ESTOP"}:
                packet["speed"] = 0.0
            if action == "ESTOP":
                packet["estop"] = 1
        else:
            vx = clamp(as_float(packet.get("vx"), 0.0), -1.0, 1.0)
            vy = clamp(as_float(packet.get("vy"), 0.0), -1.0, 1.0)
            wz = clamp(as_float(packet.get("wz"), 0.0), -1.0, 1.0)
            packet.pop("action", None)

        packet["vx"] = vx
        packet["vy"] = vy
        packet["wz"] = wz
        return packet

    if msg_type != "cmd":
        raise ValueError(f"unsupported command type: {raw.get('type')!r}")

    cmd_name = str(raw.get("cmd", "")).lower()
    action = str(raw.get("action", "")).upper()

    if cmd_name in {"stop", "hold"}:
        action = "STOP"
    elif cmd_name in {"estop", "emergency_stop"}:
        action = "ESTOP"

    if action in CLEAR_ESTOP_ACTIONS:
        return {
            "type": "remote_cmd",
            "seq": as_int(raw.get("seq"), seq),
            "manual_enable": 1,
            "action": action,
            "vx": 0.0,
            "vy": 0.0,
            "wz": 0.0,
            "speed": 0.0,
            "estop": 0,
            "timestamp_ms": as_int(raw.get("timestamp_ms"), now_ms()),
            "legacy_cmd": raw,
        }

    if action in AUTO_ACTIONS:
        return {
            "type": "remote_cmd",
            "seq": as_int(raw.get("seq"), seq),
            "manual_enable": 0,
            "action": action,
            "vx": 0.0,
            "vy": 0.0,
            "wz": 0.0,
            "speed": 0.0,
            "estop": 0,
            "timestamp_ms": as_int(raw.get("timestamp_ms"), now_ms()),
            "legacy_cmd": raw,
        }

    if action in STOP_ACTIONS:
        return {
            "type": "remote_cmd",
            "seq": as_int(raw.get("seq"), seq),
            "manual_enable": 1,
            "action": action,
            "vx": 0.0,
            "vy": 0.0,
            "wz": 0.0,
            "speed": 0.0,
            "estop": 0,
            "timestamp_ms": as_int(raw.get("timestamp_ms"), now_ms()),
            "legacy_cmd": raw,
        }

    if action in ACTION_TO_VECTOR:
        vx, vy, wz = ACTION_TO_VECTOR[action]
        speed = 0.0 if action in {"STOP", "ESTOP"} else clamp(as_float(raw.get("speed"), 0.3), 0.0, 1.0)
        return {
            "type": "remote_cmd",
            "seq": as_int(raw.get("seq"), seq),
            "manual_enable": 1 if truthy(raw.get("manual_enable"), True) else 0,
            "action": action,
            "vx": vx,
            "vy": vy,
            "wz": wz,
            "speed": speed,
            "estop": 1 if action == "ESTOP" or truthy(raw.get("estop"), False) else 0,
            "timestamp_ms": as_int(raw.get("timestamp_ms"), now_ms()),
            "legacy_cmd": raw,
        }

    if cmd_name != "move":
        raise ValueError(f"unsupported legacy cmd: {raw.get('cmd')!r}")

    # Legacy convention: vx=forward, vy=right, wz=right-rotation.
    old_forward = clamp(as_float(raw.get("vx"), 0.0), -1.0, 1.0)
    old_right = clamp(as_float(raw.get("vy"), 0.0), -1.0, 1.0)
    old_turn_right = clamp(as_float(raw.get("wz"), 0.0), -1.0, 1.0)

    remote_vx = old_right
    remote_vy = old_forward
    remote_wz = -old_turn_right

    magnitude = max(abs(remote_vx), abs(remote_vy), abs(remote_wz))
    speed = clamp(as_float(raw.get("speed"), magnitude), 0.0, 1.0)

    if magnitude > 0.0:
        remote_vx /= magnitude
        remote_vy /= magnitude
        remote_wz /= magnitude

    return {
        "type": "remote_cmd",
        "seq": as_int(raw.get("seq"), seq),
        "manual_enable": 1 if truthy(raw.get("manual_enable"), True) else 0,
        "vx": remote_vx,
        "vy": remote_vy,
        "wz": remote_wz,
        "speed": speed,
        "estop": 1 if truthy(raw.get("estop"), False) else 0,
        "timestamp_ms": as_int(raw.get("timestamp_ms"), now_ms()),
        "legacy_cmd": raw,
    }


class CarNetworkClient:
    def __init__(
        self,
        server: str,
        token: str,
        runtime_dir: Optional[str],
        status_hz: float,
        video_fps: float,
        reconnect_delay: float,
        command_timeout: float,
        max_image_bytes: int,
        motion_udp_host: str,
        motion_udp_port: int,
        video_on_demand: bool,
    ):
        self.server = server.rstrip("/")
        self.token = token
        self.store = RuntimeStore(runtime_dir)
        self.status_interval = 1.0 / max(status_hz, 0.1)
        self.video_interval = 1.0 / max(video_fps, 0.1)
        self.reconnect_delay = reconnect_delay
        self.command_timeout = command_timeout
        self.max_image_bytes = max_image_bytes
        self.video_on_demand = video_on_demand
        self.video_enabled = not video_on_demand

        self.last_cmd_time = 0.0
        self.last_safety_stop_time = 0.0
        self.control_owner = "idle"
        self.seq = 0
        self.stop_event = asyncio.Event()
        self.motion_udp_target = None
        self.motion_udp_sock = None  # type: Optional[socket.socket]
        if motion_udp_host:
            self.motion_udp_target = (motion_udp_host, motion_udp_port)
            self.motion_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def control_url(self) -> str:
        return f"{self.server}/car/control?token={self.token}"

    def video_url(self) -> str:
        return f"{self.server}/car/video?token={self.token}"

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def forward_motion_udp(self, packet: Dict[str, Any]) -> None:
        if self.motion_udp_sock is None or self.motion_udp_target is None:
            return
        try:
            payload = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.motion_udp_sock.sendto(payload, self.motion_udp_target)
        except OSError as e:
            print(f"[motion-udp] send failed: {repr(e)}")

    def publish_command(self, raw_cmd: Dict[str, Any]) -> Dict[str, Any]:
        packet = normalize_remote_command(raw_cmd, self.next_seq())
        packet.setdefault("source", raw_cmd.get("source", "web"))
        packet["received_at"] = now()
        self.store.write_cmd(packet)
        self.forward_motion_udp(packet)
        self.update_control_owner(packet)
        return packet

    def update_control_owner(self, packet: Dict[str, Any]) -> None:
        action = str(packet.get("action", "")).upper()
        manual_enable = truthy(packet.get("manual_enable"), False)
        estop = truthy(packet.get("estop"), False)

        if estop:
            self.control_owner = "estop"
        elif action in CLEAR_ESTOP_ACTIONS:
            self.control_owner = "idle"
        elif self.control_owner == "estop":
            # Emergency stop is latched until an explicit CLEAR_ESTOP action.
            return
        elif action in AUTO_ACTIONS:
            self.control_owner = "line_follow"
        elif action in STOP_ACTIONS:
            self.control_owner = "idle"
        elif manual_enable:
            self.control_owner = "remote"
        else:
            self.control_owner = "idle"

    def handle_server_event(self, event: Dict[str, Any]) -> None:
        name = str(event.get("event", ""))
        if name == "video_demand":
            self.video_enabled = truthy(event.get("enabled"), False)
            print(
                "[server-event] video_demand",
                f"enabled={self.video_enabled}",
                f"web_video_clients={event.get('web_video_clients')}",
            )
        elif name == "control_demand":
            print(
                "[server-event] control_demand",
                f"enabled={truthy(event.get('enabled'), False)}",
                f"web_control_clients={event.get('web_control_clients')}",
            )
        else:
            print("[server-event]", event)

    def write_safety_stop(self, reason: str) -> None:
        """向共享命令区写入安全停车命令。"""
        t = now()
        # 避免每毫秒刷屏写 stop
        if t - self.last_safety_stop_time < 0.5:
            return

        self.last_safety_stop_time = t
        self.publish_command({
            "type": "remote_cmd",
            "action": "STOP",
            "manual_enable": 1,
            "speed": 0.0,
            "estop": 0,
            "source": "car_net_client",
            "reason": reason,
        })

    async def control_loop(self) -> None:
        """控制/状态通道：收命令，发状态。"""
        if websockets is None:
            raise RuntimeError("缺少 websockets 依赖，请先运行：pip install websockets")

        while not self.stop_event.is_set():
            try:
                print(f"[control] connecting: {self.control_url()}")
                async with websockets.connect(
                    self.control_url(),
                    max_size=2 * 1024 * 1024,
                ) as ws:
                    print("[control] connected")
                    self.last_cmd_time = now()

                    sender_task = create_task(self.status_sender(ws))
                    receiver_task = create_task(self.command_receiver(ws))
                    watchdog_task = create_task(self.command_watchdog())

                    done, pending = await asyncio.wait(
                        {sender_task, receiver_task, watchdog_task},
                        return_when=asyncio.FIRST_EXCEPTION,
                    )

                    for task in pending:
                        task.cancel()

                    for task in done:
                        exc = task.exception()
                        if exc:
                            raise exc

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[control] disconnected/error: {repr(e)}")
                if self.video_on_demand:
                    self.video_enabled = False
                if self.control_owner == "remote":
                    self.write_safety_stop("control_channel_disconnected")
                await asyncio.sleep(self.reconnect_delay)

    async def status_sender(self, ws) -> None:
        """定时读取 latest_status.json 并发送。"""
        while True:
            status = self.store.read_status()
            if status is None:
                status = {
                    "type": "status",
                    "mode": "unknown",
                    "online": True,
                    "warning": "latest_status.json not found"
                }

            status = dict(status)
            status["type"] = "status"
            status["car_net_time"] = now()
            status["control_owner"] = self.control_owner
            status["control_owner_priority"] = {
                "estop": 4,
                "remote": 3,
                "line_follow": 2,
                "idle": 1,
            }.get(self.control_owner, 0)
            status["last_cmd_age_ms"] = int(max(0.0, now() - self.last_cmd_time) * 1000)
            status["video_enabled"] = bool(self.video_enabled)

            await ws.send(json.dumps(status, ensure_ascii=False))
            await asyncio.sleep(self.status_interval)

    async def command_receiver(self, ws) -> None:
        """接收网页端/服务器发来的控制命令，并写入 latest_cmd.json。"""
        async for msg in ws:
            try:
                cmd = json.loads(msg)
            except json.JSONDecodeError:
                print("[control] invalid json command:", msg[:100])
                continue

            if cmd.get("type") == "server_event":
                self.handle_server_event(cmd)
                continue

            if cmd.get("type") != "cmd":
                if cmd.get("type") != "remote_cmd":
                    # 允许服务器或网页端发一些非控制消息，不直接报错
                    print("[control] non-command message:", cmd)
                    continue

            self.last_cmd_time = now()

            try:
                packet = self.publish_command(cmd)
            except ValueError as e:
                print(f"[control] unsupported command: {e}; raw={cmd}")
                continue

            print("[control] command received:", packet)

    async def command_watchdog(self) -> None:
        """超过 command_timeout 没有收到控制命令，则写入 stop。"""
        while True:
            await asyncio.sleep(0.1)
            if self.command_timeout <= 0:
                continue

            if self.control_owner == "remote" and now() - self.last_cmd_time > self.command_timeout:
                self.write_safety_stop("command_timeout")

    async def video_loop(self) -> None:
        """图像通道：读取 latest_frame.jpg 并发送。"""
        if websockets is None:
            raise RuntimeError("缺少 websockets 依赖，请先运行：pip install websockets")

        last_mtime = 0

        while not self.stop_event.is_set():
            try:
                print(f"[video] connecting: {self.video_url()}")
                async with websockets.connect(
                    self.video_url(),
                    max_size=None,
                ) as ws:
                    print("[video] connected")

                    while True:
                        if self.video_on_demand and not self.video_enabled:
                            await asyncio.sleep(0.25)
                            continue

                        mtime = self.store.frame_mtime_ns()

                        # 文件没更新就不重复发，避免浪费带宽
                        if mtime != 0 and mtime != last_mtime:
                            jpg = self.store.read_frame_jpg()
                            if jpg:
                                if len(jpg) <= self.max_image_bytes:
                                    await ws.send(jpg)
                                    last_mtime = mtime
                                else:
                                    print(
                                        f"[video] frame too large: {len(jpg)} bytes, "
                                        f"limit={self.max_image_bytes}"
                                    )

                        await asyncio.sleep(self.video_interval)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[video] disconnected/error: {repr(e)}")
                await asyncio.sleep(self.reconnect_delay)

    async def run(self) -> None:
        if websockets is None:
            raise RuntimeError("缺少 websockets 依赖，请先运行：pip install websockets")

        self.store.ensure_initial_files()
        print("[runtime]", self.store.root)
        print("[status ]", self.store.status_path)
        print("[frame  ]", self.store.frame_path)
        print("[cmd    ]", self.store.cmd_path)
        if self.motion_udp_target:
            print("[motion]", f"UDP {self.motion_udp_target[0]}:{self.motion_udp_target[1]}")
        else:
            print("[motion]", "UDP forwarding disabled")
        print("[video  ]", "on-demand" if self.video_on_demand else "always upload")

        tasks = [
            create_task(self.control_loop()),
            create_task(self.video_loop()),
        ]

        try:
            await self.stop_event.wait()
        finally:
            for task in tasks:
                task.cancel()

            self.write_safety_stop("client_exit")
            if self.motion_udp_sock is not None:
                self.motion_udp_sock.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PYNQ 小车端网络传输客户端")
    parser.add_argument("--server", default="ws://127.0.0.1:8000", help="中转服务器地址，例如 ws://1.2.3.4:8000")
    parser.add_argument("--token", default="change_this_token", help="简单鉴权 token，需要和服务器一致")
    parser.add_argument("--runtime", default=None, help="共享内存目录，Linux默认 /dev/shm/car_runtime，Windows默认 ./runtime")
    parser.add_argument("--status-hz", type=float, default=2.0, help="状态上传频率")
    parser.add_argument("--video-fps", type=float, default=30.0, help="图像发送检查频率，不是实际摄像头帧率")
    parser.add_argument("--reconnect-delay", type=float, default=2.0, help="断线后重连间隔")
    parser.add_argument("--command-timeout", type=float, default=1.0, help="超过多少秒未收到控制命令就写入 stop；<=0 表示关闭")
    parser.add_argument("--max-image-bytes", type=int, default=300_000, help="单帧 JPEG 最大字节数，防止异常大图堵塞网络")
    parser.add_argument("--motion-udp-host", default="127.0.0.1", help="运动控制接收端 UDP 地址；留空则不转发")
    parser.add_argument("--motion-udp-port", type=int, default=50009, help="运动控制接收端 UDP 端口")
    parser.add_argument("--video-on-demand", dest="video_on_demand", action="store_true", default=True, help="无人观看网页视频时暂停上传视频帧")
    parser.add_argument("--no-video-on-demand", dest="video_on_demand", action="store_false", help="不等待网页观看者，始终上传视频帧")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    client = CarNetworkClient(
        server=args.server,
        token=args.token,
        runtime_dir=args.runtime,
        status_hz=args.status_hz,
        video_fps=args.video_fps,
        reconnect_delay=args.reconnect_delay,
        command_timeout=args.command_timeout,
        max_image_bytes=args.max_image_bytes,
        motion_udp_host=args.motion_udp_host,
        motion_udp_port=args.motion_udp_port,
        video_on_demand=args.video_on_demand,
    )

    loop = asyncio.get_event_loop()

    def ask_exit():
        print("[main] exit requested")
        client.stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, ask_exit)
        loop.add_signal_handler(signal.SIGTERM, ask_exit)
    except NotImplementedError:
        # Windows 可能不支持 add_signal_handler
        pass

    await client.run()


if __name__ == "__main__":
    try:
        event_loop = asyncio.get_event_loop()
        event_loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("KeyboardInterrupt")

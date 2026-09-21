"""
quick_relay_server.py

本地/课程调试用中转服务器。

真实部署时，你可以把这个文件放到 VPS 上运行。
接口和 car_net_client.py 对应：

- /car/control：车端控制/状态通道
- /web/control：网页控制/状态通道
- /car/video：车端图像上传通道
- /web/video：网页图像接收通道

运行：
    python quick_relay_server.py --host 0.0.0.0 --port 8000 --token change_this_token
"""

import argparse
import asyncio
from http import HTTPStatus
import json
import os
from urllib.parse import urlsplit
from typing import Optional, Set

import websockets
try:
    from websockets.datastructures import Headers
    from websockets.http11 import Response
except Exception:  # websockets < 11
    Headers = None
    Response = None
from websockets.exceptions import ConnectionClosed
from websockets.server import WebSocketServerProtocol


class RelayServer:
    def __init__(self, token: str, web_file: str, video_send_timeout: float = 0.2):
        self.token = token
        self.web_file = web_file
        self.video_send_timeout = video_send_timeout
        self.car_control = None  # type: Optional[WebSocketServerProtocol]
        self.web_controls = set()  # type: Set[WebSocketServerProtocol]
        self.web_videos = set()  # type: Set[WebSocketServerProtocol]
        # 音频通道(纯转发文字/控制命令, 不碰云端/不传PCM): 车端编排, 服务器只转发
        self.car_audio = None  # type: Optional[WebSocketServerProtocol]
        self.web_audios = set()  # type: Set[WebSocketServerProtocol]

    def token_ok(self, path: str) -> bool:
        # 简化解析，够课程调试使用
        return f"token={self.token}" in path

    async def safe_send(self, ws: WebSocketServerProtocol, data, timeout: Optional[float] = None) -> bool:
        try:
            if timeout and timeout > 0:
                await asyncio.wait_for(ws.send(data), timeout=timeout)
            else:
                await ws.send(data)
            return True
        except Exception:
            return False

    async def broadcast(self, clients: Set[WebSocketServerProtocol], data, timeout: Optional[float] = None) -> int:
        snapshot = list(clients)
        if not snapshot:
            return 0

        results = await asyncio.gather(
            *(self.safe_send(client, data, timeout=timeout) for client in snapshot),
            return_exceptions=True,
        )

        dead = []
        for client, ok in zip(snapshot, results):
            if ok is not True:
                dead.append(client)

        for client in dead:
            clients.discard(client)
            try:
                await client.close()
            except Exception:
                pass

        return len(dead)

    async def notify_car(self, event: str, **payload) -> None:
        """Send lightweight server-side state to the car control channel."""
        if self.car_control is None:
            return

        msg = {
            "type": "server_event",
            "event": event,
            "web_control_clients": len(self.web_controls),
            "web_video_clients": len(self.web_videos),
        }
        msg.update(payload)
        await self.safe_send(self.car_control, json.dumps(msg, ensure_ascii=False, separators=(",", ":")))

    async def notify_video_demand(self) -> None:
        await self.notify_car("video_demand", enabled=len(self.web_videos) > 0)

    async def notify_control_demand(self) -> None:
        await self.notify_car("control_demand", enabled=len(self.web_controls) > 0)

    def http_response(self, status: HTTPStatus, content_type: str, body: bytes):
        headers = [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]

        if Response is not None and Headers is not None:
            response_headers = Headers()
            for key, value in headers:
                response_headers[key] = value
            return Response(status.value, status.phrase, response_headers, body)

        return status, headers, body

    async def process_request(self, first, second=None):
        """Serve the control page on the same port as the WebSocket relay."""
        request = second if hasattr(second, "path") else None
        path = getattr(request, "path", None)
        if path is None:
            path = first if isinstance(first, str) else getattr(getattr(first, "request", None), "path", "")

        route = urlsplit(path).path
        if route.startswith(("/car/control", "/web/control", "/car/video", "/web/video",
                              "/car/audio", "/web/audio")):
            return None

        if route == "/healthz":
            body = b"ok\n"
            return self.http_response(HTTPStatus.OK, "text/plain; charset=utf-8", body)

        if route not in {"/", "/index.html", "/test_web_client.html"}:
            body = b"not found\n"
            return self.http_response(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", body)

        try:
            with open(self.web_file, "rb") as f:
                body = f.read()
        except OSError:
            body = b"control page missing\n"
            return self.http_response(HTTPStatus.INTERNAL_SERVER_ERROR, "text/plain; charset=utf-8", body)

        return self.http_response(HTTPStatus.OK, "text/html; charset=utf-8", body)

    async def handler(self, ws: WebSocketServerProtocol, path: Optional[str] = None):
        if path is None:
            request = getattr(ws, "request", None)
            path = getattr(request, "path", None) or getattr(ws, "path", "")

        if not self.token_ok(path):
            await ws.close(code=1008, reason="bad token")
            return

        if path.startswith("/car/control"):
            await self.handle_car_control(ws)
        elif path.startswith("/web/control"):
            await self.handle_web_control(ws)
        elif path.startswith("/car/video"):
            await self.handle_car_video(ws)
        elif path.startswith("/web/video"):
            await self.handle_web_video(ws)
        elif path.startswith("/car/audio"):
            await self.handle_car_audio(ws)
        elif path.startswith("/web/audio"):
            await self.handle_web_audio(ws)
        else:
            await ws.close(code=1008, reason="unknown path")

    async def handle_car_control(self, ws: WebSocketServerProtocol):
        self.car_control = ws
        print("[server] car control connected")
        await self.notify_video_demand()
        await self.notify_control_demand()
        try:
            async for msg in ws:
                # 车端发来的状态，转发给所有网页端
                await self.broadcast(self.web_controls, msg)
        except ConnectionClosed:
            pass
        finally:
            if self.car_control is ws:
                self.car_control = None
            print("[server] car control disconnected")

    async def handle_web_control(self, ws: WebSocketServerProtocol):
        self.web_controls.add(ws)
        print("[server] web control connected")
        await self.notify_control_demand()
        try:
            async for msg in ws:
                # 网页端发来的控制命令，转发给车端
                if self.car_control is not None:
                    await self.safe_send(self.car_control, msg)
                else:
                    await self.safe_send(ws, '{"type":"error","msg":"car offline"}')
        except ConnectionClosed:
            pass
        finally:
            self.web_controls.discard(ws)
            await self.notify_control_demand()
            print("[server] web control disconnected")

    async def handle_car_video(self, ws: WebSocketServerProtocol):
        print("[server] car video connected")
        try:
            async for frame in ws:
                removed = await self.broadcast(self.web_videos, frame, timeout=self.video_send_timeout)
                if removed:
                    print(f"[server] dropped slow web video clients: {removed}")
                    await self.notify_video_demand()
        except ConnectionClosed:
            pass
        finally:
            print("[server] car video disconnected")

    async def handle_web_video(self, ws: WebSocketServerProtocol):
        self.web_videos.add(ws)
        print("[server] web video connected")
        await self.notify_video_demand()
        try:
            # 网页视频端通常只接收，不发送；这里保持连接即可
            async for _ in ws:
                pass
        except ConnectionClosed:
            pass
        finally:
            self.web_videos.discard(ws)
            await self.notify_video_demand()
            print("[server] web video disconnected")

    async def handle_car_audio(self, ws: WebSocketServerProtocol):
        # 车端音频助手连接: 把车端发来的文字(ASR/回答/状态)广播给所有网页音频端
        self.car_audio = ws
        print("[server] car audio connected")
        try:
            async for msg in ws:
                await self.broadcast(self.web_audios, msg)
        except ConnectionClosed:
            pass
        finally:
            if self.car_audio is ws:
                self.car_audio = None
            print("[server] car audio disconnected")

    async def handle_web_audio(self, ws: WebSocketServerProtocol):
        # 网页音频端: 把网页命令(开启/关闭音频, 输入框文字TTS)转发给车端音频助手
        self.web_audios.add(ws)
        print("[server] web audio connected")
        try:
            async for msg in ws:
                if self.car_audio is not None:
                    await self.safe_send(self.car_audio, msg)
                else:
                    await self.safe_send(ws, '{"type":"audio_status","text":"音频助手离线"}')
        except ConnectionClosed:
            pass
        finally:
            self.web_audios.discard(ws)
            print("[server] web audio disconnected")


async def main():
    parser = argparse.ArgumentParser(description="课程设计调试用 WebSocket 中转服务器")
    parser.add_argument("--host", default=os.environ.get("CAR_RELAY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CAR_RELAY_PORT", "8000")))
    parser.add_argument("--token", default=os.environ.get("CAR_RELAY_TOKEN", "change_this_token"))
    parser.add_argument(
        "--web-file",
        default=os.environ.get(
            "CAR_RELAY_WEB_FILE",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_web_client.html"),
        ),
        help="同端口 HTTP 访问时返回的控制网页",
    )
    parser.add_argument("--ping-interval", type=float, default=30.0, help="WebSocket ping 间隔；设大可减少空闲保活流量")
    parser.add_argument("--ping-timeout", type=float, default=15.0, help="WebSocket ping 超时时间")
    parser.add_argument("--video-send-timeout", type=float, default=0.2, help="单个网页视频连接发送超时；慢连接会被移除")
    args = parser.parse_args()

    relay = RelayServer(token=args.token, web_file=args.web_file, video_send_timeout=args.video_send_timeout)

    print(f"[server] ws://{args.host}:{args.port}")
    print(f"[server] token={args.token}")
    print(f"[server] web_file={args.web_file}")

    async with websockets.serve(
        relay.handler,
        args.host,
        args.port,
        max_size=None,
        ping_interval=args.ping_interval,
        ping_timeout=args.ping_timeout,
        process_request=relay.process_request,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

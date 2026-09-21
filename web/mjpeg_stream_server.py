"""
MJPEG WebSocket 推流服务
========================
调用 VDMA 驱动获取原始帧，经 JPEG 压缩后通过 WebSocket 多客户端广播。

用法：
    python3 mjpeg_stream_server.py [--port 8080] [--quality 40]
"""

import sys
import os
import time
import json
import struct
import threading
import argparse
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ov5640_vdma_driver import OV5640VDMADriver


class LatestItem:
    """线程安全的最新数据持有者，单生产者多消费者。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.data = None
        self.seq = 0

    def update(self, data, seq):
        with self._lock:
            self.data = data
            self.seq = seq

    def get(self, last_seq=0):
        with self._lock:
            if self.seq > last_seq:
                return self.data, self.seq
            return None, last_seq


class MJPEGStreamServer:
    """JPEG 编码 + WebSocket 多客户端广播。"""

    def __init__(self, driver, jpeg_quality=40, port=8080):
        self._drv = driver
        self._quality = jpeg_quality
        self._scale = 1.0
        self._port = port
        self._latest = LatestItem()
        self._running = False
        self._clients = []
        self._clients_lock = threading.Lock()

    def start(self):
        """启动编码线程和 WebSocket 服务。"""
        self._drv.open()
        self._drv.start()
        self._running = True
        threading.Thread(target=self._encode_loop, name="mjpeg-enc", daemon=True).start()
        threading.Thread(target=self._ws_server, name="ws-srv", daemon=True).start()
        print("[mjpeg] server started, port=%d Q=%d" % (self._port, self._quality))

    def stop(self):
        self._running = False
        self._drv.stop()
        self._drv.close()

    def set_quality(self, q):
        self._quality = max(10, min(95, int(q)))

    def set_scale(self, s):
        self._scale = max(0.25, min(1.0, float(s)))

    # ── 编码线程 ──────────────────────────────────────────────────────

    def _encode_loop(self):
        """从驱动取原始帧 → JPEG 压缩 → 发布。"""
        last_seq = 0
        while self._running:
            frame, seq = self._drv.get_frame(last_seq, timeout=0.05)
            if frame is None:
                continue
            last_seq = seq

            # 可选缩放
            if self._scale < 0.99:
                h = max(2, int(frame.shape[0] * self._scale))
                w = max(2, int(frame.shape[1] * self._scale))
                frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_NEAREST)

            # JPEG 压缩（BGR 字节序直喂，零转换）
            ok, jpg = cv2.imencode('.jpg', frame, [
                int(cv2.IMWRITE_JPEG_QUALITY), self._quality,
                int(cv2.IMWRITE_JPEG_OPTIMIZE), 1,
            ])
            if not ok:
                continue

            self._latest.update(jpg.tobytes(), seq)

    # ── WebSocket 服务 ────────────────────────────────────────────────

    def _ws_server(self):
        import socket
        import select
        import hashlib
        import base64

        WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

        def ws_accept(key):
            return base64.b64encode(hashlib.sha1(key.encode()+WS_GUID).digest()).decode()

        def ws_send(conn, payload, opcode=0x02):
            n = len(payload)
            hdr = bytes([0x80|opcode])
            if n < 126:
                hdr += bytes([n])
            elif n < 65536:
                hdr += bytes([126]) + struct.pack(">H", n)
            else:
                hdr += bytes([127]) + struct.pack(">Q", n)
            conn.sendall(hdr + payload)

        def ws_recv(conn):
            r, _, _ = select.select([conn], [], [], 0.0)
            if not r:
                return None
            try:
                hdr = conn.recv(2)
                if len(hdr) < 2:
                    return None
            except Exception:
                return None
            opcode = hdr[0] & 0x0F
            masked = (hdr[1] & 0x80) != 0
            length = hdr[1] & 0x7F
            if length == 126:
                ext = conn.recv(2)
                if len(ext) < 2:
                    return None
                length = struct.unpack(">H", ext)[0]
            elif length == 127:
                ext = conn.recv(8)
                if len(ext) < 8:
                    return None
                length = struct.unpack(">Q", ext)[0]
            if length > 4*1024*1024:
                return None
            mask = conn.recv(4) if masked else b""
            if masked and len(mask) < 4:
                return None
            payload = b""
            while len(payload) < length:
                try:
                    chunk = conn.recv(min(length-len(payload), 8192))
                except Exception:
                    break
                if not chunk:
                    break
                payload += chunk
            if masked and len(mask) == 4:
                payload = bytes(b ^ mask[i%4] for i,b in enumerate(payload))
            return opcode, payload

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self._port))
        srv.listen(8)
        srv.setblocking(False)

        while self._running:
            r, _, _ = select.select([srv], [], [], 0.5)
            if not r:
                continue
            conn, addr = srv.accept()
            threading.Thread(target=self._handle_client, args=(conn, ws_accept, ws_send, ws_recv), daemon=True).start()

    def _handle_client(self, conn, ws_accept, ws_send, ws_recv):
        """处理单个 WebSocket 客户端。"""
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                req += chunk
            req_str = req.decode(errors="ignore")
            key = None
            for line in req_str.split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            if not key:
                conn.close()
                return

            # 握手
            resp = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % ws_accept(key)
            )
            conn.sendall(resp.encode())

            # 广播循环
            last_seq = 0
            while self._running:
                data, seq = self._latest.get(last_seq)
                if data is not None and seq > last_seq:
                    last_seq = seq
                    frame = struct.pack("<BI", 0x01, seq & 0xFFFFFFFF) + data
                    try:
                        ws_send(conn, frame)
                    except Exception:
                        break

                # 接收控制消息
                msg = ws_recv(conn)
                if msg is not None:
                    op, payload = msg
                    if op == 0x08:
                        break
                    elif op in (0x01, 0x02):
                        try:
                            ctrl = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
                            if 'q' in ctrl:
                                self.set_quality(ctrl['q'])
                            if 'scale' in ctrl:
                                self.set_scale(ctrl['scale'])
                        except Exception:
                            pass

                time.sleep(0.002 if data is not None else 0.008)
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ── 启动入口 ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="MJPEG WebSocket 推流服务")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--quality", type=int, default=40)
    args = ap.parse_args()

    drv = OV5640VDMADriver()
    srv = MJPEGStreamServer(drv, jpeg_quality=args.quality, port=args.port)

    try:
        srv.start()
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n停止。")
    finally:
        srv.stop()

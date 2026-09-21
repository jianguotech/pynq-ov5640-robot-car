#!/usr/bin/env python3
"""
stream_server.py — Unified low-latency video + audio streaming server.

Single process, single bitstream load. Replaces web_viewer.py + board_server.py.

Architecture:
  ┌──────────────────────────────────────────────────────────────┐
  │  ov5640_audio_mecanum.bit  (loaded ONCE)                     │
  │                                                              │
  │  CameraEngine (camera_engine.py)   AudioCapture (inline)      │
  │  VDMA S2MM → HP0                   DMA_RX S2MM → HP1         │
  │  park_reg → JPEG encode (~8ms)     PDM → popcount → PCM16    │
  │       │                                  │                    │
  │       ▼                                  ▼                    │
  │  cam.latest (LatestItem)           audio.latest (LatestItem)  │
  │       │                                  │                    │
  │       └────────────┬─────────────────────┘                    │
  │                    ▼                                          │
  │  WebSocket Server (:8080)                                     │
  │    /ws → binary frames [type:u8][seq:u32le][payload]         │
  │    /   → HTML client page                                     │
  └──────────────────────────────────────────────────────────────┘

WebSocket binary protocol:
  Type 0x01 = JPEG frame:  [0x01][seq:u32le][JPEG bytes]
  Type 0x02 = PCM audio:   [0x02][rate:u32le][PCM s16le bytes]

Client → Server (text frames, JSON):
  {"q": 40}           — set JPEG quality
  {"scale": 0.5}      — set resolution scale

Usage:
    sudo /opt/python3.6/bin/python3.6 -u stream_server.py [--port 8080]
    # Then open: http://<board-ip>:8080
"""

import sys
import os
import time
import json
import struct
import hashlib
import base64
import socket
import select
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import numpy as np

# Ensure local directory is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pynq import Bitstream, MMIO, Xlnk

# ── Camera engine (zero-copy, threaded JPEG) ───────────────────────────────
from camera_engine import CameraEngine, LatestItem

# ── Audio DMA driver (reuse existing classes, bypass their bitstream load) ─
from load_overlay_pynq2 import SimpleDMA, DMA_TX_ADDR, DMA_RX_ADDR, DMA_RANGE
from audio_dma_driver import MicCapture

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════
W, H = 640, 480
DEFAULT_JPEG_Q = 40
MIC_BUF_WORDS = 2048            # ~3ms latency per chunk
AUDIO_GAIN = 4000.0
TARGET_AUDIO_RATE = 16000       # target PCM rate after decimation

# ── Pre-compute popcount LUT (16-bit → # of 1s) ────────────────────────────
print("[init] popcount table...", end=" ", flush=True)
_POP = np.zeros(65536, dtype=np.float32)
for v in range(65536):
    _POP[v] = float(bin(v).count('1'))
print("done")

def _box(x, k):
    """Box filter decimation by factor k."""
    if k <= 1:
        return x
    n = (len(x) // k) * k
    return x[:n].reshape(-1, k).mean(axis=1) if n else x[:0]


# ═══════════════════════════════════════════════════════════════════════════
# WebSocket helpers
# ═══════════════════════════════════════════════════════════════════════════
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def ws_make_accept(key):
    return base64.b64encode(
        hashlib.sha1(key.encode() + WS_GUID).digest()).decode()

def ws_send(conn, payload, opcode=0x02):
    """Send a binary (or text) WebSocket frame, server→client (unmasked)."""
    n = len(payload)
    hdr = bytes([0x80 | opcode])
    if n < 126:
        hdr += bytes([n])
    elif n < 65536:
        hdr += bytes([126]) + struct.pack(">H", n)
    else:
        hdr += bytes([127]) + struct.pack(">Q", n)
    conn.sendall(hdr + payload)

def ws_recv(conn):
    """Non-blocking read one WebSocket frame. Returns (opcode, payload) or None.

    Uses select() to avoid blocking. Handles masked client frames.
    """
    ready, _, _ = select.select([conn], [], [], 0.0)
    if not ready:
        return None

    try:
        hdr = conn.recv(2)
        if len(hdr) < 2:
            return None
    except (socket.timeout, BlockingIOError, OSError):
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

    if length > 4 * 1024 * 1024:    # 4 MB sanity cap
        return None

    mask = conn.recv(4) if masked else b""
    if masked and len(mask) < 4:
        return None

    payload = b""
    while len(payload) < length:
        try:
            chunk = conn.recv(min(length - len(payload), 8192))
        except (socket.timeout, BlockingIOError, OSError):
            break
        if not chunk:
            break
        payload += chunk

    if masked and len(mask) == 4:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    return opcode, payload


# ═══════════════════════════════════════════════════════════════════════════
# Audio capture engine
# ═══════════════════════════════════════════════════════════════════════════
class AudioCapture:
    """PDM mic → DMA_RX S2MM → PCM16 threaded capture.

    Uses MicCapture (SimpleDMA) with reset-per-transfer for the endless
    PDM stream. Demodulates: popcount → DC removal → decimation → gain.
    Publishes PCM chunks via LatestItem for WebSocket broadcast.
    """

    def __init__(self, dma_rx):
        self.mic = MicCapture(dma_rx)
        self.latest = LatestItem()
        self.rate = TARGET_AUDIO_RATE
        self._running = False
        self._thread = None

    def start(self):
        self.mic.start(buf_words=MIC_BUF_WORDS)
        self._running = True
        self._thread = threading.Thread(target=self._loop,
                                        name="audio-cap", daemon=True)
        self._thread.start()

    def _measure_rate(self):
        """Quick burst measurement → decimation factors."""
        n = 0
        t0 = time.time()
        while time.time() - t0 < 0.5:
            try:
                n += len(self.mic.read_chunk())
            except Exception:
                continue
        wr = n / max(time.time() - t0, 0.01)      # packed words/sec
        D = max(1, int(round(wr / TARGET_AUDIO_RATE)))
        d1 = max(1, D // 2)
        d2 = max(1, int(round(D / max(d1, 1))))
        self.rate = int(round(wr / max(d1 * d2, 1)))
        print("[aud] wr=%.0f w/s  D=%d→(%d,%d)  out=%d Hz" % (
            wr, D, d1, d2, self.rate))
        return d1, d2

    def _loop(self):
        """Continuous: read → demodulate → publish."""
        d1, d2 = self._measure_rate()

        # Initial DC estimate
        try:
            dc = float(_POP[self.mic.read_chunk().astype(np.uint16)].mean())
        except Exception:
            dc = 8.0

        seq = 0

        while self._running:
            try:
                packed = self.mic.read_chunk()
            except Exception:
                time.sleep(0.001)
                continue

            ones = _POP[packed.astype(np.uint16)]    # 0..16 per word

            # DC removal (EMA, 10ms time constant)
            dc = 0.99 * dc + 0.01 * float(ones.mean())
            sig = ones - dc

            # Two-stage box-filter decimation → target sample rate
            sig = _box(_box(sig, d1), d2)

            # Gain + clip → int16 PCM
            pcm = np.clip(sig * AUDIO_GAIN, -32768, 32767).astype('<i2')

            seq += 1
            self.latest.update(pcm.tobytes(), seq)

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self.mic.stop()
        except Exception:
            pass
        print("[aud] stopped")


# ═══════════════════════════════════════════════════════════════════════════
# HTML Client Page
# ═══════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OV5640 Stream</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#111;color:#ccc;font-family:'SF Mono',Consolas,monospace;
     display:flex;flex-direction:column;align-items:center;min-height:100vh}
h2{padding:12px;font-size:15px;letter-spacing:2px;color:#888}
#vid{max-width:96vw;max-height:68vh;display:block;margin:4px auto;
     border:1px solid #2a2a2a;border-radius:3px;image-rendering:auto;
     background:#1a1a1a}
#bar{background:#1a1a1a;width:100%;max-width:720px;padding:10px 16px;
     margin:8px auto;border-radius:4px;display:flex;flex-wrap:wrap;
     align-items:center;gap:14px;font-size:13px}
#bar label{color:#777;font-size:11px;text-transform:uppercase;letter-spacing:1px}
#bar input[type=range]{width:80px;vertical-align:middle;accent-color:#1a6}
#bar select{background:#2a2a2a;color:#ddd;border:1px solid #444;padding:4px 8px;
            border-radius:3px;font-family:inherit;font-size:12px}
#bar button{padding:7px 20px;border:none;border-radius:4px;cursor:pointer;
            font-family:inherit;font-size:13px;font-weight:600;letter-spacing:1px}
.play{background:#1a6;color:#fff}.stop{background:#933;color:#fff}
#stats{color:#0a0;font-size:12px;margin-left:auto;min-width:170px;
       text-align:right;line-height:1.6}
.fps{font-size:22px;font-weight:bold}
.hint{font-size:10px;color:#555;text-align:center;padding:6px}
.hint kbd{background:#2a2a2a;border:1px solid #444;border-radius:2px;
          padding:1px 5px;font-family:inherit}
</style>
</head>
<body>
<h2>OV5640  &middot;  Camera  +  Mic Audio</h2>
<img id="vid" alt="Connecting...">

<div id="bar">
  <label>Quality <span id="qv">40</span><br>
    <input type="range" id="q" min="10" max="90" value="40"></label>

  <label>Resolution<br>
    <select id="res">
      <option value="1.0">640&times;480</option>
      <option value="0.5">320&times;240</option>
      <option value="0.25">160&times;120</option>
    </select></label>

  <button id="abtn" class="play" onclick="toggleAudio()">&#9654; Play Mic</button>

  <div id="stats">
    <span class="fps" id="fps">--</span> fps<br>
    <span id="bw">--</span> kB/s &nbsp;
    <span id="lag">--</span>ms lag
  </div>
</div>

<div class="hint">
  <kbd>1</kbd>/<kbd>2</kbd>/<kbd>3</kbd> resolution &nbsp;
  <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> quality &nbsp;
  <kbd>A</kbd> toggle audio
</div>

<script>
var ws=null, prevBlobUrl=null;
var audioCtx=null, audioOn=false, nextAudioTime=0, audioRate=16000;
var reconnectTimer=null, reconnectDelay=1000;
var frameCount=0, byteCount=0, fpsTimer=performance.now(), bwTimer=0;

// ── WebSocket connect ────────────────────────────────────────────────
function connect(){
  if(ws){try{ws.close()}catch(e){}}
  var url='ws'+('https:'==location.protocol?'s':'')+'://'+location.host+'/ws';
  ws=new WebSocket(url);
  ws.binaryType='arraybuffer';
  ws.onopen=function(){
    console.log('WS connected');
    if(reconnectTimer){clearInterval(reconnectTimer);reconnectTimer=null;}
    reconnectDelay=1000;
    document.getElementById('vid').alt='Streaming...';
    frameCount=0;byteCount=0;fpsTimer=bwTimer=performance.now();
  };
  ws.onmessage=function(e){
    if(!(e.data instanceof ArrayBuffer)) return;
    var d=new DataView(e.data), type=d.getUint8(0);
    if(type===1){  // ── JPEG video frame ──
      var seq=d.getUint32(1,true);
      var jpg=new Uint8Array(e.data,5);
      var blob=new Blob([jpg],{type:'image/jpeg'});
      var url=URL.createObjectURL(blob);
      document.getElementById('vid').src=url;
      if(prevBlobUrl) URL.revokeObjectURL(prevBlobUrl);
      prevBlobUrl=url;
      frameCount++; byteCount+=e.data.byteLength;
    }else if(type===2&&audioOn){  // ── PCM audio ──
      var rate=d.getUint32(1,true);
      var pcm=new Int16Array(e.data,5);
      audioRate=rate;
      if(!audioCtx||audioCtx.state=='closed') return;
      var f32=new Float32Array(pcm.length);
      for(var i=0;i<pcm.length;i++) f32[i]=pcm[i]/32768;
      var buf=audioCtx.createBuffer(1,f32.length,rate);
      buf.getChannelData(0).set(f32);
      var src=audioCtx.createBufferSource(); src.buffer=buf;
      src.connect(audioCtx.destination);
      var now=audioCtx.currentTime;
      if(nextAudioTime<now) nextAudioTime=now;
      if(nextAudioTime-now>0.3) nextAudioTime=now+0.02;
      src.start(nextAudioTime); nextAudioTime+=f32.length/rate;
    }
  };
  ws.onclose=function(){
    console.log('WS closed, reconnect in '+reconnectDelay+'ms');
    if(!reconnectTimer){
      reconnectTimer=setInterval(function(){
        reconnectDelay=Math.min(reconnectDelay*1.5,15000);
        connect();
      },reconnectDelay);
    }
  };
  ws.onerror=function(){try{ws.close()}catch(e){}};
}
connect();

// ── Stats update ─────────────────────────────────────────────────────
setInterval(function(){
  var now=performance.now();
  if(now-fpsTimer>1500){
    var fps=Math.round(frameCount*1000/(now-fpsTimer||1));
    var bw=Math.round(byteCount/(now-bwTimer||1)*1000/1024);
    document.getElementById('fps').textContent=fps||'--';
    document.getElementById('bw').textContent=bw||'--';
    document.getElementById('lag').textContent =
      audioOn&&audioCtx?Math.round((nextAudioTime-audioCtx.currentTime)*1000):'--';
    frameCount=0;byteCount=0;fpsTimer=bwTimer=now;
  }
},1500);

// ── Quality slider ───────────────────────────────────────────────────
document.getElementById('q').oninput=function(){
  document.getElementById('qv').textContent=this.value;
};
document.getElementById('q').onchange=function(){
  var v=parseInt(this.value);
  if(ws&&ws.readyState===1) ws.send(JSON.stringify({q:v}));
};

// ── Resolution selector ──────────────────────────────────────────────
document.getElementById('res').onchange=function(){
  var v=parseFloat(this.value);
  if(ws&&ws.readyState===1) ws.send(JSON.stringify({scale:v}));
};

// ── Audio toggle ─────────────────────────────────────────────────────
function toggleAudio(){
  audioOn=!audioOn;
  var btn=document.getElementById('abtn');
  if(audioOn){
    audioCtx=new(window.AudioContext||window.webkitAudioContext)({sampleRate:16000});
    nextAudioTime=audioCtx.currentTime+0.05;
    btn.textContent='⏸ Stop Mic'; btn.className='stop';
  }else{
    if(audioCtx){audioCtx.close();audioCtx=null;}
    nextAudioTime=0;
    btn.textContent='▶ Play Mic'; btn.className='play';
  }
}

// ── Keyboard shortcuts ───────────────────────────────────────────────
document.onkeydown=function(e){
  var k=e.key.toLowerCase();
  if(k==='a') toggleAudio();
  if(k==='1'){document.getElementById('res').value='1.0';
    document.getElementById('res').onchange();}
  if(k==='2'){document.getElementById('res').value='0.5';
    document.getElementById('res').onchange();}
  if(k==='3'){document.getElementById('res').value='0.25';
    document.getElementById('res').onchange();}
  if(k==='arrowleft'){
    var s=document.getElementById('q'); s.value=Math.max(10,parseInt(s.value)-5);
    s.oninput();s.onchange();
  }
  if(k==='arrowright'){
    var s=document.getElementById('q'); s.value=Math.min(90,parseInt(s.value)+5);
    s.oninput();s.onchange();
  }
};
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════
# HTTP / WebSocket Request Handler
# ═══════════════════════════════════════════════════════════════════════════
class StreamHandler(BaseHTTPRequestHandler):
    """Serves HTML at / and WebSocket video+audio at /ws.

    Class-level shared state (set by main() after initialization):
      camera: CameraEngine instance (or None)
      audio:  AudioCapture instance (or None)
    """

    camera = None
    audio = None

    def log_message(self, *args):
        pass   # quiet

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/ws":
            self._handle_ws()

        elif self.path == "/stats":
            s = {}
            if self.camera:
                s['camera'] = self.camera.get_stats()
            if self.audio:
                s['audio'] = {'rate': self.audio.rate}
            body = json.dumps(s).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_error(404)

    def _handle_ws(self):
        """WebSocket upgrade, then streaming loop (video + audio broadcast).

        One encode thread (in CameraEngine) serves all clients. Each
        WebSocket handler thread independently polls for new frames.
        """
        key = self.headers.get("Sec-WebSocket-Key", "")
        if not key:
            self.send_error(400)
            return

        # ── Upgrade handshake ──
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws_make_accept(key))
        self.end_headers()

        conn = self.request
        last_vseq = 0
        last_aseq = 0

        print("[ws] client connected")

        try:
            while True:
                sent = False

                # ── Video: poll latest JPEG ──
                if self.camera is not None:
                    data, seq = self.camera.latest.get(last_vseq)
                    if data is not None and seq > last_vseq:
                        last_vseq = seq
                        frame = struct.pack("<BI", 0x01, seq & 0xFFFFFFFF) + data
                        ws_send(conn, frame, opcode=0x02)
                        sent = True

                # ── Audio: poll latest PCM chunk ──
                if self.audio is not None:
                    data, seq = self.audio.latest.get(last_aseq)
                    if data is not None and seq > last_aseq:
                        last_aseq = seq
                        frame = struct.pack("<BI", 0x02, self.audio.rate) + data
                        ws_send(conn, frame, opcode=0x02)
                        sent = True

                # ── Client → Server control messages (non-blocking) ──
                msg = ws_recv(conn)
                if msg is not None:
                    opcode, payload = msg
                    if opcode == 0x08:         # Close
                        break
                    elif opcode == 0x09:       # Ping → Pong
                        ws_send(conn, payload, opcode=0x0A)
                    elif opcode in (0x01, 0x02):  # Text or Binary
                        try:
                            ctrl = json.loads(
                                payload.decode() if isinstance(payload, bytes)
                                else payload)
                            self._apply_control(ctrl)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

                # ── Adaptive sleep ──
                if sent:
                    time.sleep(0.002)   # just yield after sending
                else:
                    time.sleep(0.008)   # idle wait, 8ms

        except (ConnectionError, OSError, BrokenPipeError,
                ConnectionResetError):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
            print("[ws] client disconnected")

    def _apply_control(self, ctrl):
        """Apply client control message to CameraEngine."""
        cam = self.camera
        if cam is None:
            return
        if 'q' in ctrl or 'quality' in ctrl:
            q = ctrl.get('q', ctrl.get('quality'))
            if q is not None:
                cam.set_quality(q)
                print("[ctl] quality → %d" % cam.jpeg_quality)
        if 'scale' in ctrl or 's' in ctrl:
            s = ctrl.get('scale', ctrl.get('s'))
            if s is not None:
                cam.set_scale(s)
                print("[ctl] scale → %.2f (%dx%d)" % (
                    cam.scale,
                    int(W * cam.scale), int(H * cam.scale)))


# ═══════════════════════════════════════════════════════════════════════════
# HTTP Server
# ═══════════════════════════════════════════════════════════════════════════
class ThreadedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="OV5640 + Audio unified WebSocket streaming server")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bitstream", type=str,
                    default="ov5640_audio_mecanum.bit")
    ap.add_argument("--quality", type=int, default=DEFAULT_JPEG_Q)
    ap.add_argument("--no-audio", action="store_true",
                    help="Disable microphone capture")
    args = ap.parse_args()

    # ── 1. Load bitstream ──────────────────────────────────────────
    bit_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            args.bitstream)
    if not os.path.exists(bit_path):
        print("ERROR: bitstream not found: %s" % bit_path)
        sys.exit(1)

    print("[init] loading bitstream: %s" % os.path.basename(bit_path))
    Bitstream(bit_path).download()
    time.sleep(0.3)
    print("[init] FPGA configured")

    # ── 2. Camera ──────────────────────────────────────────────────
    print("[init] starting camera engine...")
    cam = CameraEngine(width=W, height=H, jpeg_quality=args.quality)
    cam.init()
    StreamHandler.camera = cam

    # ── 3. Audio ───────────────────────────────────────────────────
    audio = None
    if not args.no_audio:
        print("[init] starting audio capture...")
        try:
            # Create DMA objects directly — do NOT load a second bitstream
            mmio_rx = MMIO(DMA_RX_ADDR, DMA_RANGE)
            dma_rx = SimpleDMA(mmio_rx, 2)    # S2MM direction
            audio = AudioCapture(dma_rx)
            audio.start()
            StreamHandler.audio = audio
        except Exception as e:
            print("[init] audio init failed: %s" % e)
            print("[init] continuing with video only")
            StreamHandler.audio = None
    else:
        print("[init] audio disabled (--no-audio)")
        StreamHandler.audio = None

    # ── 4. HTTP server ─────────────────────────────────────────────
    server = None
    for try_port in range(args.port, args.port + 20):
        try:
            server = ThreadedServer(("0.0.0.0", try_port), StreamHandler)
            args.port = try_port
            break
        except OSError:
            continue

    if server is None:
        print("ERROR: cannot bind any port")
        sys.exit(1)

    threading.Thread(target=server.serve_forever,
                     name="http-srv", daemon=True).start()

    # ── 5. Ready ───────────────────────────────────────────────────
    try:
        host_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        host_ip = "192.168.2.99"

    print("")
    print("=" * 55)
    print("  Stream server ready")
    print("  %s:%d  (WebSocket + HTTP)" % (host_ip, args.port))
    print("  Camera: %dx%d  Q=%d  scale=%.2f" % (
        W, H, cam.jpeg_quality, cam.scale))
    if audio:
        print("  Audio:  %d Hz PCM  gain=%.0f" % (
            audio.rate, AUDIO_GAIN))
    print("=" * 55)
    print("  Browser shortcuts:")
    print("    1/2/3 = resolution   ←/→ = quality   A = audio")
    print("")

    # ── 6. Periodic stats ──────────────────────────────────────────
    try:
        while True:
            time.sleep(5)
            s = cam.get_stats()
            print("[srv] fps=%s  enc=%.1fms  Q=%d  "
                  "scale=%.2f  frames=%d  drops=%d" % (
                      s['fps'], s['encode_ms'],
                      cam.jpeg_quality, cam.scale,
                      s['frames'], s['drops']))
    except KeyboardInterrupt:
        print("\n[exit] shutting down...")
    finally:
        cam.stop()
        if audio:
            audio.stop()
        if server:
            server.shutdown()
            server.server_close()
        print("[exit] done.")


if __name__ == "__main__":
    main()

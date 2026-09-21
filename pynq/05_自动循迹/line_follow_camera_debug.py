#!/usr/bin/env python3
import argparse
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import cv2
import numpy as np


BOARD_NOTEBOOK_DIR = "/home/xilinx/jupyter_notebooks"
DEFAULT_BITFILE = "/home/xilinx/jupyter_notebooks/ov5640_audio_mecanum.bit"
DEFAULT_SAVE_DIR = "/home/xilinx/line_debug"
TRACK_MODE_CHOICES = [
    "auto", "gray", "dark", "bright", "red", "blue", "yellow",
    "fast_dark", "fast_bright", "fast_gray", "fast_auto",
    "fast_red", "fast_white", "fast_red_white",
    "fast_white_line_left", "fast_white_line_right", "fast_white_line_nearest",
]


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="OV5640 VDMA + line-follow preprocessing debug viewer."
    )
    parser.add_argument("--track-mode", default="auto", choices=TRACK_MODE_CHOICES)
    parser.add_argument("--roi-y-ratio", type=float, default=0.40)
    parser.add_argument("--morph-kernel", type=int, default=5)
    parser.add_argument("--process-width", type=int, default=0,
                        help="resize frames before preprocessing; 0 keeps camera width")
    parser.add_argument("--process-height", type=int, default=0,
                        help="resize frames before preprocessing; 0 keeps camera height")
    parser.add_argument("--fast-camera-read", action="store_true",
                        help="skip per-frame color correction for faster black/white line tests")
    parser.add_argument("--lookahead-window", type=int, default=5)
    parser.add_argument("--margin", type=int, default=52)
    parser.add_argument("--minpix", type=int, default=70)
    parser.add_argument("--nwindows", type=int, default=10)
    parser.add_argument("--filter-alpha", type=float, default=0.35)
    parser.add_argument("--predict-frames", type=int, default=5)
    parser.add_argument("--save-dir", default=DEFAULT_SAVE_DIR)
    parser.add_argument("--frames", type=int, default=5,
                        help="capture this many frames when not using --serve")
    parser.add_argument("--load-bit", action="store_true",
                        help="download the merged bitstream before camera init")
    parser.add_argument("--bitfile", default=DEFAULT_BITFILE)
    parser.add_argument("--serve", action="store_true",
                        help="start a web viewer for live debug image")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--serve-fps", type=float, default=2.0,
                        help="web debug refresh/process rate; lower is lighter")
    parser.add_argument("--save-every", type=int, default=0,
                        help="save historical frame_N_panel.jpg every N frames; 0 disables")
    parser.add_argument("--ascii", action="store_true",
                        help="print a small binary-mask preview in terminal")
    return parser.parse_args(argv)


def enable_fast_camera_read(camera):
    def fast_get_frame(idx):
        i, _ = camera._safe_frame_idx(idx)
        frame = np.asarray(camera.frames[i])
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    camera.get_frame = fast_get_frame


def import_board_camera(load_bit, bitfile, fast_camera_read=False):
    sys.path.insert(0, BOARD_NOTEBOOK_DIR)
    if load_bit:
        from pynq import Bitstream
        print("LOAD_BITSTREAM path=%s" % bitfile)
        Bitstream(bitfile).download()
        print("LOAD_BITSTREAM_OK")

    from web_viewer import Camera
    camera = Camera()
    print("CAMERA_INIT_BEGIN")
    camera.init()
    if fast_camera_read:
        enable_fast_camera_read(camera)
        print("FAST_CAMERA_READ_OK")
    print("CAMERA_INIT_OK")
    return camera


def import_line_follow():
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    from ps_line_follow_prototype import TargetFilter, find_target, preprocess
    return TargetFilter, find_target, preprocess


def ascii_mask(mask, width=80, height=28):
    if mask is None or mask.size == 0:
        return ""
    small = cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    lines = []
    for row in small:
        chars = ["#" if int(v) > 0 else "." for v in row]
        lines.append("".join(chars))
    return "\n".join(lines)


def make_panel(frame, binary, roi_y, centers, target_x, target_source, info,
               confidence, frame_id):
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, roi_y), (w - 1, h - 1), (0, 255, 255), 2)
    cv2.line(overlay, (w // 2, 0), (w // 2, h - 1), (255, 0, 0), 2)

    for cx, cy in centers:
        cv2.circle(overlay, (int(cx), int(cy) + roi_y), 3, (0, 255, 0), -1)

    if target_x >= 0:
        color = (0, 0, 255) if target_source == "MEASURED" else (0, 165, 255)
        y = h // 2
        if centers:
            y = int(centers[min(len(centers) - 1, 5)][1]) + roi_y
        cv2.circle(overlay, (int(target_x), y), 8, color, -1)

    text_lines = [
        "frame_id=%d" % frame_id,
        "threshold=%s" % info.get("threshold_mode", "unknown"),
        "score=%.3f confidence=%.3f" % (info.get("threshold_score", 0.0), confidence),
        "target_x=%s source=%s" % (target_x, target_source),
        "error=%s" % (target_x - w // 2 if target_x >= 0 else "NA"),
    ]
    for i, text in enumerate(text_lines):
        cv2.putText(overlay, text, (10, 24 + i * 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2)

    mask_full = np.zeros((h, w), dtype=np.uint8)
    mask_full[roi_y:h, :] = binary
    mask_bgr = cv2.cvtColor(mask_full, cv2.COLOR_GRAY2BGR)

    raw_labeled = frame.copy()
    cv2.putText(raw_labeled, "raw camera frame", (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    cv2.putText(mask_bgr, "preprocessed binary mask", (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    panel = np.concatenate([raw_labeled, overlay, mask_bgr], axis=1)
    panel = cv2.resize(panel, (960, 240))
    return panel, overlay, mask_full


class SharedFrame:
    def __init__(self):
        self.lock = threading.Lock()
        self.jpg = None
        self.summary = "waiting for first frame"

    def update(self, panel, summary):
        ok, enc = cv2.imencode(".jpg", panel, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return
        with self.lock:
            self.jpg = enc.tobytes()
            self.summary = summary

    def snapshot(self):
        with self.lock:
            return self.jpg, self.summary


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(shared):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Line Follow Debug</title>
<style>body{background:#111;color:#eee;font-family:monospace;text-align:center}
img{max-width:100%;border:2px solid #555}.hint{color:#9fd;margin:12px}</style></head>
<body><h2>Line Follow Preprocess Debug</h2>
<div class="hint">left: raw | middle: prediction overlay | right: binary mask</div>
<img id="img" src="/latest.jpg"><pre id="summary"></pre>
<script>
setInterval(function(){
  document.getElementById('img').src='/latest.jpg?t='+Date.now();
  fetch('/summary').then(r=>r.text()).then(t=>document.getElementById('summary').textContent=t);
}, 500);
</script></body></html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
                return
            if self.path.startswith("/latest.jpg"):
                jpg, _ = shared.snapshot()
                if jpg is None:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b"no frame yet")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(jpg)
                return
            if self.path.startswith("/summary"):
                _, summary = shared.snapshot()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(summary.encode("utf-8"))
                return
            self.send_error(404)
    return Handler


def process_once(camera, target_filter, args, frame_id, save_dir=None,
                 print_ascii=False, save_history=True):
    _, find_target, preprocess = import_line_follow()
    frame = camera.get_frame(frame_id)
    if args.process_width > 0 and args.process_height > 0:
        frame = cv2.resize(frame, (args.process_width, args.process_height))
    roi_y, binary, info = preprocess(
        frame,
        track_mode=args.track_mode,
        roi_y_ratio=args.roi_y_ratio,
        morph_kernel=args.morph_kernel,
        return_debug=True,
    )
    found_raw, raw_target_x, centers, confidence = find_target(
        binary,
        lookahead_window=args.lookahead_window,
        margin=args.margin,
        minpix=args.minpix,
        nwindows=args.nwindows,
    )
    found, target_x, target_source, lost_frames = target_filter.update(
        found_raw, raw_target_x, frame.shape[1]
    )
    panel, overlay, mask_full = make_panel(
        frame, binary, roi_y, centers, target_x if found else -1,
        target_source, info, confidence, frame_id
    )
    summary = (
        "frame_id=%d found=%d raw_target=%d target_x=%d source=%s "
        "lost_frames=%d threshold=%s score=%.3f confidence=%.3f"
        % (
            frame_id, 1 if found else 0, raw_target_x, target_x, target_source,
            lost_frames, info.get("threshold_mode", "unknown"),
            info.get("threshold_score", 0.0), confidence,
        )
    )
    print(summary)

    if print_ascii:
        print("ASCII_BINARY_MASK_BEGIN")
        print(ascii_mask(binary))
        print("ASCII_BINARY_MASK_END")

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)
        cv2.imwrite(os.path.join(save_dir, "latest_raw.jpg"), frame)
        cv2.imwrite(os.path.join(save_dir, "latest_overlay.jpg"), overlay)
        cv2.imwrite(os.path.join(save_dir, "latest_binary.png"), mask_full)
        cv2.imwrite(os.path.join(save_dir, "latest_panel.jpg"), panel)
        if save_history:
            cv2.imwrite(os.path.join(save_dir, "frame_%06d_panel.jpg" % frame_id), panel)
        print("SAVED_PANEL=%s" % os.path.join(save_dir, "latest_panel.jpg"))

    return panel, summary


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if os.geteuid() != 0:
        print("ERROR: please run with sudo; VDMA/MMIO/Xlnk need root on this image.")
        return 2

    TargetFilter, _, _ = import_line_follow()
    target_filter = TargetFilter(alpha=args.filter_alpha, max_predict_frames=args.predict_frames)
    camera = import_board_camera(args.load_bit, args.bitfile, args.fast_camera_read)

    shared = SharedFrame()
    stop_flag = threading.Event()

    if args.serve:
        def worker():
            frame_id = 0
            interval = 1.0 / max(0.2, args.serve_fps)
            while not stop_flag.is_set():
                try:
                    start_t = time.time()
                    frame_id += 1
                    save_this_history = (
                        args.save_every > 0 and frame_id % args.save_every == 0
                    )
                    panel, summary = process_once(
                        camera, target_filter, args, frame_id,
                        save_dir=args.save_dir,
                        print_ascii=False,
                        save_history=save_this_history,
                    )
                    shared.update(panel, summary)
                    elapsed = time.time() - start_t
                    if elapsed < interval:
                        time.sleep(interval - elapsed)
                except Exception as exc:
                    print("CAPTURE_ERROR %s" % exc)
                    time.sleep(0.5)

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

        server = ThreadedHTTPServer(("0.0.0.0", args.port), make_handler(shared))
        print("LINE_FOLLOW_VIEWER_READY")
        print("OPEN_URL=http://192.168.10.183:%d/" % args.port)
        print("DEBUG_DIR=%s" % args.save_dir)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop_flag.set()
            server.shutdown()
            camera.stop()
        return 0

    for frame_id in range(1, max(1, args.frames) + 1):
        process_once(
            camera, target_filter, args, frame_id,
            save_dir=args.save_dir,
            print_ascii=args.ascii,
            save_history=True,
        )
        time.sleep(0.12)
    camera.stop()
    print("LINE_FOLLOW_CAMERA_DEBUG_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

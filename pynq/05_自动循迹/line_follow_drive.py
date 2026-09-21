#!/usr/bin/env python3
import argparse
import atexit
import mmap
import os
import signal
import struct
import sys
import time

import cv2


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from line_follow_camera_debug import import_board_camera
from ps_line_follow_prototype import (
    TargetFilter,
    control,
    detect_obstacle,
    find_lane_center,
    find_target,
    load_tuning_config,
    preprocess,
)


REG_CTRL = 0x00
REG_STEP_HIGH_TICKS = 0x04
REG_WHEEL_A_PERIOD_TICKS = 0x08
REG_WHEEL_B_PERIOD_TICKS = 0x0C
REG_WHEEL_C_PERIOD_TICKS = 0x10
REG_WHEEL_D_PERIOD_TICKS = 0x14
REG_DIR_BITS = 0x18
REG_STATUS = 0x1C
REG_VERSION = 0x20
REG_HEARTBEAT = 0x24

CTRL_DISABLE = 0x00
CTRL_RUN = 0x09
CTRL_HOLD = 0x0B
EXPECTED_VERSION = 0x59590200

# Calibrated on the current mecanum car: positive wheel command means
# A/B DIR low and C/D DIR high.
POSITIVE_DIR_HIGH = (False, False, True, True)


def clamp(value, low, high):
    return max(low, min(high, value))


class AxiMotion:
    def __init__(self, base_addr, span=0x1000):
        self.base_addr = base_addr
        self.span = span
        self.fd = None
        self.mem = None
        self.running = False

    def open(self):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.mem = mmap.mmap(
            self.fd,
            self.span,
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE,
            offset=self.base_addr,
        )
        return self

    def close(self):
        if self.mem is not None:
            self.mem.close()
            self.mem = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read_reg(self, offset):
        return struct.unpack_from("<I", self.mem, offset)[0]

    def write_reg(self, offset, value):
        struct.pack_into("<I", self.mem, offset, value & 0xFFFFFFFF)

    def hold(self):
        self.write_reg(REG_CTRL, CTRL_HOLD)
        for off in (
            REG_WHEEL_A_PERIOD_TICKS,
            REG_WHEEL_B_PERIOD_TICKS,
            REG_WHEEL_C_PERIOD_TICKS,
            REG_WHEEL_D_PERIOD_TICKS,
        ):
            self.write_reg(off, 0)
        self.write_reg(REG_CTRL, CTRL_DISABLE)
        self.running = False

    def write_motion(self, step_high_ticks, periods, dir_bits):
        # Do not disable the pulse generator on every control update. Repeated
        # stop/start at low control_hz makes the stepper motors jerk.
        if not self.running:
            self.write_reg(REG_CTRL, CTRL_HOLD)
        self.write_reg(REG_STEP_HIGH_TICKS, step_high_ticks)
        self.write_reg(REG_WHEEL_A_PERIOD_TICKS, periods[0])
        self.write_reg(REG_WHEEL_B_PERIOD_TICKS, periods[1])
        self.write_reg(REG_WHEEL_C_PERIOD_TICKS, periods[2])
        self.write_reg(REG_WHEEL_D_PERIOD_TICKS, periods[3])
        self.write_reg(REG_DIR_BITS, dir_bits)
        self.write_reg(REG_CTRL, CTRL_RUN)
        self.running = True


class SharedJpegCamera:
    def __init__(self, path, timeout_s=0.5, poll_s=0.005):
        self.path = path
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self.last_mtime_ns = 0
        self.last_frame = None

    def get_frame(self, _frame_id):
        deadline = time.monotonic() + max(0.05, self.timeout_s)
        last_error = None
        while time.monotonic() < deadline:
            try:
                stat = os.stat(self.path)
                if stat.st_mtime_ns != self.last_mtime_ns or self.last_frame is None:
                    frame = cv2.imread(self.path, cv2.IMREAD_COLOR)
                    if frame is not None and frame.size:
                        self.last_mtime_ns = stat.st_mtime_ns
                        self.last_frame = frame
                        return frame
            except OSError as exc:
                last_error = exc
            time.sleep(self.poll_s)

        if self.last_frame is not None:
            return self.last_frame.copy()
        raise RuntimeError("shared camera frame timeout path=%s error=%s" % (self.path, last_error))

    def stop(self):
        pass


def pack_dir_bits(wheel_cmds):
    bits = 0
    for idx, cmd in enumerate(wheel_cmds):
        positive_high = POSITIVE_DIR_HIGH[idx]
        dir_high = cmd >= 0 if positive_high else cmd < 0
        if dir_high:
            bits |= 1 << idx
    return bits


def wheel_cmd_to_period(cmd, cmd_limit, min_period, max_period, deadband):
    magnitude = abs(float(cmd)) / float(max(1, cmd_limit))
    if magnitude < deadband:
        return 0
    magnitude = clamp(magnitude, 0.0, 1.0)
    return int(round(max_period - (max_period - min_period) * magnitude))


def wheel_cmds_to_periods(wheel_cmds, cmd_limit, min_period, max_period, deadband):
    return tuple(
        wheel_cmd_to_period(cmd, cmd_limit, min_period, max_period, deadband)
        for cmd in wheel_cmds
    )


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Camera line-follow to AXI motor control.")
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x40000000)
    parser.add_argument("--bitfile", default="/home/xilinx/jupyter_notebooks/ov5640_audio_mecanum_hp1.bit")
    parser.add_argument("--config", default="/home/xilinx/line_follow/line_follow_fast_tuning.json")
    parser.add_argument("--reload-config", action="store_true")
    parser.add_argument("--config-check-frames", type=int, default=15)
    parser.add_argument("--camera-source", choices=("board", "shared-jpeg"), default="board")
    parser.add_argument("--shared-frame", default="/dev/shm/car_runtime/latest_frame.jpg")
    parser.add_argument("--shared-frame-timeout", type=float, default=0.5)
    parser.add_argument("--track-mode", default="fast_red_white")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--roi-y-ratio", type=float, default=0.55)
    parser.add_argument("--morph-kernel", type=int, default=3)
    parser.add_argument("--lookahead-window", type=int, default=4)
    parser.add_argument("--margin", type=int, default=32)
    parser.add_argument("--minpix", type=int, default=25)
    parser.add_argument("--nwindows", type=int, default=8)
    parser.add_argument("--filter-alpha", type=float, default=0.35)
    parser.add_argument("--predict-frames", type=int, default=5)
    parser.add_argument("--kp", type=float, default=1.2)
    parser.add_argument("--kd", type=float, default=0.35)
    parser.add_argument("--base-vy", type=int, default=80)
    parser.add_argument("--slow-vy", type=int, default=45)
    parser.add_argument("--lost-vy", type=int, default=0)
    parser.add_argument("--lost-search-vy", type=int, default=0)
    parser.add_argument("--lost-search-wz", type=int, default=0)
    parser.add_argument("--wheel-limit", type=int, default=220)
    parser.add_argument("--lost-estop-frames", type=int, default=8)
    parser.add_argument("--camera-center-offset", type=int, default=0)
    parser.add_argument("--lane-width-px", type=int, default=0)
    parser.add_argument("--heading-gain", type=float, default=0.45)
    parser.add_argument("--command-smoothing", type=float, default=0.35)
    parser.add_argument("--command-delta-limit", type=int, default=0)
    parser.add_argument("--obstacle-mode", default="off")
    parser.add_argument("--step-high", type=int, default=500)
    parser.add_argument("--min-period", type=int, default=650000)
    parser.add_argument("--max-period", type=int, default=1200000)
    parser.add_argument("--deadband", type=float, default=0.04)
    parser.add_argument("--control-hz", type=float, default=8.0)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--fast-camera-read", action="store_true", default=True)
    parser.add_argument("--normal-camera-read", action="store_true")
    parser.add_argument("--drive", action="store_true", help="actually write AXI motor registers")
    parser.add_argument("--dry-run", action="store_true", help="print only; overrides --drive")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.normal_camera_read:
        args.fast_camera_read = False

    if os.geteuid() != 0:
        print("ERROR: please run with sudo; camera and /dev/mem need root.")
        return 2

    config_mtime, _ = load_tuning_config(args.config, args, quiet=True)
    write_axi = args.drive and not args.dry_run

    axi = None
    stop_requested = False

    def force_hold_now():
        if axi is not None:
            try:
                axi.hold()
            except Exception as exc:
                print("AXI_HOLD_FAILED error=%s" % exc)

    def request_stop(signum, _frame):
        nonlocal stop_requested
        stop_requested = True
        force_hold_now()
        print("STOP_SIGNAL signum=%d" % signum)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    if write_axi:
        axi = AxiMotion(args.base).open()
        version = axi.read_reg(REG_VERSION)
        print("AXI_VERSION=0x%08X" % version)
        if version != EXPECTED_VERSION:
            print("ERROR: motor IP version mismatch, refuse to drive.")
            axi.close()
            return 3
        axi.hold()
        atexit.register(force_hold_now)

    if args.camera_source == "shared-jpeg":
        camera = SharedJpegCamera(args.shared_frame, args.shared_frame_timeout)
        print("CAMERA_INIT_SHARED_JPEG path=%s timeout=%.3f" % (args.shared_frame, args.shared_frame_timeout))
    else:
        camera = import_board_camera(False, args.bitfile, args.fast_camera_read)
    target_filter = TargetFilter(alpha=args.filter_alpha, max_predict_frames=args.predict_frames)

    print(
        "LINE_FOLLOW_DRIVE_BEGIN write_axi=%d camera_source=%s track_mode=%s size=%dx%d control_hz=%.2f duration=%.1f"
        % (
            1 if write_axi else 0,
            args.camera_source,
            args.track_mode,
            args.width,
            args.height,
            args.control_hz,
            args.duration,
        )
    )
    print("SAFETY: lift wheels for first --drive test; keep one hand near motor power switch.")

    frame_id = 0
    last_error = 0
    lost_frames = 0
    smoothed_wheels = None
    start = time.monotonic()
    interval = 1.0 / max(1.0, args.control_hz)

    try:
        while True:
            if stop_requested:
                print("STOP_REQUESTED")
                break
            loop_t = time.monotonic()
            if args.duration > 0 and loop_t - start >= args.duration:
                print("DURATION_DONE")
                break
            if args.max_frames > 0 and frame_id >= args.max_frames:
                print("MAX_FRAMES_DONE")
                break

            frame_id += 1
            frame = camera.get_frame(frame_id)
            if frame.shape[1] != args.width or frame.shape[0] != args.height:
                frame = cv2.resize(frame, (args.width, args.height))

            if args.reload_config and args.config and frame_id % max(1, args.config_check_frames) == 0:
                config_mtime, changed = load_tuning_config(args.config, args, config_mtime, quiet=True)
                if changed:
                    target_filter.alpha = args.filter_alpha
                    target_filter.max_predict_frames = args.predict_frames
                    interval = 1.0 / max(1.0, args.control_hz)

            roi_y, binary, prep_info = preprocess(
                frame,
                track_mode=args.track_mode,
                roi_y_ratio=args.roi_y_ratio,
                morph_kernel=args.morph_kernel,
                return_debug=True,
            )
            if args.track_mode in ("fast_bright", "fast_blue_white"):
                found_raw, raw_target_x, centers, confidence = find_lane_center(
                    binary,
                    minpix=args.minpix,
                    expected_center_x=args.width // 2 + args.camera_center_offset,
                    expected_lane_width=args.lane_width_px,
                    heading_gain=args.heading_gain,
                )
            else:
                found_raw, raw_target_x, centers, confidence = find_target(
                    binary,
                    lookahead_window=args.lookahead_window,
                    margin=args.margin,
                    minpix=args.minpix,
                    nwindows=args.nwindows,
                )
            found, target_x, target_source, filter_lost_frames = target_filter.update(
                found_raw, raw_target_x, args.width
            )
            obstacle, obstacle_ratio = detect_obstacle(frame, mode=args.obstacle_mode)

            out = control(
                target_x,
                found,
                args.width,
                last_error,
                lost_frames,
                target_source=target_source,
                obstacle_detected=obstacle,
                base_vy_cmd=args.base_vy,
                slow_vy_cmd=args.slow_vy,
                lost_vy_cmd=args.lost_vy,
                lost_search_vy_cmd=args.lost_search_vy,
                lost_search_wz_cmd=args.lost_search_wz,
                kp=args.kp,
                kd=args.kd,
                wheel_cmd_limit=args.wheel_limit,
                lost_line_frames_to_estop=args.lost_estop_frames,
                camera_center_offset=args.camera_center_offset,
            )
            last_error = out["last_error"]
            lost_frames = filter_lost_frames
            alpha = clamp(args.command_smoothing, 0.0, 1.0)
            raw_wheels = tuple(out["wheel_cmds"])
            previous_wheels = smoothed_wheels
            if out["hold_request"] or out["estop_request"]:
                smoothed_wheels = raw_wheels
            elif smoothed_wheels is None:
                previous_wheels = (0, 0, 0, 0) if args.command_delta_limit > 0 else None
                smoothed_wheels = raw_wheels
            elif alpha > 0.0:
                smoothed_wheels = tuple(
                    int(round((1.0 - alpha) * prev + alpha * cur))
                    for prev, cur in zip(smoothed_wheels, raw_wheels)
                )
            else:
                smoothed_wheels = raw_wheels
            if args.command_delta_limit > 0 and smoothed_wheels is not None:
                limit = int(args.command_delta_limit)
                if previous_wheels is not None:
                    smoothed_wheels = tuple(
                        int(prev + clamp(cur - prev, -limit, limit))
                        for prev, cur in zip(previous_wheels, smoothed_wheels)
                    )
            out["wheel_cmds"] = smoothed_wheels

            periods = wheel_cmds_to_periods(
                out["wheel_cmds"],
                args.wheel_limit,
                args.min_period,
                args.max_period,
                args.deadband,
            )
            dir_bits = pack_dir_bits(out["wheel_cmds"])

            if out["hold_request"] or out["estop_request"] or max(periods) == 0:
                if write_axi:
                    axi.hold()
            elif write_axi:
                axi.write_motion(args.step_high, periods, dir_bits)

            elapsed = time.monotonic() - loop_t
            print(
                "frame=%d found=%d raw=%d target=%d src=%s err=%d vy=%d wz=%d "
                "wheel=%s period=%s dir=0x%X hold=%d estop=%d obs=%d conf=%.3f "
                "threshold=%s score=%.3f loop_ms=%.1f"
                % (
                    frame_id,
                    1 if found else 0,
                    raw_target_x,
                    target_x,
                    target_source,
                    out["error"],
                    out["vy_cmd"],
                    out["wz_cmd"],
                    list(out["wheel_cmds"]),
                    periods,
                    dir_bits,
                    1 if out["hold_request"] else 0,
                    1 if out["estop_request"] else 0,
                    1 if obstacle else 0,
                    confidence,
                    prep_info.get("threshold_mode", "unknown"),
                    prep_info.get("threshold_score", 0.0),
                    elapsed * 1000.0,
                )
            )

            sleep_s = interval - (time.monotonic() - loop_t)
            if sleep_s > 0:
                time.sleep(sleep_s)

    except KeyboardInterrupt:
        print("STOP_BY_CTRL_C")
    finally:
        if axi is not None:
            axi.hold()
            axi.close()
            print("AXI_HOLD_DONE")
        try:
            camera.stop()
        except Exception:
            pass
        print("LINE_FOLLOW_DRIVE_END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

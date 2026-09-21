#!/usr/bin/env python3
"""
PYNQ 端远程遥控接收示例。

默认 dry-run，只打印收到的遥控数据和转换后的四轮参数。
只有显式加 --write-axi 时，才会通过 /dev/mem 写 AXI-Lite 电机控制 IP。

关键安全原则：
1. 遥控同学只给“期望值”：manual_enable、vx、vy、wz、speed、estop。
2. 最终速度上限必须在本运动控制端再次限制，不能完全相信外部输入。
3. 遥控超时后不能继续执行最后一帧命令，必须进入 hold/stop。
"""

import argparse
import json
import mmap
import os
import socket
import struct
import subprocess
import time


REG_CTRL = 0x00
REG_STEP_HIGH_TICKS = 0x04
REG_WHEEL_A_PERIOD_TICKS = 0x08
REG_WHEEL_B_PERIOD_TICKS = 0x0C
REG_WHEEL_C_PERIOD_TICKS = 0x10
REG_WHEEL_D_PERIOD_TICKS = 0x14
REG_DIR_BITS = 0x18
REG_STATUS = 0x1C
REG_VERSION = 0x20

CTRL_DISABLE = 0x00
CTRL_RUN = 0x09
CTRL_HOLD = 0x0B
CTRL_ESTOP = 0x0C

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

STOP_ACTIONS = {"STOP", "HOLD", "STOP_AUTO", "STOP_LINE_FOLLOW"}
LINE_FOLLOW_PROCESS_PATTERN = "[l]ine_follow_drive.py|[r]un_line_follow_safe.sh"

# 实车方向标定结果：正向命令时 A/B 的 DIR 为 0，C/D 的 DIR 为 1。
POSITIVE_DIR_HIGH = (False, False, True, True)


def clamp(value, low, high):
    return max(low, min(high, value))


class AxiMotion:
    def __init__(self, base_addr, span=0x1000):
        self.base_addr = base_addr
        self.span = span
        self.fd = None
        self.mem = None
        self.last_motion = None
        self.state = "disabled"
        self.motion_writes = 0
        self.motion_skips = 0

    def open(self):
        self.fd = open("/dev/mem", "r+b", buffering=0)
        self.mem = mmap.mmap(self.fd.fileno(), self.span, offset=self.base_addr)
        return self

    def close(self):
        if self.mem is not None:
            self.mem.close()
            self.mem = None
        if self.fd is not None:
            self.fd.close()
            self.fd = None

    def read_reg(self, offset):
        self.mem.seek(offset)
        return struct.unpack("<I", self.mem.read(4))[0]

    def write_reg(self, offset, value):
        self.mem.seek(offset)
        self.mem.write(struct.pack("<I", value & 0xFFFFFFFF))

    def write_hold(self, force=False):
        if not force and self.state == "hold":
            return False
        self.write_reg(REG_CTRL, CTRL_HOLD)
        self.write_reg(REG_WHEEL_A_PERIOD_TICKS, 0)
        self.write_reg(REG_WHEEL_B_PERIOD_TICKS, 0)
        self.write_reg(REG_WHEEL_C_PERIOD_TICKS, 0)
        self.write_reg(REG_WHEEL_D_PERIOD_TICKS, 0)
        self.last_motion = None
        self.state = "hold"
        return True

    def write_estop(self, force=False):
        if not force and self.state == "estop":
            return False
        self.write_reg(REG_CTRL, CTRL_ESTOP)
        self.write_reg(REG_WHEEL_A_PERIOD_TICKS, 0)
        self.write_reg(REG_WHEEL_B_PERIOD_TICKS, 0)
        self.write_reg(REG_WHEEL_C_PERIOD_TICKS, 0)
        self.write_reg(REG_WHEEL_D_PERIOD_TICKS, 0)
        self.last_motion = None
        self.state = "estop"
        return True

    def write_motion(self, step_high_ticks, periods, dir_bits, direction_change_hold_s=0.02):
        motion = (int(step_high_ticks), tuple(int(v) for v in periods), int(dir_bits))
        if self.state == "run" and self.last_motion == motion:
            self.motion_skips += 1
            return False

        if (
            self.state == "run"
            and self.last_motion is not None
            and self.last_motion[2] != motion[2]
            and direction_change_hold_s > 0
        ):
            self.write_reg(REG_CTRL, CTRL_HOLD)
            time.sleep(direction_change_hold_s)

        self.write_reg(REG_STEP_HIGH_TICKS, step_high_ticks)
        self.write_reg(REG_WHEEL_A_PERIOD_TICKS, periods[0])
        self.write_reg(REG_WHEEL_B_PERIOD_TICKS, periods[1])
        self.write_reg(REG_WHEEL_C_PERIOD_TICKS, periods[2])
        self.write_reg(REG_WHEEL_D_PERIOD_TICKS, periods[3])
        self.write_reg(REG_DIR_BITS, dir_bits)
        self.write_reg(REG_CTRL, CTRL_RUN)
        self.last_motion = motion
        self.state = "run"
        self.motion_writes += 1
        return True


def normalize_packet(packet, max_linear, max_rotate):
    """把遥控输入转换成已经限速的 vx/vy/wz。

    输入约定：遥控端给 -1~1 的归一化期望值和 0~1 的 speed。
    输出约定：运动控制端根据本地 max_linear/max_rotate 做最终安全上限。
    """
    action = str(packet.get("action", "")).upper()
    speed = clamp(float(packet.get("speed", 1.0)), 0.0, 1.0)

    if action in ACTION_TO_VECTOR:
        vx, vy, wz = ACTION_TO_VECTOR[action]
    else:
        vx = clamp(float(packet.get("vx", 0.0)), -1.0, 1.0)
        vy = clamp(float(packet.get("vy", 0.0)), -1.0, 1.0)
        wz = clamp(float(packet.get("wz", 0.0)), -1.0, 1.0)
        action = "VECTOR"

    vx = clamp(vx * speed, -1.0, 1.0) * max_linear
    vy = clamp(vy * speed, -1.0, 1.0) * max_linear
    wz = clamp(wz * speed, -1.0, 1.0) * max_rotate
    return vx, vy, wz, action, speed


def split_to_wheels(vx, vy, wz):
    # 麦轮运动学简化式：vx 左右平移，vy 前后，wz 旋转。
    wheel_cmds = [
        -vx + vy - wz,
        +vx + vy - wz,
        -vx + vy + wz,
        +vx + vy + wz,
    ]

    # 三个方向叠加后可能超过 1，这里按比例归一化，保留方向和比例关系。
    max_abs = max(abs(v) for v in wheel_cmds)
    if max_abs > 1.0:
        wheel_cmds = [v / max_abs for v in wheel_cmds]
    return tuple(wheel_cmds)


def pack_dir_bits(wheel_cmds):
    bits = 0
    for i, cmd in enumerate(wheel_cmds):
        positive_high = POSITIVE_DIR_HIGH[i]
        dir_high = cmd >= 0 if positive_high else cmd < 0
        if dir_high:
            bits |= 1 << i
    return bits


def wheel_cmd_to_period(cmd, min_period, max_period, deadband):
    magnitude = abs(cmd)
    if magnitude < deadband:
        return 0
    magnitude = clamp(magnitude, 0.0, 1.0)
    # 速度越大，period_ticks 越小，STEP 频率越高。
    return int(max_period - (max_period - min_period) * magnitude)


def call_quiet(cmd):
    """执行系统命令，隐藏正常输出，只返回退出码。"""
    try:
        return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"CMD_FAILED cmd={cmd!r} error={exc}")
        return 127


def is_line_follow_running(process_pattern=LINE_FOLLOW_PROCESS_PATTERN):
    """检查循迹脚本或循迹主程序是否已经在后台运行。"""
    return call_quiet(["pgrep", "-f", process_pattern]) == 0


def force_stop_car(python_bin, stop_car_script):
    """通过 stop_car.py 再写一次 HOLD，避免硬件保持上一帧 RUN。"""
    if not os.path.exists(stop_car_script):
        print(f"STOP_CAR_MISSING path={stop_car_script}")
        return False
    return call_quiet([python_bin, "-B", stop_car_script]) == 0


def stop_line_follow(
    python_bin,
    stop_car_script,
    force_motor_stop=True,
    process_pattern=LINE_FOLLOW_PROCESS_PATTERN,
):
    """停止循迹进程；必要时再调用 stop_car.py 让 AXI 输出进入 HOLD。"""
    was_running = is_line_follow_running(process_pattern)
    if was_running:
        call_quiet(["pkill", "-TERM", "-f", process_pattern])
        time.sleep(0.25)
        if is_line_follow_running(process_pattern):
            call_quiet(["pkill", "-KILL", "-f", process_pattern])
            time.sleep(0.10)

    if force_motor_stop:
        force_stop_car(python_bin, stop_car_script)

    return was_running


def start_line_follow(
    line_follow_script,
    line_follow_log,
    process_pattern=LINE_FOLLOW_PROCESS_PATTERN,
):
    """后台启动循迹脚本；循迹日志写入文件，避免刷屏影响遥控接收。"""
    if is_line_follow_running(process_pattern):
        return "line-follow-already-running"
    if not os.path.exists(line_follow_script):
        return "line-follow-script-missing"

    log_dir = os.path.dirname(line_follow_log)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    with open(line_follow_log, "ab", buffering=0) as log_file:
        proc = subprocess.Popen(
            [line_follow_script],
            cwd=os.path.dirname(line_follow_script) or None,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return f"line-follow-started-pid-{proc.pid}"


def main():
    parser = argparse.ArgumentParser(description="Receive remote-control UDP packets.")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=50009)
    parser.add_argument("--base", type=lambda x: int(x, 0), default=0x40000000)
    parser.add_argument("--timeout-ms", type=int, default=400)
    parser.add_argument("--step-high", type=int, default=500)
    parser.add_argument("--min-period", type=int, default=200000)
    parser.add_argument("--max-period", type=int, default=900000)
    parser.add_argument("--max-linear", type=float, default=0.40)
    parser.add_argument("--max-rotate", type=float, default=0.30)
    parser.add_argument("--accel-linear", type=float, default=0.0)
    parser.add_argument("--accel-rotate", type=float, default=0.0)
    parser.add_argument("--deadband", type=float, default=0.03)
    parser.add_argument("--python-bin", default="/opt/python3.6/bin/python3.6")
    parser.add_argument("--line-follow-script", default="/home/xilinx/line_follow/run_line_follow_safe.sh")
    parser.add_argument("--line-follow-log", default="/home/xilinx/line_follow/line_follow_auto.log")
    parser.add_argument("--stop-car-script", default="/home/xilinx/line_follow/stop_car.py")
    parser.add_argument("--line-follow-process-pattern", default=LINE_FOLLOW_PROCESS_PATTERN)
    parser.add_argument("--dry-run", action="store_true", help="print only; do not write AXI")
    parser.add_argument("--write-axi", action="store_true", help="write AXI registers")
    args = parser.parse_args()

    write_axi = args.write_axi and not args.dry_run
    axi = None
    if write_axi:
        axi = AxiMotion(args.base).open()
        print(f"AXI_VERSION=0x{axi.read_reg(REG_VERSION):08X}")
        axi.write_hold()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.05)

    last_rx = time.monotonic()
    last_active = False
    control_owner = "idle"
    estop_latched = False
    print(
        f"REMOTE_RECEIVER_BEGIN bind={args.bind}:{args.port} write_axi={write_axi} "
        f"max_linear={args.max_linear} max_rotate={args.max_rotate}"
    )

    try:
        while True:
            now = time.monotonic()
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                if last_active and (now - last_rx) * 1000.0 > args.timeout_ms:
                    print("REMOTE_TIMEOUT -> HOLD")
                    if write_axi:
                        axi.write_hold()
                    last_active = False
                continue

            last_rx = time.monotonic()
            text = data.decode("utf-8", errors="replace").strip()
            try:
                packet = json.loads(text)
            except json.JSONDecodeError:
                print(f"BAD_PACKET from={addr} payload={text!r}")
                continue

            raw_action = str(packet.get("action", "")).upper()
            manual_enable = int(packet.get("manual_enable", 0)) != 0
            auto_request = raw_action in AUTO_ACTIONS
            clear_estop = raw_action in CLEAR_ESTOP_ACTIONS
            stop_request = raw_action in STOP_ACTIONS
            estop = int(packet.get("estop", 0)) != 0 or raw_action == "ESTOP"

            if estop:
                if write_axi and (
                    control_owner == "line_follow"
                    or is_line_follow_running(args.line_follow_process_pattern)
                ):
                    stopped = stop_line_follow(
                        args.python_bin,
                        args.stop_car_script,
                        force_motor_stop=False,
                        process_pattern=args.line_follow_process_pattern,
                    )
                    print(f"LINE_FOLLOW_STOPPED_BY_ESTOP stopped={int(stopped)}")
                if write_axi:
                    axi_event = "estop-write" if axi.write_estop(force=True) else "estop-skip"
                else:
                    axi_event = "dry-estop"
                control_owner = "estop"
                estop_latched = True
                last_active = False
                print(
                    f"RX from={addr} seq={packet.get('seq')} manual={int(manual_enable)} "
                    f"action={raw_action or 'ESTOP'} owner=estop estop=1 axi={axi_event}"
                )
                continue

            if clear_estop:
                if write_axi:
                    axi_event = "hold-write" if axi.write_hold(force=True) else "hold-skip"
                else:
                    axi_event = "dry-clear-estop"
                control_owner = "idle"
                estop_latched = False
                last_active = False
                print(
                    f"RX from={addr} seq={packet.get('seq')} manual={int(manual_enable)} "
                    f"action={raw_action} owner=idle estop=0 axi={axi_event}"
                )
                continue

            if estop_latched:
                if write_axi:
                    axi.write_estop()
                last_active = False
                print(
                    f"RX_IGNORED_ESTOP_LATCHED from={addr} seq={packet.get('seq')} "
                    f"manual={int(manual_enable)} action={raw_action or 'VECTOR'} owner=estop"
                )
                continue

            if auto_request:
                if write_axi:
                    axi.write_hold(force=True)
                    start_event = start_line_follow(
                        args.line_follow_script,
                        args.line_follow_log,
                        args.line_follow_process_pattern,
                    )
                    if start_event.startswith("line-follow-started") or start_event == "line-follow-already-running":
                        control_owner = "line_follow"
                else:
                    start_event = "dry-line-follow-start"
                last_active = False
                print(
                    f"RX from={addr} seq={packet.get('seq')} manual={int(manual_enable)} "
                    f"action={raw_action} owner={control_owner} axi={start_event}"
                )
                continue

            if stop_request or manual_enable:
                if write_axi and (
                    control_owner == "line_follow"
                    or is_line_follow_running(args.line_follow_process_pattern)
                ):
                    stopped = stop_line_follow(
                        args.python_bin,
                        args.stop_car_script,
                        force_motor_stop=write_axi,
                        process_pattern=args.line_follow_process_pattern,
                    )
                    print(f"LINE_FOLLOW_STOPPED_BY_REMOTE stopped={int(stopped)} action={raw_action}")
                control_owner = "idle" if stop_request else "remote"

            if (not manual_enable) and (not estop) and (not stop_request) and control_owner == "line_follow":
                print(
                    f"RX from={addr} seq={packet.get('seq')} manual=0 "
                    f"action={raw_action or 'IDLE'} owner=line_follow axi=line-follow-owned"
                )
                continue

            vx, vy, wz, action, speed = normalize_packet(packet, args.max_linear, args.max_rotate)
            wheel_cmds = split_to_wheels(vx, vy, wz)
            periods = tuple(
                wheel_cmd_to_period(c, args.min_period, args.max_period, args.deadband)
                for c in wheel_cmds
            )
            dir_bits = pack_dir_bits(wheel_cmds)
            axi_event = "dry"

            if not manual_enable or max(abs(vx), abs(vy), abs(wz)) < args.deadband:
                if write_axi:
                    axi_event = "hold-write" if axi.write_hold() else "hold-skip"
                last_active = False
            else:
                if write_axi:
                    axi_event = "motion-write" if axi.write_motion(args.step_high, periods, dir_bits) else "motion-skip"
                last_active = True

            print(
                f"RX from={addr} seq={packet.get('seq')} manual={int(manual_enable)} "
                f"action={action} owner={control_owner} speed={speed:.2f} "
                f"vx={vx:.2f} vy={vy:.2f} wz={wz:.2f} "
                f"wheel={[round(c, 2) for c in wheel_cmds]} period={periods} "
                f"dir=0x{dir_bits:X} estop={int(estop)} axi={axi_event}"
            )

    except KeyboardInterrupt:
        print("\nREMOTE_RECEIVER_STOP_BY_CTRL_C")
    finally:
        if write_axi and control_owner == "line_follow":
            stop_line_follow(
                args.python_bin,
                args.stop_car_script,
                force_motor_stop=write_axi,
                process_pattern=args.line_follow_process_pattern,
            )
        if write_axi and axi is not None:
            axi.write_hold()
            axi.close()
        sock.close()
        print("REMOTE_RECEIVER_END")


if __name__ == "__main__":
    main()

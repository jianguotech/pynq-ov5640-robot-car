#!/usr/bin/env python3
import argparse
import mmap
import os
import struct
import sys
import time


BASE_DEFAULT = 0x40000000
MAP_SIZE = 0x1000

REG_CTRL = 0x00
REG_STEP_HIGH = 0x04
REG_A_PERIOD = 0x08
REG_B_PERIOD = 0x0C
REG_C_PERIOD = 0x10
REG_D_PERIOD = 0x14
REG_DIR = 0x18
REG_STATUS = 0x1C
REG_VERSION = 0x20
REG_HEARTBEAT = 0x24

CTRL_RUN = 0x09
CTRL_HOLD = 0x0B
CTRL_DISABLE = 0x00

EXPECTED_VERSION = 0x59590200

DIR_ACTIONS = {
    "forward": 0x0C,
    "backward": 0x03,
    "left": 0x06,
    "right": 0x09,
    "rotate_left": 0x0F,
    "rotate_right": 0x00,
}


def read_reg(mem, off):
    return struct.unpack_from("<I", mem, off)[0]


def write_reg(mem, off, val):
    struct.pack_into("<I", mem, off, val & 0xFFFFFFFF)


def force_stop(mem):
    write_reg(mem, REG_CTRL, CTRL_HOLD)
    for off in (REG_A_PERIOD, REG_B_PERIOD, REG_C_PERIOD, REG_D_PERIOD):
        write_reg(mem, off, 0)
    time.sleep(0.05)
    write_reg(mem, REG_CTRL, CTRL_DISABLE)


def load_bitstream(bitfile):
    print("LOAD_BITSTREAM path=%s" % bitfile)
    try:
        from pynq import Bitstream
        Bitstream(bitfile).download()
        print("LOAD_BITSTREAM_OK")
        return True
    except Exception as exc:
        print("LOAD_BITSTREAM_FAILED error=%s" % exc)
        return False


def open_mem(base):
    fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
    mem = mmap.mmap(
        fd,
        MAP_SIZE,
        mmap.MAP_SHARED,
        mmap.PROT_READ | mmap.PROT_WRITE,
        offset=base,
    )
    return fd, mem


def print_status(mem):
    version = read_reg(mem, REG_VERSION)
    status = read_reg(mem, REG_STATUS)
    ctrl = read_reg(mem, REG_CTRL)
    heartbeat1 = read_reg(mem, REG_HEARTBEAT)
    time.sleep(0.2)
    heartbeat2 = read_reg(mem, REG_HEARTBEAT)
    heartbeat_delta = (heartbeat2 - heartbeat1) & 0xFFFFFFFF
    print("VERSION=0x%08X" % version)
    print("CTRL=0x%08X STATUS=0x%08X" % (ctrl, status))
    print("HEARTBEAT_DELTA_200MS=%d" % heartbeat_delta)
    print("VERSION_OK=%d" % (1 if version == EXPECTED_VERSION else 0))
    return version == EXPECTED_VERSION


def run_action(mem, name, dir_bits, period, step_high, seconds, pause):
    print(
        "MOVE_BEGIN action=%s dir=0x%X period=%d seconds=%.3f"
        % (name, dir_bits, period, seconds)
    )
    write_reg(mem, REG_CTRL, CTRL_DISABLE)
    write_reg(mem, REG_STEP_HIGH, step_high)
    for off in (REG_A_PERIOD, REG_B_PERIOD, REG_C_PERIOD, REG_D_PERIOD):
        write_reg(mem, off, period)
    write_reg(mem, REG_DIR, dir_bits)
    write_reg(mem, REG_CTRL, CTRL_HOLD)
    time.sleep(0.10)
    write_reg(mem, REG_CTRL, CTRL_RUN)
    print(
        "RUN_STATUS action=%s CTRL=0x%08X STATUS=0x%08X"
        % (name, read_reg(mem, REG_CTRL), read_reg(mem, REG_STATUS))
    )
    time.sleep(max(0.0, seconds))
    force_stop(mem)
    print(
        "MOVE_END action=%s CTRL=0x%08X STATUS=0x%08X"
        % (name, read_reg(mem, REG_CTRL), read_reg(mem, REG_STATUS))
    )
    if pause > 0:
        time.sleep(pause)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Quick PYNQ mecanum motor test.")
    parser.add_argument(
        "--action",
        default="status",
        choices=["status", "all"] + sorted(DIR_ACTIONS.keys()),
        help="status only reads registers; other actions move the car briefly",
    )
    parser.add_argument("--base", default=hex(BASE_DEFAULT), help="AXI base address")
    parser.add_argument("--period", type=int, default=900000, help="larger is slower")
    parser.add_argument("--seconds", type=float, default=1.0)
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--step-high", type=int, default=500)
    parser.add_argument(
        "--load-bit",
        action="store_true",
        help="load the merged bitstream before testing",
    )
    parser.add_argument(
        "--bitfile",
        default="/home/xilinx/jupyter_notebooks/ov5640_audio_mecanum_hp1.bit",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    base = int(args.base, 0)

    if os.geteuid() != 0:
        print("ERROR: please run with sudo, because /dev/mem needs root.")
        return 2

    if args.load_bit:
        load_bitstream(args.bitfile)
        time.sleep(0.3)

    fd, mem = open_mem(base)
    try:
        print("MOTOR_QUICK_TEST_BEGIN")
        print("AXI_BASE=0x%08X" % base)
        ok = print_status(mem)
        if args.action == "status":
            return 0 if ok else 3

        if not ok:
            print("ERROR: motor IP version mismatch; refuse to move.")
            return 3

        if args.action == "all":
            actions = [
                "forward",
                "backward",
                "right",
                "left",
                "rotate_right",
                "rotate_left",
            ]
        else:
            actions = [args.action]

        print("SAFETY: keep one hand near the motor power switch.")
        for name in actions:
            run_action(
                mem,
                name,
                DIR_ACTIONS[name],
                args.period,
                args.step_high,
                args.seconds,
                args.pause,
            )
        return 0
    finally:
        try:
            force_stop(mem)
            print(
                "FINAL_STOP CTRL=0x%08X STATUS=0x%08X"
                % (read_reg(mem, REG_CTRL), read_reg(mem, REG_STATUS))
            )
            print("MOTOR_QUICK_TEST_END")
        finally:
            mem.close()
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())

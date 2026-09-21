#!/usr/bin/env python3
import argparse
import json
import os
import sys

import cv2
import numpy as np


DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480
DEFAULT_FPS = 30

TRACK_MODES = (
    "auto", "gray", "dark", "bright", "red", "blue", "yellow",
    "fast_dark", "fast_bright", "fast_gray", "fast_auto",
    "fast_red", "fast_white", "fast_red_white", "fast_blue_white",
    "fast_white_line_left", "fast_white_line_right", "fast_white_line_nearest",
)
OBSTACLE_MODES = ("off", "dark", "red")
CONFIG_KEYS = {
    "track_mode",
    "width",
    "height",
    "fps",
    "roi_y_ratio",
    "morph_kernel",
    "lookahead_window",
    "margin",
    "minpix",
    "nwindows",
    "filter_alpha",
    "predict_frames",
    "kp",
    "kd",
    "base_vy",
    "slow_vy",
    "lost_vy",
    "lost_search_vy",
    "lost_search_wz",
    "wheel_limit",
    "lost_estop_frames",
    "obstacle_mode",
    "step_high",
    "min_period",
    "max_period",
    "deadband",
    "control_hz",
    "camera_center_offset",
    "lane_width_px",
    "heading_gain",
    "command_smoothing",
    "command_delta_limit",
}


def clamp(value, low, high):
    return max(low, min(high, value))


def clamp_cmd(value, limit):
    return int(max(-limit, min(limit, value)))


def load_tuning_config(path, args, last_mtime=None, quiet=False):
    if not path:
        return last_mtime, False

    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        if not quiet:
            print(f"CONFIG_MISSING path={path} error={exc}", file=sys.stderr)
        return last_mtime, False

    if last_mtime is not None and mtime <= last_mtime:
        return last_mtime, False

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"CONFIG_LOAD_FAILED path={path} error={exc}", file=sys.stderr)
        return last_mtime, False

    if isinstance(data, dict) and "line_follow" in data and isinstance(data["line_follow"], dict):
        data = data["line_follow"]

    if not isinstance(data, dict):
        print(f"CONFIG_IGNORED path={path} reason=not_object", file=sys.stderr)
        return mtime, False

    changed = []
    for key, value in data.items():
        if key not in CONFIG_KEYS:
            continue
        if not hasattr(args, key):
            continue

        if key == "track_mode":
            if value not in TRACK_MODES:
                print(f"CONFIG_IGNORED key={key} value={value}", file=sys.stderr)
                continue
            setattr(args, key, value)
        elif key == "obstacle_mode":
            if value not in OBSTACLE_MODES:
                print(f"CONFIG_IGNORED key={key} value={value}", file=sys.stderr)
                continue
            setattr(args, key, value)
        elif isinstance(getattr(args, key), int):
            setattr(args, key, int(value))
        elif isinstance(getattr(args, key), float):
            setattr(args, key, float(value))
        else:
            setattr(args, key, value)
        changed.append(f"{key}={getattr(args, key)}")

    if changed:
        print("CONFIG_RELOADED " + " ".join(changed))
    return mtime, bool(changed)


def clean_mask(mask, kernel_size=5):
    kernel_size = max(3, int(kernel_size) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def clean_mask_fast(mask, kernel_size=3):
    if kernel_size <= 1:
        return mask
    kernel_size = min(5, max(3, int(kernel_size) | 1))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_size, kernel_size))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def fast_mask_score(mask):
    if mask is None or mask.size == 0:
        return 0.0
    area_ratio = float(cv2.countNonZero(mask)) / float(mask.size)
    if area_ratio < 0.002 or area_ratio > 0.45:
        return 0.0
    h, _ = mask.shape[:2]
    bottom = mask[h // 2:, :]
    bottom_ratio = float(cv2.countNonZero(bottom)) / float(bottom.size)
    lower_quarter = mask[(h * 3) // 4:, :]
    lower_quarter_ratio = float(cv2.countNonZero(lower_quarter)) / float(lower_quarter.size)
    density_score = max(0.0, 1.0 - abs(area_ratio - 0.08) / 0.20)
    return 2.0 * bottom_ratio + 1.2 * lower_quarter_ratio + density_score


def keep_line_like_components(mask, min_area_ratio=0.002, max_area_ratio=0.32):
    if mask is None or mask.size == 0:
        return mask, 0.0

    h, w = mask.shape[:2]
    contour_result = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = contour_result[-2]
    if not contours:
        return np.zeros_like(mask), 0.0

    best_contour = None
    best_score = 0.0
    image_area = float(mask.size)
    image_cx = w / 2.0

    for contour in contours:
        area = cv2.contourArea(contour)
        area_ratio = area / image_area
        if area_ratio < min_area_ratio or area_ratio > max_area_ratio:
            continue

        x, y, cw, ch = cv2.boundingRect(contour)
        if cw <= 0 or ch <= 0:
            continue

        vertical_reach = ch / float(h)
        horizontal_reach = cw / float(w)
        fill_ratio = area / float(max(1, cw * ch))
        lower_touch = max(0.0, ((y + ch) - h * 0.55) / max(1.0, h * 0.45))
        center_bonus = 1.0 - min(1.0, abs((x + cw / 2.0) - image_cx) / image_cx)

        # A track line should be visible in the lower ROI, have reasonable area,
        # and not fill almost the whole image like a wall/desk/background patch.
        score = (
            1.6 * lower_touch +
            1.2 * vertical_reach +
            0.7 * min(1.0, horizontal_reach * 2.0) +
            0.4 * center_bonus +
            0.5 * max(0.0, 1.0 - abs(fill_ratio - 0.45) / 0.45)
        )
        score *= max(0.15, 1.0 - max(0.0, area_ratio - 0.18) / 0.14)

        if score > best_score:
            best_score = score
            best_contour = contour

    if best_contour is None:
        return np.zeros_like(mask), 0.0

    filtered = np.zeros_like(mask)
    cv2.drawContours(filtered, [best_contour], -1, 255, thickness=cv2.FILLED)
    return filtered, best_score


def keep_white_line_by_side(mask, side="right", min_area_ratio=0.0004, max_area_ratio=0.12):
    if mask is None or mask.size == 0:
        return mask, 0.0

    h, w = mask.shape[:2]
    contour_result = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = contour_result[-2]
    if not contours:
        return np.zeros_like(mask), 0.0

    best_contour = None
    best_score = -1.0
    image_area = float(mask.size)
    image_cx = w / 2.0

    for contour in contours:
        area = cv2.contourArea(contour)
        area_ratio = area / image_area
        if area_ratio < min_area_ratio or area_ratio > max_area_ratio:
            continue

        x, y, cw, ch = cv2.boundingRect(contour)
        if cw <= 0 or ch <= 0:
            continue

        # Use the lower part of the component to decide which physical line it is.
        pts = contour.reshape(-1, 2)
        lower_pts = pts[pts[:, 1] >= int(h * 0.45)]
        if lower_pts.size == 0:
            lower_x = x + cw / 2.0
        else:
            lower_x = float(np.mean(lower_pts[:, 0]))

        vertical_reach = ch / float(h)
        lower_touch = max(0.0, ((y + ch) - h * 0.55) / max(1.0, h * 0.45))
        thin_bonus = max(0.0, 1.0 - abs((cw / float(w)) - 0.16) / 0.24)
        line_score = 1.5 * lower_touch + 1.2 * vertical_reach + 0.6 * thin_bonus

        if side == "left":
            side_score = max(0.0, (image_cx - lower_x) / image_cx)
        elif side == "right":
            side_score = max(0.0, (lower_x - image_cx) / image_cx)
        else:
            # nearest: prefer the line closest to the image center in the lower ROI.
            side_score = 1.0 - min(1.0, abs(lower_x - image_cx) / image_cx)

        score = line_score + 1.4 * side_score
        if score > best_score:
            best_score = score
            best_contour = contour

    if best_contour is None:
        return np.zeros_like(mask), 0.0

    filtered = np.zeros_like(mask)
    cv2.drawContours(filtered, [best_contour], -1, 255, thickness=cv2.FILLED)
    return filtered, max(0.0, best_score)


def keep_hough_white_line_by_side(mask, side="right"):
    if mask is None or mask.size == 0:
        return mask, 0.0

    h, w = mask.shape[:2]
    lines = cv2.HoughLinesP(
        mask,
        rho=1,
        theta=np.pi / 180.0,
        threshold=max(12, int(min(h, w) * 0.08)),
        minLineLength=max(18, int(w * 0.14)),
        maxLineGap=max(10, int(w * 0.08)),
    )
    if lines is None:
        return np.zeros_like(mask), 0.0

    best_line = None
    best_score = -1.0
    image_cx = w / 2.0
    diag = float(np.hypot(w, h))

    for item in lines:
        x1, y1, x2, y2 = [int(v) for v in item[0]]
        dx = float(x2 - x1)
        dy = float(y2 - y1)
        length = float(np.hypot(dx, dy))
        if length < max(15.0, w * 0.10):
            continue

        # The lane line in perspective is usually diagonal. Reject nearly flat
        # highlights from buildings/horizon while keeping shallow real lane lines.
        diagonal_score = min(1.0, abs(dy) / max(1.0, abs(dx)) * 5.0)
        if diagonal_score < 0.08:
            continue

        if y1 >= y2:
            lower_x, lower_y = x1, y1
        else:
            lower_x, lower_y = x2, y2

        if side == "left":
            side_score = max(0.0, (image_cx - lower_x) / image_cx)
        elif side == "right":
            side_score = max(0.0, (lower_x - image_cx) / image_cx)
        else:
            side_score = 1.0 - min(1.0, abs(lower_x - image_cx) / image_cx)

        lower_score = clamp(lower_y / float(max(1, h - 1)), 0.0, 1.0)
        length_score = clamp(length / diag, 0.0, 1.0)
        score = 1.8 * side_score + 1.3 * lower_score + 1.1 * length_score + 0.8 * diagonal_score

        if score > best_score:
            best_score = score
            best_line = (x1, y1, x2, y2)

    if best_line is None:
        return np.zeros_like(mask), 0.0

    selected = np.zeros_like(mask)
    cv2.line(selected, best_line[:2], best_line[2:], 255, thickness=max(3, int(w * 0.015)))
    selected = cv2.dilate(selected, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    return selected, max(0.0, best_score)


def fast_blue_white_mask(roi_bgr, morph_kernel=3):
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    h_ch, s_ch, v_ch = cv2.split(hsv)

    # 蓝白跑道：先确认“蓝色跑道背景”，再只保留蓝色背景附近的低饱和高亮白线。
    blue_sat_min = max(45, int(np.percentile(s_ch, 45)))
    blue_val_min = max(25, int(np.percentile(v_ch, 10)))
    blue = cv2.inRange(hsv, (85, blue_sat_min, blue_val_min), (140, 255, 255))
    blue = clean_mask_fast(blue, morph_kernel)

    white_sat_max = min(120, max(55, int(np.percentile(s_ch, 38))))
    white_val_min = max(115, int(np.percentile(v_ch, 68)))
    white = cv2.inRange(hsv, (0, 0, white_val_min), (179, white_sat_max, 255))

    # 白线自身不是蓝色，所以把蓝色区域横向膨胀一点，让贴着蓝色跑道的白线被保留。
    h, w = roi_bgr.shape[:2]
    support_w = max(9, int(w * 0.08) | 1)
    support_h = max(5, int(h * 0.05) | 1)
    blue_support = cv2.dilate(
        blue,
        cv2.getStructuringElement(cv2.MORPH_RECT, (support_w, support_h)),
    )
    mask = cv2.bitwise_and(white, blue_support)
    mask = clean_mask_fast(mask, morph_kernel)

    return mask, blue, {
        "white_sat_max": int(white_sat_max),
        "white_val_min": int(white_val_min),
        "blue_sat_min": int(blue_sat_min),
        "blue_val_min": int(blue_val_min),
    }


def mask_score(mask):
    if mask is None or mask.size == 0:
        return 0.0

    area_ratio = float(cv2.countNonZero(mask)) / float(mask.size)
    if area_ratio < 0.002 or area_ratio > 0.55:
        return 0.0

    h, w = mask.shape[:2]
    bottom = mask[h // 2:, :]
    bottom_ratio = float(cv2.countNonZero(bottom)) / float(bottom.size)

    contour_result = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = contour_result[-2]
    contour_score = 0.0
    if contours:
        largest = max(contours, key=cv2.contourArea)
        x, y, cw, ch = cv2.boundingRect(largest)
        contour_area_ratio = cv2.contourArea(largest) / float(mask.size)
        vertical_reach = ch / float(h)
        center_bonus = 1.0 - min(1.0, abs((x + cw / 2.0) - w / 2.0) / (w / 2.0))
        contour_score = 2.0 * contour_area_ratio + 0.8 * vertical_reach + 0.3 * center_bonus

    # A good line mask is neither empty nor full, and it should be visible near the lower ROI.
    density_score = max(0.0, 1.0 - abs(area_ratio - 0.08) / 0.20)
    return 1.8 * bottom_ratio + density_score + contour_score


def preprocess_fast(frame, track_mode="fast_dark", roi_y_ratio=0.55, morph_kernel=3):
    h, _ = frame.shape[:2]
    roi_y = int(h * roi_y_ratio)
    roi_y = clamp(roi_y, 0, max(0, h - 1))
    roi = frame[roi_y:, :]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

    candidates = []
    if track_mode in ("fast_white_line_left", "fast_white_line_right", "fast_white_line_nearest"):
        _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        side = track_mode.replace("fast_white_line_", "")
        mask = clean_mask_fast(bright, morph_kernel)
        hough_mask, hough_score = keep_hough_white_line_by_side(mask, side=side)
        if hough_score > 0.0:
            mask = hough_mask
            component_score = hough_score
        else:
            mask, component_score = keep_white_line_by_side(mask, side=side)
        score = fast_mask_score(mask) + component_score
        if score < 0.35:
            mask = np.zeros(roi.shape[:2], dtype=np.uint8)
            return roi_y, mask, {
                "threshold_mode": "fast_no_reliable_white_line_%s" % side,
                "threshold_score": 0.0,
                "component_score": 0.0,
                "roi_y": int(roi_y),
                "fast": True,
            }
        return roi_y, mask, {
            "threshold_mode": "fast_white_line_%s" % side,
            "threshold_score": float(score),
            "component_score": float(component_score),
            "roi_y": int(roi_y),
            "fast": True,
        }

    if track_mode == "fast_bright":
        _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = clean_mask_fast(bright, morph_kernel)
        score = fast_mask_score(mask)
        return roi_y, mask, {
            "threshold_mode": "fast_gray_otsu_bright_raw",
            "threshold_score": float(score),
            "component_score": 0.0,
            "roi_y": int(roi_y),
            "fast": True,
        }

    if track_mode == "fast_blue_white":
        mask, blue_mask, params = fast_blue_white_mask(roi, morph_kernel)
        score = fast_mask_score(mask)
        blue_ratio = float(cv2.countNonZero(blue_mask)) / float(max(1, blue_mask.size))
        if score < 0.18 or blue_ratio < 0.08:
            mask = np.zeros(roi.shape[:2], dtype=np.uint8)
            score = 0.0
        return roi_y, mask, {
            "threshold_mode": "fast_blue_white",
            "threshold_score": float(score),
            "blue_ratio": float(blue_ratio),
            "roi_y": int(roi_y),
            "fast": True,
            "white_sat_max": params["white_sat_max"],
            "white_val_min": params["white_val_min"],
            "blue_sat_min": params["blue_sat_min"],
            "blue_val_min": params["blue_val_min"],
        }

    if track_mode in ("fast_dark", "fast_gray", "fast_auto"):
        _, dark = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        candidates.append(("fast_gray_otsu_dark", dark))
    if track_mode in ("fast_gray", "fast_auto"):
        _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(("fast_gray_otsu_bright", bright))
    if track_mode in ("fast_red", "fast_white", "fast_red_white"):
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        h_ch, s_ch, v_ch = cv2.split(hsv)
        if track_mode in ("fast_red", "fast_red_white"):
            sat_min = max(45, int(np.percentile(s_ch, 55)))
            val_min = max(35, int(np.percentile(v_ch, 20)))
            red1 = cv2.inRange(hsv, (0, sat_min, val_min), (12, 255, 255))
            red2 = cv2.inRange(hsv, (168, sat_min, val_min), (179, 255, 255))
            candidates.append(("fast_hsv_red", cv2.bitwise_or(red1, red2)))
        if track_mode in ("fast_white", "fast_red_white"):
            sat_max = min(95, max(35, int(np.percentile(s_ch, 45))))
            val_min = max(120, int(np.percentile(v_ch, 70)))
            candidates.append(("fast_hsv_white", cv2.inRange(hsv, (0, 0, val_min), (179, sat_max, 255))))

    best_name = "none"
    best_mask = None
    best_score = -1.0
    best_component_score = 0.0
    for name, mask in candidates:
        mask = clean_mask_fast(mask, morph_kernel)
        mask, component_score = keep_line_like_components(mask)
        score = fast_mask_score(mask) + component_score
        if score > best_score:
            best_name = name
            best_mask = mask
            best_score = score
            best_component_score = component_score

    if best_mask is None or best_score < 0.35:
        best_mask = np.zeros(roi.shape[:2], dtype=np.uint8)
        best_name = "fast_no_reliable_line"
        best_score = 0.0
        best_component_score = 0.0

    return roi_y, best_mask, {
        "threshold_mode": best_name,
        "threshold_score": float(best_score),
        "component_score": float(best_component_score),
        "roi_y": int(roi_y),
        "fast": True,
    }


def otsu_candidates(gray):
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bright = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, dark = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 3
    )
    adaptive_inv = cv2.bitwise_not(adaptive)
    return [
        ("gray_otsu_bright", bright),
        ("gray_otsu_dark", dark),
        ("gray_adaptive_bright", adaptive),
        ("gray_adaptive_dark", adaptive_inv),
    ]


def percentile_mask(channel, percentile, bright=True):
    threshold = float(np.percentile(channel, percentile))
    if bright:
        mask = (channel >= threshold).astype(np.uint8) * 255
    else:
        mask = (channel <= threshold).astype(np.uint8) * 255
    return mask


def color_candidates(roi_bgr, track_mode):
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2Lab)
    h, s, v = cv2.split(hsv)
    l, a, b = cv2.split(lab)

    candidates = []
    if track_mode in ("auto", "gray", "dark", "bright"):
        gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        candidates.extend(otsu_candidates(gray))
        candidates.append(("gray_dynamic_dark", percentile_mask(gray, 25, bright=False)))
        candidates.append(("gray_dynamic_bright", percentile_mask(gray, 75, bright=True)))

    if track_mode in ("auto", "red"):
        sat_min = max(45, int(np.percentile(s, 60)))
        red1 = cv2.inRange(hsv, (0, sat_min, 35), (12, 255, 255))
        red2 = cv2.inRange(hsv, (168, sat_min, 35), (179, 255, 255))
        candidates.append(("hsv_red", cv2.bitwise_or(red1, red2)))
        candidates.append(("lab_a_red_dynamic", percentile_mask(a, 75, bright=True)))

    if track_mode in ("auto", "blue"):
        sat_min = max(45, int(np.percentile(s, 60)))
        candidates.append(("hsv_blue", cv2.inRange(hsv, (90, sat_min, 35), (135, 255, 255))))
        candidates.append(("lab_b_blue_dynamic", percentile_mask(b, 25, bright=False)))

    if track_mode in ("auto", "yellow"):
        sat_min = max(45, int(np.percentile(s, 60)))
        candidates.append(("hsv_yellow", cv2.inRange(hsv, (18, sat_min, 35), (40, 255, 255))))
        candidates.append(("lab_b_yellow_dynamic", percentile_mask(b, 75, bright=True)))

    return candidates


def preprocess(frame, track_mode="auto", roi_y_ratio=0.4, morph_kernel=5, return_debug=False):
    if track_mode.startswith("fast_"):
        roi_y, best_mask, info = preprocess_fast(
            frame,
            track_mode=track_mode,
            roi_y_ratio=roi_y_ratio,
            morph_kernel=morph_kernel,
        )
        if return_debug:
            return roi_y, best_mask, info
        return roi_y, best_mask

    h, _ = frame.shape[:2]
    roi_y = int(h * roi_y_ratio)
    roi_y = clamp(roi_y, 0, max(0, h - 1))
    roi = frame[roi_y:, :].copy()

    candidates = color_candidates(roi, track_mode)
    best_name = "none"
    best_mask = None
    best_score = -1.0

    for name, mask in candidates:
        mask = clean_mask(mask, morph_kernel)
        score = mask_score(mask)
        if score > best_score:
            best_name = name
            best_mask = mask
            best_score = score

    if best_mask is None:
        best_mask = np.zeros(roi.shape[:2], dtype=np.uint8)

    info = {
        "threshold_mode": best_name,
        "threshold_score": float(best_score),
        "roi_y": int(roi_y),
    }
    if return_debug:
        return roi_y, best_mask, info
    return roi_y, best_mask


def find_target(binary, lookahead_window=5, margin=52, minpix=70, nwindows=10):
    h, w = binary.shape
    bottom = binary[h // 2:, :]
    hist = np.sum(bottom, axis=0)
    base_x = int(np.argmax(hist))
    if hist[base_x] < 6 * 255:
        return False, -1, [], 0.0

    current_x = base_x
    window_h = max(1, h // nwindows)
    centers = []
    weights = []

    for wi in range(nwindows):
        y_low = max(0, h - (wi + 1) * window_h)
        y_high = min(h, h - wi * window_h)
        x_low = max(0, current_x - margin)
        x_high = min(w, current_x + margin)

        win = binary[y_low:y_high, x_low:x_high]
        ys, xs = np.nonzero(win)
        count = len(xs)
        if count >= minpix:
            current_x = x_low + int(np.mean(xs))

        centers.append((current_x, (y_low + y_high) // 2))
        # Upper windows are closer to the lookahead point, but low windows are more reliable.
        count_weight = clamp(count / float(max(minpix, 1)), 0.2, 3.0)
        layer_weight = 1.0 + 0.15 * wi
        weights.append(count_weight * layer_weight)

    lookahead_idx = min(len(centers) - 1, max(0, lookahead_window))
    xs_for_weight = np.array([p[0] for p in centers[: lookahead_idx + 1]], dtype=np.float32)
    ws_for_weight = np.array(weights[: lookahead_idx + 1], dtype=np.float32)
    if float(np.sum(ws_for_weight)) <= 1e-6:
        target_x = centers[lookahead_idx][0]
    else:
        weighted_x = float(np.sum(xs_for_weight * ws_for_weight) / np.sum(ws_for_weight))
        target_x = int(round(0.55 * centers[lookahead_idx][0] + 0.45 * weighted_x))

    confidence = clamp(float(hist[base_x]) / float(max(1, h * 255)), 0.0, 1.0)
    return True, target_x, centers, confidence


def _find_lane_center_in_band(binary, y0, y1, minpix=12, min_sep_ratio=0.22,
                              expected_center_x=None, expected_lane_width=0):
    if binary is None or binary.size == 0:
        return False, -1, [], 0.0

    h, w = binary.shape
    y0 = int(clamp(y0, 0, max(0, h - 1)))
    y1 = int(clamp(y1, y0 + 1, h))
    band = binary[y0:y1, :]
    hist = np.sum(band, axis=0).astype(np.float32)
    if hist.size == 0:
        return False, -1, [], 0.0

    kernel = max(5, (w // 40) | 1)
    hist = cv2.GaussianBlur(hist.reshape(1, -1), (kernel, 1), 0).reshape(-1)
    max_val = float(np.max(hist))
    if max_val < max(3, minpix) * 255:
        return False, -1, [], 0.0

    threshold = max(max_val * 0.32, float(max(3, minpix) * 255))
    active = hist >= threshold

    segments = []
    start = None
    for x, is_active in enumerate(active):
        if is_active and start is None:
            start = x
        elif not is_active and start is not None:
            end = x - 1
            if end >= start:
                xs = np.arange(start, end + 1, dtype=np.float32)
                weights = hist[start:end + 1]
                weight_sum = float(np.sum(weights))
                if weight_sum > 0:
                    cx = int(round(float(np.sum(xs * weights) / weight_sum)))
                    segments.append((cx, start, end, weight_sum))
            start = None
    if start is not None:
        end = len(active) - 1
        xs = np.arange(start, end + 1, dtype=np.float32)
        weights = hist[start:end + 1]
        weight_sum = float(np.sum(weights))
        if weight_sum > 0:
            cx = int(round(float(np.sum(xs * weights) / weight_sum)))
            segments.append((cx, start, end, weight_sum))

    expected_center = int(expected_center_x) if expected_center_x is not None else w // 2
    expected_lane_width = int(expected_lane_width)
    y = y0 + band.shape[0] // 2

    def infer_from_single_segment(line):
        if line[0] < expected_center:
            target_x = int(round(line[0] + expected_lane_width / 2.0))
        else:
            target_x = int(round(line[0] - expected_lane_width / 2.0))
        target_x = int(clamp(target_x, 0, w - 1))
        confidence = clamp(line[3] / float(max(1, band.shape[0] * 255)), 0.0, 0.65)
        centers = [(line[0], y), (target_x, y)]
        return True, target_x, centers, confidence

    if len(segments) == 1 and expected_lane_width > 0:
        return infer_from_single_segment(segments[0])

    if len(segments) < 2:
        return False, -1, [], 0.0

    min_sep = int(w * min_sep_ratio)
    best_pair = None
    best_score = -1.0e18
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            left = segments[i]
            right = segments[j]
            sep = abs(right[0] - left[0])
            if sep < min_sep:
                continue
            pair_center = (left[0] + right[0]) / 2.0
            weight_score = 0.0001 * (left[3] + right[3])
            if expected_center_x is not None:
                center_penalty = abs(pair_center - expected_center)
                if expected_lane_width > 0:
                    width_penalty = abs(sep - expected_lane_width)
                    score = weight_score - 0.05 * center_penalty - 0.35 * width_penalty
                else:
                    score = weight_score - 0.05 * center_penalty + 0.08 * sep
            else:
                score = sep + weight_score
            if score > best_score:
                best_score = score
                best_pair = (left, right)

    if best_pair is None:
        if segments and expected_lane_width > 0:
            strongest = max(segments, key=lambda item: item[3])
            return infer_from_single_segment(strongest)
        return False, -1, [], 0.0

    left, right = sorted(best_pair, key=lambda item: item[0])
    target_x = int(round((left[0] + right[0]) / 2.0))
    centers = [(left[0], y), (right[0], y), (target_x, y)]
    confidence = clamp((left[3] + right[3]) / float(max(1, band.shape[0] * 255 * 2)), 0.0, 1.0)
    return True, target_x, centers, confidence


def find_lane_center(binary, minpix=12, min_sep_ratio=0.22,
                     expected_center_x=None, expected_lane_width=0,
                     heading_gain=0.45):
    if binary is None or binary.size == 0:
        return False, -1, [], 0.0

    h, _ = binary.shape
    expected_center = expected_center_x
    near = _find_lane_center_in_band(
        binary,
        int(h * 0.55),
        h,
        minpix=minpix,
        min_sep_ratio=min_sep_ratio,
        expected_center_x=expected_center,
        expected_lane_width=expected_lane_width,
    )
    far = _find_lane_center_in_band(
        binary,
        int(h * 0.08),
        int(h * 0.62),
        minpix=max(3, int(minpix * 0.75)),
        min_sep_ratio=min_sep_ratio,
        expected_center_x=expected_center,
        expected_lane_width=expected_lane_width,
    )
    full = _find_lane_center_in_band(
        binary,
        h // 3,
        h,
        minpix=minpix,
        min_sep_ratio=min_sep_ratio,
        expected_center_x=expected_center,
        expected_lane_width=expected_lane_width,
    )

    found_near, near_x, near_centers, near_conf = near
    found_far, far_x, far_centers, far_conf = far
    found_full, full_x, full_centers, full_conf = full

    if found_near and found_far:
        # near_x corrects lateral offset; far_x adds heading/lookahead correction.
        gain = clamp(float(heading_gain), 0.0, 1.0)
        target_x = int(round((1.0 - gain) * near_x + gain * far_x))
        confidence = clamp(0.65 * near_conf + 0.35 * far_conf, 0.0, 1.0)
        centers = list(near_centers) + list(far_centers) + [(target_x, int(h * 0.58))]
        return True, target_x, centers, confidence

    if found_near:
        return True, near_x, near_centers, near_conf

    if found_full:
        return True, full_x, full_centers, full_conf

    if found_far:
        return True, far_x, far_centers, far_conf

    return False, -1, [], 0.0


class TargetFilter:
    def __init__(self, alpha=0.35, max_predict_frames=5):
        self.alpha = alpha
        self.max_predict_frames = max_predict_frames
        self.filtered_x = None
        self.last_x = None
        self.velocity = 0.0
        self.lost_frames = 0

    def update(self, found, target_x, image_width):
        if found and target_x >= 0:
            measured = float(target_x)
            if self.filtered_x is None:
                self.filtered_x = measured
                self.velocity = 0.0
            else:
                prev = self.filtered_x
                self.filtered_x = self.alpha * measured + (1.0 - self.alpha) * self.filtered_x
                self.velocity = self.filtered_x - prev
            self.last_x = self.filtered_x
            self.lost_frames = 0
            return True, int(round(self.filtered_x)), "MEASURED", self.lost_frames

        self.lost_frames += 1
        if self.last_x is not None and self.lost_frames <= self.max_predict_frames:
            predicted = self.last_x + self.velocity * self.lost_frames
            predicted = clamp(predicted, 0, image_width - 1)
            self.filtered_x = predicted
            return True, int(round(predicted)), "PREDICTED", self.lost_frames

        return False, -1, "LOST", self.lost_frames


def split_to_wheels(vx_cmd, vy_cmd, wz_cmd, turn_scale, wheel_cmd_limit):
    rot_term = int(round(turn_scale * wz_cmd))
    raw = (
        -vx_cmd + vy_cmd - rot_term,
        +vx_cmd + vy_cmd - rot_term,
        -vx_cmd + vy_cmd + rot_term,
        +vx_cmd + vy_cmd + rot_term,
    )
    max_abs = max(abs(v) for v in raw)
    if max_abs > wheel_cmd_limit:
        scale = wheel_cmd_limit / float(max_abs)
        raw = tuple(int(round(v * scale)) for v in raw)
    return tuple(clamp_cmd(v, wheel_cmd_limit) for v in raw)


def detect_obstacle(frame, mode="off", area_threshold=0.28):
    if mode == "off":
        return False, 0.0

    h, w = frame.shape[:2]
    y0 = int(h * 0.70)
    x0 = int(w * 0.22)
    x1 = int(w * 0.78)
    zone = frame[y0:h, x0:x1]
    if zone.size == 0:
        return False, 0.0

    if mode == "red":
        hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, (0, 80, 40), (12, 255, 255))
        mask2 = cv2.inRange(hsv, (168, 80, 40), (179, 255, 255))
        mask = cv2.bitwise_or(mask1, mask2)
        ratio = float(cv2.countNonZero(mask)) / float(mask.size)
        return ratio > 0.08, ratio

    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    mask = cv2.inRange(gray, 0, 55)
    mask = clean_mask(mask, 7)
    ratio = float(cv2.countNonZero(mask)) / float(mask.size)
    return ratio > area_threshold, ratio


def control(target_x, found, image_width, last_error, lost_frames, target_source="MEASURED",
            obstacle_detected=False, vx_cmd=0, base_vy_cmd=120, slow_vy_cmd=60,
            lost_vy_cmd=0, lost_search_vy_cmd=0, lost_search_wz_cmd=0,
            kp=1.2, kd=0.35, turn_scale=1.0,
            wheel_cmd_limit=300, lost_line_frames_to_estop=8,
            camera_center_offset=0):
    if obstacle_detected:
        return {
            "tracking_ok": False,
            "error": 0,
            "vx_cmd": 0,
            "vy_cmd": 0,
            "wz_cmd": 0,
            "wheel_cmds": (0, 0, 0, 0),
            "last_error": last_error,
            "lost_frames": lost_frames,
            "estop_request": True,
            "hold_request": True,
        }

    if not found:
        lost_frames += 1
        estop_request = lost_frames >= lost_line_frames_to_estop
        if estop_request:
            search_vy = 0
            search_wz = 0
            wheel_cmds = split_to_wheels(vx_cmd, 0, 0, turn_scale, wheel_cmd_limit)
        else:
            search_vy = lost_search_vy_cmd if lost_search_vy_cmd > 0 else min(slow_vy_cmd, max(0, lost_vy_cmd))
            search_wz_mag = lost_search_wz_cmd if lost_search_wz_cmd > 0 else max(35, int(wheel_cmd_limit * 0.22))
            search_dir = 1 if last_error >= 0 else -1
            search_wz = search_dir * search_wz_mag
            wheel_cmds = split_to_wheels(vx_cmd, search_vy, search_wz, turn_scale, wheel_cmd_limit)
        return {
            "tracking_ok": False,
            "error": last_error,
            "vx_cmd": vx_cmd,
            "vy_cmd": search_vy,
            "wz_cmd": search_wz,
            "wheel_cmds": wheel_cmds,
            "last_error": last_error,
            "lost_frames": lost_frames,
            "estop_request": estop_request,
            "hold_request": estop_request,
        }

    image_center_x = image_width // 2 + int(camera_center_offset)
    error = target_x - image_center_x
    diff_error = error - last_error
    wz_cmd = int(round(kp * error + kd * diff_error))
    wz_cmd = clamp_cmd(wz_cmd, wheel_cmd_limit)

    turn_ratio = clamp(abs(error) / float(max(1, image_width // 2)), 0.0, 1.0)
    vy_cmd = int(round(base_vy_cmd - (base_vy_cmd - slow_vy_cmd) * turn_ratio))
    if target_source == "PREDICTED":
        # A short prediction means the line was only missed for a frame or two.
        # Keep a moderate forward speed instead of dropping to pure turning,
        # otherwise the car moves in a stop-turn-stop pattern on intermittent frames.
        predicted_vy_cmd = lost_vy_cmd if lost_vy_cmd > 0 else max(slow_vy_cmd, int(base_vy_cmd * 0.65))
        vy_cmd = min(vy_cmd, predicted_vy_cmd)

    wheel_cmds = split_to_wheels(vx_cmd, vy_cmd, wz_cmd, turn_scale, wheel_cmd_limit)

    return {
        "tracking_ok": target_source == "MEASURED",
        "error": error,
        "vx_cmd": vx_cmd,
        "vy_cmd": vy_cmd,
        "wz_cmd": wz_cmd,
        "wheel_cmds": wheel_cmds,
        "last_error": error,
        "lost_frames": 0 if target_source == "MEASURED" else lost_frames,
        "estop_request": False,
        "hold_request": False,
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Enhanced PS line-following prototype.")
    parser.add_argument("mode", choices=["camera", "video", "image"])
    parser.add_argument("source")
    parser.add_argument("track_mode", nargs="?", default="auto",
                        choices=list(TRACK_MODES))
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--roi-y-ratio", type=float, default=0.40)
    parser.add_argument("--morph-kernel", type=int, default=5)
    parser.add_argument("--lookahead-window", type=int, default=5)
    parser.add_argument("--margin", type=int, default=52)
    parser.add_argument("--minpix", type=int, default=70)
    parser.add_argument("--nwindows", type=int, default=10)
    parser.add_argument("--filter-alpha", type=float, default=0.35)
    parser.add_argument("--predict-frames", type=int, default=5)
    parser.add_argument("--kp", type=float, default=1.2)
    parser.add_argument("--kd", type=float, default=0.35)
    parser.add_argument("--base-vy", type=int, default=120)
    parser.add_argument("--slow-vy", type=int, default=60)
    parser.add_argument("--lost-vy", type=int, default=0)
    parser.add_argument("--lost-search-vy", type=int, default=0)
    parser.add_argument("--lost-search-wz", type=int, default=0)
    parser.add_argument("--wheel-limit", type=int, default=300)
    parser.add_argument("--lost-estop-frames", type=int, default=8)
    parser.add_argument("--camera-center-offset", type=int, default=0)
    parser.add_argument("--lane-width-px", type=int, default=0)
    parser.add_argument("--heading-gain", type=float, default=0.45)
    parser.add_argument("--command-smoothing", type=float, default=0.35)
    parser.add_argument("--command-delta-limit", type=int, default=0)
    parser.add_argument("--obstacle-mode", default="off", choices=list(OBSTACLE_MODES))
    parser.add_argument("--config", default="", help="JSON tuning file; values override command-line defaults")
    parser.add_argument("--reload-config", action="store_true", help="reload --config while running")
    parser.add_argument("--config-check-frames", type=int, default=15)
    parser.add_argument("--save-debug-dir", default="", help="save debug frames and binary masks")
    parser.add_argument("--save-every", type=int, default=10)
    parser.add_argument("--no-gui", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    config_mtime, _ = load_tuning_config(args.config, args)

    frame_id = 0
    last_error = 0
    lost_frames = 0
    target_filter = TargetFilter(alpha=args.filter_alpha, max_predict_frames=args.predict_frames)

    if args.mode == "camera":
        cap = cv2.VideoCapture(int(args.source))
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FPS, args.fps)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        img = None
    elif args.mode == "video":
        cap = cv2.VideoCapture(args.source)
        img = None
    else:
        img = cv2.imread(args.source)
        if img is None:
            print("image open failed")
            return 2
        cap = None
        img = cv2.resize(img, (args.width, args.height))

    while True:
        if args.mode == "image":
            frame = img.copy()
            ok = True
        else:
            ok, frame = cap.read()

        if not ok or frame is None:
            break

        frame = cv2.resize(frame, (args.width, args.height))
        frame_id += 1

        if args.reload_config and args.config and frame_id % max(1, args.config_check_frames) == 0:
            config_mtime, changed = load_tuning_config(args.config, args, config_mtime, quiet=True)
            if changed:
                target_filter.alpha = args.filter_alpha
                target_filter.max_predict_frames = args.predict_frames

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

        print(
            f"frame_id={frame_id} raw_target={raw_target_x} target_x={target_x} "
            f"source={target_source} error={out['error']} vy_cmd={out['vy_cmd']} "
            f"wz_cmd={out['wz_cmd']} wheel=[{out['wheel_cmds'][0]},{out['wheel_cmds'][1]},"
            f"{out['wheel_cmds'][2]},{out['wheel_cmds'][3]}] tracking={1 if out['tracking_ok'] else 0} "
            f"estop={1 if out['estop_request'] else 0} obstacle={1 if obstacle else 0} "
            f"obstacle_ratio={obstacle_ratio:.3f} confidence={confidence:.3f} "
            f"threshold={prep_info['threshold_mode']}"
        )

        dbg = frame.copy()
        cv2.rectangle(dbg, (0, roi_y), (args.width - 1, args.height - 1), (0, 255, 255), 2)
        cv2.line(dbg, (args.width // 2, 0), (args.width // 2, args.height - 1), (255, 0, 0), 2)
        for cx, cy in centers:
            cv2.circle(dbg, (cx, cy + roi_y), 3, (0, 255, 0), -1)
        if target_x >= 0:
            color = (0, 0, 255) if target_source == "MEASURED" else (0, 165, 255)
            y = centers[min(len(centers) - 1, args.lookahead_window)][1] + roi_y if centers else args.height // 2
            cv2.circle(dbg, (target_x, y), 7, color, -1)
        if obstacle:
            cv2.putText(dbg, "OBSTACLE", (20, args.height - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

        overlay_lines = [
            f"frame_id: {frame_id}",
            f"target: {target_x} ({target_source})",
            f"error: {out['error']}",
            f"vy/wz: {out['vy_cmd']}/{out['wz_cmd']}",
            "wheel: {},{},{},{}".format(*out["wheel_cmds"]),
            f"tracking/estop: {int(out['tracking_ok'])}/{int(out['estop_request'])}",
            f"threshold: {prep_info['threshold_mode']}",
        ]
        for idx, text in enumerate(overlay_lines):
            cv2.putText(dbg, text, (10, 22 + idx * 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)

        if args.save_debug_dir and frame_id % max(1, args.save_every) == 0:
            os.makedirs(args.save_debug_dir, exist_ok=True)
            cv2.imwrite(os.path.join(args.save_debug_dir, f"frame_{frame_id:06d}.jpg"), dbg)
            cv2.imwrite(os.path.join(args.save_debug_dir, f"binary_{frame_id:06d}.png"), binary)

        if not args.no_gui:
            cv2.imshow("frame_debug", dbg)
            cv2.imshow("binary", binary)
            key = cv2.waitKey(0 if args.mode == "image" else 1)
            if key in (27, ord("q")):
                break
        elif args.mode == "image":
            break

        if args.mode == "image" and not args.no_gui:
            break

    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

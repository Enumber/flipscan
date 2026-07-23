#!/usr/bin/env python3
# Copyright (C) 2026 ENum
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. This program is distributed WITHOUT ANY WARRANTY; see the GNU General
# Public License (LICENSE) for details.

"""高拍仪翻页检测自动拍照 v10"""

# ── 版本号：全项目唯一来源 ──────────────────────────────────────
# 要用版本号的地方一律引这个常量，别再抄字面量。散落多处迟早有一处忘了改，
# 表现就是自动更新永远提示有新版（或永远不提示），还特别难查。
# 发版时改这一行 + 打同名 tag（v1.1.0）。
__version__ = "1.3.0"
GITHUB_REPO = "Enumber/flipscan"

import argparse
import cv2, os, sys, time, json, signal, subprocess, threading
import numpy as np
from collections import deque
from datetime import datetime
from enum import Enum
import locale
import enum_update
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw, ImageOps

# ── 轻量双语（无第三方依赖）─────────────────────────────────────

def _is_zh():
    """按系统 locale 判断是否中文环境。环境变量优先，回退到 locale 模块。"""
    for key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(key, "")
        if val:
            v = val.lower()
            return "zh" in v or "cn" in v
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    loc = loc.lower()
    return "zh" in loc or "cn" in loc

_ZH = _is_zh()      # locale 运行期不会变，只判定一次

def _t(zh, en):
    """中文环境返回 zh，其它环境返回 en。"""
    return zh if _ZH else en

# ── 用户配置持久化 ──────────────────────────────────────────────
# 摄像头选择要跨次启动记住，所以落到磁盘。放 XDG 配置目录而不是程序目录：
# 程序可能装在只读位置（/opt、系统包），而配置是每个用户各自的。

CONFIG_DEFAULTS = {
    "camera": None,            # 固定的摄像头设备路径，如 "/dev/video2"
    "remember_camera": False,  # 为真才会在启动时直接用上面这个设备
    "output_dir": None,        # 默认保存文件夹；None=程序自带的拍照结果目录
    "auto_check_updates": True,  # 启动时到 GitHub 看一眼有没有新版本
    # 高拍仪之外的普通摄像头（笔记本内置、USB 网络摄像头）画面往往更暗、更糊、
    # 白平衡也不一样，翻页检测的默认阈值是照着高拍仪调的，直接用容易漏拍或误拍。
    # 下面三项让这类设备也能调到能用：
    "motion_threshold": None,    # 翻页判定灵敏度，None=用内置默认
    "settle_frames": None,       # 画面稳定多少帧才拍，None=用内置默认
    "capture_delay": None,       # 判定稳定后再等多久才按快门（秒），None=默认
    "capture_on_start": False,   # 点「开始记录」时先自动拍一张当前画面，再开始监测翻页
}


def config_path():
    """配置文件绝对路径，尊重 XDG_CONFIG_HOME。"""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "flipscan", "config.json")


def load_config():
    """读配置，永远返回一个补齐了默认值的 dict。

    配置文件是可以被用户手改、也可能被写坏的外部输入，所以这里对
    「文件不存在 / 不是 JSON / 顶层不是对象 / 字段类型不对」全部
    静默回退默认值——配置坏掉绝不能让程序起不来。
    """
    cfg = dict(CONFIG_DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return cfg
    if not isinstance(data, dict):
        return cfg
    cam = data.get("camera")
    if isinstance(cam, str) and cam.strip():
        cfg["camera"] = cam.strip()
    elif isinstance(cam, int):          # 容忍手写成索引数字的情况
        cfg["camera"] = f"/dev/video{cam}"
    cfg["remember_camera"] = bool(data.get("remember_camera", False))
    out = data.get("output_dir")
    if isinstance(out, str) and out.strip() and os.path.isdir(out.strip()):
        cfg["output_dir"] = out.strip()
    else:
        cfg["output_dir"] = None
    # 缺这个字段就按默认值"开"：老配置文件升级上来不用改任何东西
    cfg["auto_check_updates"] = bool(
        data.get("auto_check_updates", CONFIG_DEFAULTS["auto_check_updates"]))
    # 三个可调阈值：只接受正数，其余（None/空/负数/写坏了）一律退回内置默认，
    # 免得一个手滑的值让翻页检测彻底不工作还查不出原因。
    for _k in ("motion_threshold", "settle_frames", "capture_delay"):
        _v = data.get(_k)
        try:
            _v = float(_v) if _v is not None else None
        except (TypeError, ValueError):
            _v = None
        cfg[_k] = _v if (_v is not None and _v > 0) else None
    cfg["capture_on_start"] = bool(data.get("capture_on_start", False))
    return cfg


def save_config(cfg):
    """写配置。先写临时文件再 rename，避免写到一半断电留下半个坏文件。

    写失败只返回 False，不抛异常：记不住选择是小事，崩掉是大事。
    """
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: cfg.get(k, v) for k, v in CONFIG_DEFAULTS.items()},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def remember_camera_setting(device_path, remember):
    """把「固定哪个摄像头」写进配置（读-改-写，保留将来可能新增的其它字段）。"""
    cfg = load_config()
    cfg["camera"] = device_path
    cfg["remember_camera"] = bool(remember)
    return save_config(cfg)

# ── 检测参数 ────────────────────────────────────────────────────
# 这些默认值是照着**高拍仪**调的（俯拍、光照均匀、画幅稳定）。笔记本内置摄像头、
# 普通 USB 网络摄像头的画面更暗更糊、白平衡也不同，用这套默认值常见的症状是
# 翻页了不拍（阈值太高）或手一晃就拍（阈值太低）。所以 MOTION_THRESHOLD /
# STABLE_SECONDS / 拍摄延迟三项允许用配置覆盖，见「设置 > 高级翻页检测」。
MOTION_THRESHOLD = 300
MIN_FLIP_MOTION  = 8000
MIN_FLIP_SECONDS = 0.3
STABLE_SECONDS   = 0.8
CAPTURE_DELAY    = 0.0   # 判定稳定后再等这么久才按快门；慢速对焦的摄像头可以调大


def apply_tuning(cfg):
    """把配置里的可调项覆盖到模块级常量上（None=保持内置默认）。

    用模块级常量而不是实例属性：检测循环里逐帧都要读，走全局最省事，
    也和这些常量原本的用法保持一致。"""
    global MOTION_THRESHOLD, STABLE_SECONDS, CAPTURE_DELAY
    v = cfg.get("motion_threshold")
    if v:
        MOTION_THRESHOLD = int(v)
    v = cfg.get("settle_frames")
    if v:
        STABLE_SECONDS = float(v)
    v = cfg.get("capture_delay")
    if v:
        CAPTURE_DELAY = float(v)
DIFF_THRESH      = 35
MOTION_SMOOTH    = 6
METER_MAX        = 20000
DETECT_RES       = (480, 360)

ELOAM_CMD   = "/opt/eloamcamera2.1/startup.sh"
LOCK_FILE   = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp",
                           "doc-camera-auto-capture.lock")
DEFAULT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           _t("拍照结果", "Captures"))
IMG_EXTS    = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
HIGH_CAMERA_HINTS = ("USB_2.0_Camera", "Sonix", "S1862B")

# ── 配色 ────────────────────────────────────────────────────────
C = {
    "bg":   "#050c18", "sbg":  "#080f1e", "cbg":  "#0b1526",
    "line": "#0f2a52", "cyan": "#00c8ff", "clow": "#003c4d",
    "glow": "#00ff88", "gldm": "#003322", "ambe": "#ffaa00",
    "red":  "#ff2255", "rdim": "#330011", "dim":  "#1a2840",
    "mid":  "#3d6090", "text": "#8ab4d8", "lit":  "#cce8ff",
    "wh":   "#e8f6ff",
}

class State(Enum):
    IDLE        = _t("待机",   "Idle")
    WAITING     = _t("监控中", "Watching")
    FLIPPING    = _t("翻页中", "Page turning")
    STABILIZING = _t("稳定中", "Settling")
    CAPTURING   = _t("拍照中", "Capturing")

STATE_COLOR = {
    State.IDLE:        C["mid"],
    State.WAITING:     C["cyan"],
    State.FLIPPING:    C["ambe"],
    State.STABILIZING: C["glow"],
    State.CAPTURING:   "#ffffff",
}

# ── 图像处理 ─────────────────────────────────────────────────────

def order_points(pts):
    """四角点排序：左上→右上→右下→左下"""
    pts  = np.array(pts, dtype="float32")
    s    = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)
    rect = np.zeros((4, 2), dtype="float32")
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def detect_paper(frame):
    """检测纸张/夹板轮廓，返回 (4,2) 顶点数组或 None。

    主策略：检测深色夹板/底板——白纸放在深色夹板上时，
    夹板四周露出的深色边框比白纸更容易检测（白纸+书本+灰色背景
    会连成一片让 Otsu 失效，而深色夹板边框不会）。
    备用：检测亮色纸张、Canny 边缘。
    """
    h, w    = frame.shape[:2]
    min_a   = h * w * 0.06
    max_a   = h * w * 0.85
    gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (11, 11), 0)
    med     = float(np.median(blurred))

    pad = 20
    ks  = max(20, min(h, w) // 60)
    k   = np.ones((ks, ks), np.uint8)
    # 白边：让深色轮廓在图像边缘也能闭合
    brd_w = cv2.copyMakeBorder(blurred, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=255)
    # 黑边：让亮色轮廓在图像边缘也能闭合
    brd_b = cv2.copyMakeBorder(blurred, pad, pad, pad, pad,
                                cv2.BORDER_CONSTANT, value=0)

    def _rect_from_padded(mask_brd, max_ratio=2.5):
        """从带 pad 的二值图找最大近似矩形（原图坐标）。"""
        closed  = cv2.morphologyEx(mask_brd, cv2.MORPH_CLOSE, k)
        inner   = closed[pad:-pad, pad:-pad]
        cnts, _ = cv2.findContours(inner, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
            a = cv2.contourArea(cnt)
            if not (min_a <= a <= max_a):
                continue
            hull  = cv2.convexHull(cnt)
            rect  = cv2.minAreaRect(hull)
            rw, rh = rect[1]
            ratio = max(rw, rh) / max(min(rw, rh), 1)
            if ratio > max_ratio:
                continue
            return cv2.boxPoints(rect).astype("float32")
        return None

    # ── 主策略：深色夹板检测 ────────────────────────────────────
    for thr in (90, 70, 110):
        _, dark = cv2.threshold(brd_w, thr, 255, cv2.THRESH_BINARY_INV)
        q = _rect_from_padded(dark)
        if q is not None:
            return q

    # ── 备用1：亮色纸张（超大核填满文字空隙）──────────────────
    ks2 = max(50, min(h, w) // 28)
    k2  = np.ones((ks2, ks2), np.uint8)

    def _paper_from_padded(mask_brd):
        closed  = cv2.morphologyEx(mask_brd, cv2.MORPH_CLOSE, k2)
        inner   = closed[pad:-pad, pad:-pad]
        cnts, _ = cv2.findContours(inner, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
        for cnt in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
            a = cv2.contourArea(cnt)
            if not (min_a <= a <= max_a):
                continue
            hull  = cv2.convexHull(cnt)
            rect  = cv2.minAreaRect(hull)
            rw, rh = rect[1]
            if max(rw, rh) / max(min(rw, rh), 1) > 2.5:
                continue
            peri = cv2.arcLength(hull, True)
            for tol in (0.02, 0.04, 0.06, 0.08):
                ap = cv2.approxPolyDP(hull, tol * peri, True)
                if len(ap) == 4:
                    return ap.reshape(4, 2).astype("float32")
            return cv2.boxPoints(rect).astype("float32")
        return None

    for thr in (185, 160, 140):
        _, bright = cv2.threshold(brd_b, thr, 255, cv2.THRESH_BINARY)
        q = _paper_from_padded(bright)
        if q is not None:
            return q

    # ── 备用2：Canny 兜底 ────────────────────────────────────────
    edges = cv2.Canny(brd_b, max(0.0, 0.3 * med), min(255.0, 1.5 * med))
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)
    return _paper_from_padded(edges)

def perspective_crop(frame, pts):
    """透视变换裁出纸张。"""
    rect = order_points(pts)
    tl, tr, br, bl = rect
    W = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
    H = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))
    if W < 50 or H < 50:
        return None
    dst = np.array([[0,0],[W-1,0],[W-1,H-1],[0,H-1]], dtype="float32")
    M   = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(frame, M, (W, H))

def auto_rotate_img(img):
    """横向图旋转为竖向（文档一般是竖的）。"""
    h, w = img.shape[:2]
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE) if w > h else img

def expand_quad(pts, expand=10):
    """将检测到的四边形各角向外扩 expand 像素，防止裁剪时切掉纸张边缘。"""
    cx = np.mean(pts[:, 0])
    cy = np.mean(pts[:, 1])
    result = pts.astype(float)
    for i in range(4):
        dx, dy = pts[i, 0] - cx, pts[i, 1] - cy
        dist = max(np.hypot(dx, dy), 1)
        result[i, 0] += dx / dist * expand
        result[i, 1] += dy / dist * expand
    return result.astype("float32")

def apply_clahe(frame):  # 保留供外部调用，界面已移除该选项
    """CLAHE 自动亮度均衡（LAB L通道）。"""
    lab      = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b  = cv2.split(lab)
    clahe    = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return cv2.cvtColor(cv2.merge([clahe.apply(l), a, b]), cv2.COLOR_LAB2BGR)

def scan_effect(bgr):
    """扫描件增强：逐通道光照归一化（消色偏）+ 背景白化 + 提对比 + 锐化。

    效果接近扫描仪「彩色文档」模式：纸底变纯白、文字更黑，
    红色印章/签名等有色内容保留（法律文件盖章需保留）。
    核尺寸随分辨率自适应，全幅原图与裁切图都适用。
    """
    h, w = bgr.shape[:2]
    ks = max(41, (max(h, w) // 45) | 1)   # 奇数，需大于最大文字笔画
    k  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
    normed = []
    for ch in cv2.split(bgr):
        # 每通道各除以自身纸底估计 -> 各通道背景都拉到 255 -> 中性白、去色偏
        bg = cv2.morphologyEx(ch, cv2.MORPH_CLOSE, k)      # 去掉暗文字，留纸张明暗
        bg = cv2.GaussianBlur(bg, (0, 0), ks / 2.0).astype(np.float32)
        n  = ch.astype(np.float32) * 255.0 / np.clip(bg, 40, None)
        normed.append(np.clip(n, 0, 255))
    out = cv2.merge(normed).astype(np.uint8)
    out = cv2.convertScaleAbs(out, alpha=1.15, beta=-14)   # 提对比、压灰底

    # 背景白化：亮且低饱和的像素 -> 纯白；保留深色文字与高饱和印章
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV)
    _, S, V = cv2.split(hsv)
    out[(V > 200) & (S < 28)] = (255, 255, 255)

    blur = cv2.GaussianBlur(out, (0, 0), 1.1)              # 轻度锐化
    return cv2.addWeighted(out, 1.4, blur, -0.4, 0)

def process_captured(frame, do_crop, do_rotate, do_scan=False):
    """拍照后处理管道：裁剪→旋转→扫描件增强。"""
    if do_crop:
        pts = detect_paper(frame)
        if pts is not None:
            h, w = frame.shape[:2]
            pts = expand_quad(pts, expand=10)
            # 限制扩展后的点不超出图像边界
            pts[:, 0] = np.clip(pts[:, 0], 0, w - 1)
            pts[:, 1] = np.clip(pts[:, 1], 0, h - 1)
            cropped = perspective_crop(frame, pts)
            if cropped is not None:
                frame = auto_rotate_img(cropped) if do_rotate else cropped
    if do_scan:
        frame = scan_effect(frame)
    return frame

# ── 摄像头进程检测 ───────────────────────────────────────────────

def list_capture_devices():
    """枚举本机视频采集设备，返回 [(索引, 名称), ...]，每个物理设备只保留第一个节点。"""
    devices = []
    sys_dir = "/sys/class/video4linux"
    try:
        entries = os.listdir(sys_dir)
    except FileNotFoundError:
        return devices
    for entry in sorted(entries, key=lambda n: int(n[5:]) if n[5:].isdigit() else 0):
        if not (entry.startswith("video") and entry[5:].isdigit()):
            continue
        base = os.path.join(sys_dir, entry)
        try:
            # 同一个 USB 摄像头会生成视频+元数据两个节点，index=0 的才是采集节点
            with open(os.path.join(base, "index")) as f:
                if f.read().strip() != "0":
                    continue
        except OSError:
            continue
        try:
            with open(os.path.join(base, "name")) as f:
                name = f.read().strip()
        except OSError:
            name = ""
        devices.append((int(entry[5:]), name or entry))
    return devices


def device_label(idx, name, max_chars=None):
    """摄像头在界面上的显示文字：「video2 · USB_2.0_Camera」。

    max_chars 给定时按字符数截断加省略号——侧栏宽度固定，设备名可以很长。
    """
    text = f"video{idx} · {name}" if name else f"video{idx}"
    if max_chars and len(text) > max_chars:
        text = text[:max(1, max_chars - 1)] + "…"
    return text


def device_display_name(device_path):
    """由设备路径反查显示名；查不到就退回路径本身。"""
    try:
        idx = int(os.path.realpath(str(device_path)).rsplit("video", 1)[-1])
    except (ValueError, AttributeError):
        return str(device_path)
    for i, name in list_capture_devices():
        if i == idx:
            return device_label(i, name)
    return f"video{idx}"


def choose_camera_dialog(devices, note=None, remember_init=False):
    """多个摄像头时弹窗让用户选择。

    返回 (索引, 是否固定)；用户关闭窗口返回 (None, False)。
    note 用于说明为什么又弹出来了（例如原先固定的设备不见了）。
    """
    dlg = tk.Tk(className="FlipScan")
    dlg.title(_t("选择摄像头", "Select Camera"))
    if note:
        # 提示单独一行并染成警示色，避免用户以为程序无缘无故又问一遍
        tk.Label(dlg, text=note, padx=12, pady=(8), justify="left",
                 fg="#b35c00", wraplength=460).pack(anchor="w")
    tk.Label(dlg, text=_t("检测到多个摄像头，请选择高拍仪：",
                          "Multiple cameras found. Please select the document camera:"),
             padx=12, pady=8, justify="left", wraplength=460).pack(anchor="w")
    box = tk.Listbox(dlg, width=58, height=max(2, min(8, len(devices))))
    for idx, name in devices:
        box.insert(tk.END, f"/dev/video{idx}  —  {name}")
    box.selection_set(0)
    box.pack(padx=12, pady=4)

    remember_var = tk.BooleanVar(value=bool(remember_init))
    tk.Checkbutton(dlg, variable=remember_var,
                   text=_t("记住我的选择，下次直接使用",
                           "Remember my choice and use it next time"),
                   padx=12, pady=2, anchor="w").pack(anchor="w")

    chosen = []
    # 勾选状态必须在 destroy 之前读出来存下：dlg 是根窗口，销毁后
    # Tcl 解释器就没了，再调 remember_var.get() 会抛 TclError。
    result = {"remember": False}

    def ok(*_):
        sel = box.curselection()
        if sel:
            chosen.append(devices[sel[0]][0])
        result["remember"] = bool(remember_var.get())
        dlg.destroy()

    box.bind("<Double-Button-1>", ok)
    tk.Button(dlg, text=_t("确定", "OK"), command=ok, padx=24).pack(pady=8)
    dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
    dlg.mainloop()
    if not chosen:
        return None, False
    return chosen[0], result["remember"]


def select_camera_interactive(note=None, remember_init=False):
    """auto 模式选摄像头：名称像高拍仪的优先，唯一设备直接用，多个弹窗选。

    返回 (摄像头索引或 /dev 路径, 是否固定)；用户关闭选择窗口返回 (None, False)。
    「是否固定」只有真的弹窗问过才是 bool，自动挑中的返回 None
    表示「用户没表态」——没问过就别替用户改配置。
    note 非空时跳过自动挑选直接弹窗：调用方已经知道需要用户重新选。
    """
    if note:
        devices = list_capture_devices()
        if devices:
            return choose_camera_dialog(devices, note=note,
                                        remember_init=remember_init)
        # 一个设备都没有时无从选起，退回下面的默认逻辑
    by_id = "/dev/v4l/by-id"
    try:
        for name in sorted(os.listdir(by_id)):
            if not name.endswith("video-index0"):
                continue
            if any(hint in name for hint in HIGH_CAMERA_HINTS):
                return os.path.realpath(os.path.join(by_id, name)), None
    except FileNotFoundError:
        pass
    devices = list_capture_devices()
    if not devices:
        return 0, None
    if len(devices) == 1:
        return devices[0][0], None
    return choose_camera_dialog(devices, remember_init=remember_init)


def resolve_camera_source(camera="auto"):
    """把 --camera 参数解析成 OpenCV 可用的设备索引或路径。"""
    if camera in (None, "", "auto"):
        by_id = "/dev/v4l/by-id"
        try:
            for name in sorted(os.listdir(by_id)):
                if not name.endswith("video-index0"):
                    continue
                if any(hint in name for hint in HIGH_CAMERA_HINTS):
                    return os.path.realpath(os.path.join(by_id, name))
        except FileNotFoundError:
            pass
        devices = list_capture_devices()
        if devices:
            return devices[0][0]
        return 0
    if isinstance(camera, str):
        if camera.startswith("/dev/"):
            return os.path.realpath(camera)
        try:
            return int(camera)
        except ValueError:
            return camera
    return camera

def camera_device_path(camera):
    source = resolve_camera_source(camera)
    if isinstance(source, int):
        return f"/dev/video{source}"
    return os.path.realpath(str(source))

def find_camera_users(camera=0):
    """返回占用指定摄像头设备的其他进程 PID 列表。"""
    device = camera_device_path(camera)
    pids   = []
    ancestors = {os.getpid()}
    ppid = os.getppid()
    while ppid and ppid not in ancestors:
        ancestors.add(ppid)
        try:
            with open(f"/proc/{ppid}/stat") as f:
                ppid = int(f.read().split()[3])
        except Exception:
            break
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            pid_i = int(pid)
            if pid_i in ancestors:
                continue
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    if os.path.realpath(os.readlink(f"{fd_dir}/{fd}")) == device:
                        pids.append(pid_i)
                        break
            except (PermissionError, FileNotFoundError, OSError):
                pass
            cmdline = f"/proc/{pid}/cmdline"
            try:
                exe = os.path.basename(os.path.realpath(f"/proc/{pid}/exe"))
                cmd = open(cmdline, "rb").read().replace(b"\0", b" ").decode(errors="ignore")
                # 有些 ffmpeg/v4l2 进程通过命令行持有设备，但 /proc/*/fd 扫描不一定稳定显示。
                # 只把已知采集进程纳入兜底，避免误杀启动本程序的 shell/Hermes 进程。
                if exe in {"ffmpeg", "ffplay", "v4l2-ctl", "gst-launch-1.0"} and device in cmd and pid_i not in pids:
                    pids.append(pid_i)
            except (PermissionError, FileNotFoundError, OSError):
                pass
    except Exception:
        pass
    return [p for p in pids if p not in ancestors]

def force_take_camera(camera=0):
    """SIGTERM/SIGKILL 占用摄像头的其他进程。"""
    pids = find_camera_users(camera)
    for p in pids:
        try: os.kill(p, signal.SIGTERM)
        except ProcessLookupError: pass
    if pids:
        time.sleep(0.6)
    for p in pids:
        try: os.kill(p, signal.SIGKILL)
        except ProcessLookupError: pass
    return bool(pids)

def describe_pids(pids):
    lines = []
    for pid in pids:
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read()
            cmd = cmd.replace(b"\0", b" ").decode(errors="ignore").strip()
        except Exception:
            cmd = ""
        lines.append(f"PID {pid}: {cmd or _t('未知进程', 'unknown process')}")
    return "\n".join(lines)

# ── 良田 / 锁 ────────────────────────────────────────────────────

def find_eloam_pids():
    try:
        return [int(p) for p in
                subprocess.check_output(["pgrep", "-f", "eloamscanner"],
                                        text=True).split()]
    except subprocess.CalledProcessError:
        return []

def stop_eloam():
    pids = find_eloam_pids()
    if not pids:
        return False
    for p in pids:
        try: os.kill(p, signal.SIGTERM)
        except ProcessLookupError: pass
    time.sleep(1)
    for p in pids:
        try: os.kill(p, signal.SIGKILL)
        except ProcessLookupError: pass
    return True

def restart_eloam():
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":1")}
    subprocess.Popen([ELOAM_CMD], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, env=env)

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            old = int(open(LOCK_FILE).read().strip())
            os.kill(old, signal.SIGTERM)
            time.sleep(0.5)
            try: os.kill(old, signal.SIGKILL)
            except ProcessLookupError: pass
        except (ValueError, ProcessLookupError):
            pass
    open(LOCK_FILE, "w").write(str(os.getpid()))

def release_lock():
    try: os.remove(LOCK_FILE)
    except FileNotFoundError: pass

# ── 摄像头线程（支持暂停/恢复）─────────────────────────────────

class CameraThread:
    def __init__(self, idx="auto"):
        self._idx    = resolve_camera_source(idx)
        self._lock   = threading.Lock()   # 保护 _frame / cap / _idx，临界区极短
        # _io 在真正调用 cap.read() 和 release() 期间持有：只有这样才能保证
        # 释放句柄时采集线程一定不在 read() 里面，否则热切换会读已释放的句柄崩掉。
        self._io     = threading.Lock()
        # 采集线程一直在抢 _io，切换方可能长期抢不到；先立这个标志让采集线程
        # 主动退让，切换完再清掉。
        self._yield  = threading.Event()
        self._stop   = threading.Event()
        self._paused = False
        self.cap     = None
        self._frame  = np.zeros((2448, 3264, 3), dtype=np.uint8)
        if not self._open():
            raise RuntimeError(_t(f"无法打开摄像头 {idx}", f"Cannot open camera {idx}"))
        threading.Thread(target=self._loop, daemon=True).start()

    def _open(self):
        if isinstance(self._idx, int):
            cap = cv2.VideoCapture(self._idx, cv2.CAP_V4L2)
        else:
            cap = cv2.VideoCapture(self._idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            return False
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  3264)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2448)
        ok, frame = cap.read()
        if not ok:
            cap.release()
            return False
        with self._lock:
            self.cap    = cap
            self._frame = frame
        return True

    def _close(self):
        # 先摘掉 self.cap 再 release：采集线程只要在 _io 之外就再也拿不到旧句柄，
        # 而 _io 保证它此刻不在 read() 中。
        self._yield.set()
        try:
            with self._io:
                with self._lock:
                    cap, self.cap = self.cap, None
                if cap:
                    cap.release()
        finally:
            self._yield.clear()

    def pause(self):
        self._paused = True
        self._close()

    def resume(self):
        self._paused = False
        return self._open()

    def switch(self, source):
        """热切换到另一个摄像头，成功返回 True。

        失败（设备被占用/拔掉/打不开）时回滚到原设备，让实时画面继续跑，
        而不是把用户丢在一个黑屏且没有摄像头的界面里。
        """
        new_idx = resolve_camera_source(source)
        with self._lock:
            old_idx = self._idx
        if new_idx == old_idx:
            return True
        self._close()
        with self._lock:
            self._idx = new_idx
        if self._paused:
            # 让出摄像头期间只记住选择，等 resume() 时再真正打开
            return True
        if self._open():
            return True
        with self._lock:
            self._idx = old_idx
        self._open()
        return False

    def _loop(self):
        while not self._stop.is_set():
            if self._paused or self._yield.is_set():
                time.sleep(0.05)
                continue
            with self._io:
                with self._lock:
                    cap = self.cap
                if cap is None:
                    ok, f = False, None
                else:
                    ok, f = cap.read()
            if ok:
                with self._lock:
                    self._frame = f
            elif cap is None:
                time.sleep(0.1)

    def read(self):
        with self._lock:
            return self._frame.copy()

    def release(self):
        self._stop.set()
        time.sleep(0.2)
        self._close()

# ── 运动检测 / 存图 ──────────────────────────────────────────────

def motion_score(f1, f2):
    s1 = cv2.resize(f1, DETECT_RES)
    s2 = cv2.resize(f2, DETECT_RES)
    g1 = cv2.GaussianBlur(cv2.cvtColor(s1, cv2.COLOR_BGR2GRAY), (11, 11), 0)
    g2 = cv2.GaussianBlur(cv2.cvtColor(s2, cv2.COLOR_BGR2GRAY), (11, 11), 0)
    _, th = cv2.threshold(cv2.absdiff(g1, g2), DIFF_THRESH, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(th)

def save_frame(frame, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{_t('试卷', 'scan')}_{ts}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path

def friendly_photo_name(path):
    """把「试卷_20260703_214532.jpg」这类文件名转成更易读的时间显示，解析失败则回退原文件名。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split("_")
    if len(parts) >= 2 and len(parts[-2]) == 8 and len(parts[-1]) == 6 \
            and parts[-2].isdigit() and parts[-1].isdigit():
        d, t = parts[-2], parts[-1]
        return f"{d[4:6]}-{d[6:8]} {t[0:2]}:{t[2:4]}:{t[4:6]}"
    return os.path.basename(path)

def hist_header_text(prefix, n):
    """历史照片区标题文字（▶/▼ + 名称 + 数量），中英统一在此生成。"""
    if n:
        return _t(f"{prefix}  历史照片（{n}张）", f"{prefix}  History ({n})")
    return _t(f"{prefix}  历史照片（空）", f"{prefix}  History (empty)")

# ── 摄像头共享状态提示（嵌入实时画面右下角，不自动消失）───────

class CameraBanner:
    """嵌在预览区右下角的摄像头共享状态提示（状态机，不自动消失）。"""

    def __init__(self, preview_frame, root):
        self._pf       = preview_frame
        self._root     = root
        self._w        = None
        self._blink_id = None

    # ── 内部 ──────────────────────────────────────────────────

    def _ensure(self):
        if self._w and self._w.winfo_exists():
            return
        self._w = tk.Frame(self._pf, bg=C["sbg"],
                            highlightthickness=1, highlightbackground=C["ambe"])
        self._w.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)
        self._title = tk.Label(self._w, font=("Sans", 10, "bold"), bg=C["sbg"])
        self._title.pack(padx=12, pady=(10, 2), anchor="w")
        self._sub = tk.Label(self._w, font=("Sans", 8), fg=C["text"], bg=C["sbg"])
        self._sub.pack(padx=12, anchor="w")
        self._bf = tk.Frame(self._w, bg=C["sbg"])
        self._bf.pack(fill=tk.X, padx=12, pady=(6, 10))

    def _clear_btns(self):
        for w in self._bf.winfo_children():
            w.destroy()

    def _btn(self, text, fg, bg, abg, cmd, pad_left=0):
        b = tk.Button(self._bf, text=text, font=("Sans", 9),
                      fg=fg, bg=bg, activeforeground=C["lit"], activebackground=abg,
                      relief=tk.FLAT, bd=0, padx=10, pady=4, cursor="hand2",
                      command=cmd)
        b.pack(side=tk.LEFT, padx=(pad_left, 0))
        return b

    def _stop_blink(self):
        if self._blink_id:
            try: self._root.after_cancel(self._blink_id)
            except Exception: pass
            self._blink_id = None

    # ── 公开状态方法 ──────────────────────────────────────────

    def show_conflict(self, on_force, on_wait):
        """摄像头被占用 → 强制切换 / 等待自动恢复"""
        self._stop_blink()
        self._ensure()
        self._w.configure(highlightbackground=C["ambe"])
        self._title.config(text=_t("⚠  摄像头正被其他应用占用",
                                   "⚠  Camera is in use by another app"), fg=C["ambe"])
        self._sub.config(text="")
        self._clear_btns()
        self._btn(_t("强制切换", "Take over"), "white", C["red"], "#cc1133", on_force)
        self._btn(_t("等待自动恢复", "Wait for release"),
                  C["mid"], C["cbg"], C["dim"], on_wait, pad_left=6)

    def show_monitoring(self):
        """监控其他应用是否退出（闪烁动画）"""
        self._stop_blink()
        self._ensure()
        self._w.configure(highlightbackground=C["cyan"])
        self._sub.config(text=_t("其他应用退出后将自动恢复",
                                 "Will resume once the other app exits"))
        self._clear_btns()
        _s = {"v": False}
        def _blink():
            if not (self._w and self._w.winfo_exists()):
                return
            _s["v"] = not _s["v"]
            self._title.config(
                text=f"{'●' if _s['v'] else '○'}  "
                     + _t("等待摄像头释放中...", "Waiting for camera to be released..."),
                fg=C["cyan"] if _s["v"] else C["mid"])
            self._blink_id = self._root.after(800, _blink)
        _blink()

    def show_forced(self, on_give_back):
        """已强制夺回 → 可让出摄像头"""
        self._stop_blink()
        self._ensure()
        self._w.configure(highlightbackground=C["glow"])
        self._title.config(text=_t("✓  摄像头已夺回", "✓  Camera taken over"), fg=C["glow"])
        self._sub.config(text=_t("其他应用当前无法使用摄像头",
                                 "Other apps cannot use the camera right now"))
        self._clear_btns()
        self._btn(_t("让出摄像头", "Give back"), C["mid"], C["cbg"], C["dim"], on_give_back)
        self._btn(_t("关闭", "Close"), C["mid"], C["dim"], C["mid"], self.hide, pad_left=6)

    def show_error(self, sub):
        """一次性错误提示（如切换摄像头失败），不自动消失，用户手动关闭。"""
        self._stop_blink()
        self._ensure()
        self._w.configure(highlightbackground=C["red"])
        self._title.config(text=_t("⚠  切换摄像头失败", "⚠  Camera switch failed"),
                           fg=C["red"])
        self._sub.config(text=sub)
        self._clear_btns()
        self._btn(_t("关闭", "Close"), C["mid"], C["dim"], C["mid"], self.hide)

    def show_restored(self):
        """自动恢复，不自动消失"""
        self._stop_blink()
        self._ensure()
        self._w.configure(highlightbackground=C["glow"])
        self._title.config(text=_t("✓  摄像头已自动恢复", "✓  Camera restored"), fg=C["glow"])
        self._sub.config(text=_t("其他应用已退出", "The other app has exited"))
        self._clear_btns()
        self._btn(_t("关闭", "Close"), C["mid"], C["dim"], C["mid"], self.hide)

    def hide(self):
        self._stop_blink()
        try:
            if self._w and self._w.winfo_exists():
                self._w.place_forget()
                self._w.destroy()
        except Exception:
            pass
        self._w = None

    def visible(self):
        return bool(self._w and self._w.winfo_exists())

# ── 文件夹选择对话框 ─────────────────────────────────────────────

class FolderPicker(tk.Toplevel):
    def __init__(self, parent, init_dir, callback):
        super().__init__(parent)
        self._cb  = callback
        self._dir = init_dir if os.path.isdir(init_dir) else os.path.expanduser("~")
        self.title(_t("选择保存文件夹", "Choose Save Folder"))
        self.configure(bg=C["sbg"])
        self.geometry("620x440")
        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        px = parent.winfo_x() + parent.winfo_width()  // 2 - 310
        py = parent.winfo_y() + parent.winfo_height() // 2 - 220
        self.geometry(f"+{px}+{py}")
        self._build()
        self._refresh()

    def _marks(self):
        h = os.path.expanduser("~")

        def _xdg(var, names):
            """优先读 XDG 用户目录环境变量，回退到常见的中英文目录名。"""
            p = os.environ.get(var)
            if p and os.path.isdir(os.path.expanduser(p)):
                return os.path.expanduser(p)
            return next((os.path.join(h, d) for d in names
                         if os.path.isdir(os.path.join(h, d))), None)

        desk = _xdg("XDG_DESKTOP_DIR",   ("桌面", "Desktop"))
        docs = _xdg("XDG_DOCUMENTS_DIR", ("文档", "Documents"))
        dl   = _xdg("XDG_DOWNLOAD_DIR",  ("下载", "Downloads"))
        m = [(_t("主目录", "Home"), h)]
        if desk: m.append((_t("桌面", "Desktop"), desk))
        if docs: m.append((_t("文档", "Documents"), docs))
        if dl:   m.append((_t("下载", "Downloads"), dl))
        if os.path.isdir(DEFAULT_DIR):
            m.append((_t("拍照结果", "Captures"), DEFAULT_DIR))
        return m

    def _build(self):
        tk.Label(self, text=_t("选择保存文件夹", "Choose Save Folder"),
                 font=("Sans", 13, "bold"), fg=C["cyan"], bg=C["sbg"]
                 ).pack(padx=16, pady=(14, 8), anchor="w")

        nav = tk.Frame(self, bg=C["sbg"])
        nav.pack(fill=tk.X, padx=12, pady=(0, 6))
        tk.Button(nav, text=_t("← 上级", "← Up"), font=("Sans", 10),
                  fg=C["text"], bg=C["cbg"],
                  activeforeground=C["lit"], activebackground=C["dim"],
                  relief=tk.FLAT, bd=0, padx=10, pady=5, cursor="hand2",
                  command=self._up).pack(side=tk.LEFT, padx=(0, 8))
        self._path_var = tk.StringVar()
        entry = tk.Entry(nav, textvariable=self._path_var,
                         font=("Monospace", 9), fg=C["lit"], bg=C["clow"],
                         insertbackground=C["cyan"], relief=tk.FLAT, bd=0)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5, padx=(0,6))
        entry.bind("<Return>", self._path_enter)
        tk.Button(nav, text=_t("跳转", "Go"), font=("Sans", 9),
                  fg=C["cyan"], bg=C["clow"],
                  activeforeground=C["wh"], activebackground=C["cyan"],
                  relief=tk.FLAT, bd=0, padx=8, pady=5, cursor="hand2",
                  command=self._path_enter).pack(side=tk.LEFT)

        body = tk.Frame(self, bg=C["sbg"])
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

        # 宽度留足余量：英文 "Documents"/"Downloads" 比中文标签长
        bk = tk.Frame(body, bg=C["cbg"], width=145,
                      highlightthickness=1, highlightbackground=C["line"])
        bk.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        bk.pack_propagate(False)
        tk.Label(bk, text=_t("快速访问", "Quick access"), font=("Sans", 8),
                 fg=C["mid"], bg=C["cbg"]).pack(pady=(8,4), padx=8, anchor="w")
        for name, path in self._marks():
            tk.Button(bk, text=f"  {name}", font=("Sans", 10),
                      fg=C["text"], bg=C["cbg"],
                      activeforeground=C["cyan"], activebackground=C["clow"],
                      relief=tk.FLAT, bd=0, pady=7, anchor="w", cursor="hand2",
                      command=lambda p=path: self._goto(p)
                      ).pack(fill=tk.X, padx=4)

        rf = tk.Frame(body, bg=C["cbg"],
                      highlightthickness=1, highlightbackground=C["line"])
        rf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = tk.Scrollbar(rf, bg=C["cbg"], troughcolor=C["sbg"])
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._lb = tk.Listbox(rf, bg=C["cbg"], fg=C["text"],
                               font=("Sans", 11),
                               selectbackground=C["clow"], selectforeground=C["cyan"],
                               activestyle="none", relief=tk.FLAT, bd=0,
                               highlightthickness=0, yscrollcommand=sb.set)
        self._lb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        sb.config(command=self._lb.yview)
        self._lb.bind("<Double-Button-1>", self._enter)

        bf = tk.Frame(self, bg=C["sbg"])
        bf.pack(fill=tk.X, padx=12, pady=(0,12))
        tk.Button(bf, text=_t("+ 新建文件夹", "+ New Folder"), font=("Sans", 10),
                  fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["lit"], activebackground=C["dim"],
                  relief=tk.FLAT, bd=0, padx=12, pady=8, cursor="hand2",
                  command=self._mkdir).pack(side=tk.LEFT)
        tk.Button(bf, text=_t("取消", "Cancel"), font=("Sans", 10),
                  fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["wh"], activebackground=C["dim"],
                  relief=tk.FLAT, bd=0, padx=18, pady=8, cursor="hand2",
                  command=self.destroy).pack(side=tk.RIGHT, padx=(6,0))
        tk.Button(bf, text=_t("选择此文件夹", "Select This Folder"),
                  font=("Sans", 10, "bold"), fg=C["bg"], bg=C["cyan"],
                  activeforeground=C["bg"], activebackground=C["lit"],
                  relief=tk.FLAT, bd=0, padx=18, pady=8, cursor="hand2",
                  command=self._confirm).pack(side=tk.RIGHT)

    def _refresh(self):
        self._path_var.set(self._dir)
        self._lb.delete(0, tk.END)
        try:
            dirs = sorted([d for d in os.listdir(self._dir)
                           if os.path.isdir(os.path.join(self._dir, d))
                           and not d.startswith(".")])
            for d in dirs:
                self._lb.insert(tk.END, f"  {d}")
            if not dirs:
                self._lb.insert(tk.END, _t("  （没有子文件夹）", "  (No subfolders)"))
        except PermissionError:
            self._lb.insert(tk.END, _t("  （无权限访问）", "  (Permission denied)"))

    def _up(self):
        p = os.path.dirname(self._dir)
        if p != self._dir:
            self._dir = p; self._refresh()

    def _goto(self, path):
        self._dir = path; self._refresh()

    def _enter(self, _=None):
        sel = self._lb.curselection()
        if not sel: return
        name = self._lb.get(sel[0]).strip()
        path = os.path.join(self._dir, name)
        if os.path.isdir(path):
            self._dir = path; self._refresh()

    def _path_enter(self, _=None):
        p = self._path_var.get().strip()
        if os.path.isdir(p):
            self._dir = p; self._refresh()

    def _mkdir(self):
        dlg = tk.Toplevel(self)
        dlg.title(_t("新建文件夹", "New Folder")); dlg.configure(bg=C["sbg"])
        dlg.geometry(_t("360x140", "470x160")); dlg.transient(self); dlg.grab_set()
        dlg.resizable(False, False)
        tk.Label(dlg, text=_t("文件夹名称：", "Folder name:"), font=("Sans", 10),
                 fg=C["text"], bg=C["sbg"]).pack(padx=16, pady=(16,4), anchor="w")
        var   = tk.StringVar()
        entry = tk.Entry(dlg, textvariable=var, font=("Sans", 11),
                         fg=C["lit"], bg=C["cbg"],
                         insertbackground=C["cyan"], relief=tk.FLAT, bd=0)
        entry.pack(fill=tk.X, padx=16, ipady=6); entry.focus_set()
        def do(_=None):
            name = var.get().strip()
            if name:
                path = os.path.join(self._dir, name)
                os.makedirs(path, exist_ok=True)
                self._dir = path; self._refresh()
            dlg.destroy()
        entry.bind("<Return>", do)
        bf2 = tk.Frame(dlg, bg=C["sbg"]); bf2.pack(fill=tk.X, padx=16, pady=10)
        tk.Button(bf2, text=_t("取消", "Cancel"), command=dlg.destroy, font=("Sans",10),
                  fg=C["mid"], bg=C["cbg"], relief=tk.FLAT, bd=0, padx=12, pady=6
                  ).pack(side=tk.RIGHT, padx=(6,0))
        tk.Button(bf2, text=_t("创建", "Create"), command=do, font=("Sans",10,"bold"),
                  fg=C["bg"], bg=C["cyan"], relief=tk.FLAT, bd=0, padx=12, pady=6
                  ).pack(side=tk.RIGHT)

    def _confirm(self):
        self._cb(self._dir); self.destroy()

# ── 摄像头选择对话框（界面内切换用）─────────────────────────────

class CameraPicker(tk.Toplevel):
    """运行中切换摄像头的小窗：设备列表 + 「固定」勾选。

    与启动时的 choose_camera_dialog 分开实现：那个要自建 Tk 根窗口并跑自己的
    mainloop（主界面还不存在），这个挂在主窗口上、用主界面的深色配色。
    """

    def __init__(self, parent, current_path, remember_init, callback):
        super().__init__(parent)
        self._cb      = callback
        self._devices = list_capture_devices()
        self.title(_t("选择摄像头", "Select Camera"))
        self.configure(bg=C["sbg"])
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._remember = tk.BooleanVar(value=bool(remember_init))
        self._build(current_path)
        self.update_idletasks()
        # 居中到主窗口
        px = parent.winfo_x() + parent.winfo_width()  // 2 - self.winfo_width()  // 2
        py = parent.winfo_y() + parent.winfo_height() // 2 - self.winfo_height() // 2
        self.geometry(f"+{max(px, 0)}+{max(py, 0)}")

    def _build(self, current_path):
        # 中英文宽度分开给：英文说明比中文长得多，共用一个宽度必然有一边被挤
        wrap_px = _t(360, 430)
        tk.Label(self, text=_t("选择摄像头", "Select Camera"),
                 font=("Sans", 13, "bold"), fg=C["cyan"], bg=C["sbg"]
                 ).pack(padx=16, pady=(14, 4), anchor="w")
        tk.Label(self, text=_t("切换后实时画面会立即换过去，无需重启程序。",
                               "The live view switches over immediately; no restart needed."),
                 font=("Sans", 9), fg=C["text"], bg=C["sbg"],
                 wraplength=wrap_px, justify="left"
                 ).pack(padx=16, pady=(0, 8), anchor="w")

        box_w = _t(34, 42)      # 以字符计的列表宽度，英文设备说明更长
        frame = tk.Frame(self, bg=C["cbg"],
                         highlightthickness=1, highlightbackground=C["line"])
        frame.pack(fill=tk.BOTH, expand=True, padx=16)
        self._lb = tk.Listbox(frame, bg=C["cbg"], fg=C["text"], font=("Sans", 10),
                              width=box_w, height=max(2, min(8, len(self._devices) or 2)),
                              selectbackground=C["clow"], selectforeground=C["cyan"],
                              activestyle="none", relief=tk.FLAT, bd=0,
                              highlightthickness=0)
        self._lb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        cur_idx = None
        try:
            cur_idx = int(os.path.realpath(str(current_path)).rsplit("video", 1)[-1])
        except ValueError:
            pass
        for row, (idx, name) in enumerate(self._devices):
            mark = "● " if idx == cur_idx else "  "
            # 列表宽度固定，长设备名在这里就截断，不让它把窗口撑宽
            self._lb.insert(tk.END, mark + device_label(idx, name, max_chars=box_w - 3))
            if idx == cur_idx:
                self._lb.selection_set(row)
        if not self._devices:
            self._lb.insert(tk.END, _t("  （没有找到摄像头）", "  (No cameras found)"))
        if not self._lb.curselection() and self._devices:
            self._lb.selection_set(0)
        self._lb.bind("<Double-Button-1>", lambda _: self._confirm())

        # 「固定」勾选：深色主题下自绘 ✓/○，与侧栏选项按钮同款
        self._rb = tk.Button(self, font=("Sans", 10), anchor="w",
                             bg=C["sbg"], activebackground=C["sbg"],
                             relief=tk.FLAT, bd=0, highlightthickness=0,
                             cursor="hand2", command=self._toggle_remember)
        self._rb.pack(fill=tk.X, padx=16, pady=(10, 0))
        self._sync_remember()

        bf = tk.Frame(self, bg=C["sbg"])
        bf.pack(fill=tk.X, padx=16, pady=(10, 14))
        tk.Button(bf, text=_t("取消", "Cancel"), font=("Sans", 10),
                  fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["wh"], activebackground=C["dim"],
                  relief=tk.FLAT, bd=0, padx=18, pady=8, cursor="hand2",
                  command=self.destroy).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(bf, text=_t("切换到这个摄像头", "Switch to this camera"),
                  font=("Sans", 10, "bold"), fg=C["bg"], bg=C["cyan"],
                  activeforeground=C["bg"], activebackground=C["lit"],
                  relief=tk.FLAT, bd=0, padx=18, pady=8, cursor="hand2",
                  command=self._confirm).pack(side=tk.RIGHT)

    def _sync_remember(self):
        on = self._remember.get()
        self._rb.config(
            text=("✓  " if on else "○  ")
                 + _t("固定这个摄像头（下次启动直接用）",
                      "Remember this camera for next launch"),
            fg=C["glow"] if on else C["mid"])

    def _toggle_remember(self):
        self._remember.set(not self._remember.get())
        self._sync_remember()

    def _confirm(self):
        sel = self._lb.curselection()
        if not sel or not self._devices:
            self.destroy()
            return
        idx = self._devices[sel[0]][0]
        remember = bool(self._remember.get())
        self.destroy()               # 先关窗，切换过程中不留一个僵着的对话框
        self._cb(idx, remember)

# ── 主应用 ──────────────────────────────────────────────────────

class App:
    SW = 340
    THUMB_COLS = 2   # 历史照片网格列数（竖版文档多列显示，一屏放更多）
    CUR_COLS   = 1   # 本次拍摄列数：一行一张，突出显示刚拍的照片

    def __init__(self, root, cam, eloam_was_running):
        self.root              = root
        self.cam               = cam
        self.cam_idx           = cam._idx
        self.eloam_was_running = eloam_was_running
        # 摄像头设置：界面里换摄像头 / 固定摄像头都要落到这份配置
        self.cfg           = load_config()
        apply_tuning(self.cfg)   # 把用户调过的检测参数应用到全局常量
        saved_dir = (self.cfg.get("output_dir") or "").strip()
        self.output_dir = (
            saved_dir if saved_dir and os.path.isdir(saved_dir) else DEFAULT_DIR)
        self.remember_var  = None   # 在 _build 里创建
        self._cam_picker   = None   # 当前打开的摄像头选择窗，避免重复弹
        self._more_dialog  = None   # 当前打开的设置窗，避免重复弹

        self.state        = State.IDLE
        self.recording    = False
        self.stable_t     = None
        self.flip_start   = None
        self.peak_motion  = 0
        self.count        = 0
        self.saved        = []
        self.prev         = cam.read()
        self.running      = True

        self._refs           = []
        self._hist_refs      = []
        self._motion_buf     = deque(maxlen=MOTION_SMOOTH)
        self._load_id        = 0
        self._photo_cards    = []   # [(frame, path, pack_kw), ...]
        self._recent_paths   = []
        self._older_paths    = []
        self._history_snapshot = None
        self._history_poll_job = None
        self._cam_bg         = False
        self._cam_monitoring = False

        self._selected_paths = set()  # 多选删除
        self._select_controls = {}    # {照片路径: [(card, set_checked), ...]}，同步同一路径的所有选择框
        self._overlay        = None   # 当前打开的全屏预览覆盖层
        self._preview_view_state = {}  # {照片路径: {"scale","ox","oy"}}，记住每张照片上次的缩放/平移
        self._preview_active_path = None  # 当前正在全屏预览的照片路径
        self._preview_close_cb    = None  # 预览层关闭回调
        self._preview_save_cb     = None  # 切换照片/返回实时前保存当前缩放和平移

        # 实时直播画面的缩放/平移状态
        self._live_zoom = 1.0
        self._live_ox   = 0.0   # 源画面像素坐标系下的裁剪中心偏移
        self._live_oy   = 0.0
        self._live_drag = None

        # Tkinter 变量（在 _build 里创建）
        self.auto_crop_var   = None
        self.auto_rotate_var = None
        self.scan_var        = None

        self._build()
        self._cam_banner = CameraBanner(self._pf, root)  # 嵌入预览区
        root.protocol("WM_DELETE_WINDOW", self._quit)
        root.after(50,  self._tick)
        root.after(500, self._blink_tick)
        root.after(600, self._initial_load_history)
        root.after(2000, self._poll_history_changes)

        # 摄像头共享：最小化→让出，恢复→夺回（不因其他窗口叠加触发）
        root.bind("<Unmap>", self._on_iconify)
        root.bind("<Map>",   self._on_restore)

    # ── 构建界面 ─────────────────────────────────────────────

    def _build(self):
        r = self.root
        # 窗口标题固定英文，不跟随语言：名字是程序在系统里的身份。界面内容仍按 locale 适配。
        r.title("FlipScan · ENum")
        r.configure(bg=C["bg"])
        r.geometry("1360x860")
        r.minsize(960, 620)

        # 侧栏（先 pack）
        side = tk.Frame(r, width=self.SW, bg=C["sbg"],
                        highlightthickness=1, highlightbackground=C["line"])
        side.pack(side=tk.RIGHT, fill=tk.Y)
        side.pack_propagate(False)

        # 预览区
        pf = tk.Frame(r, bg=C["bg"])
        pf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        pf.pack_propagate(False)
        self._pf = pf

        self.preview = tk.Label(pf, bg=C["bg"])
        self.preview.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.preview.bind("<MouseWheel>",      self._live_scroll)
        self.preview.bind("<Button-4>",        self._live_scroll)
        self.preview.bind("<Button-5>",        self._live_scroll)
        self.preview.bind("<ButtonPress-1>",   self._live_drag_start)
        self.preview.bind("<B1-Motion>",       self._live_drag_move)
        self.preview.bind("<ButtonRelease-1>", self._live_drag_end)
        self.preview.bind("<Double-Button-1>", self._live_zoom_reset)

        # 左上：录制状态
        self._rec_lbl = tk.Label(pf, text=_t("◉  待机", "◉  Idle"),
                                  font=("Sans", 11, "bold"),
                                  fg=C["dim"], bg=C["bg"])
        self._rec_lbl.place(x=12, y=10)

        # 右上 HUD：仅 ⊙ 拍照 按钮
        hud_b = tk.Frame(pf, bg=C["line"], padx=1, pady=1)
        hud_b.place(relx=1.0, y=12, anchor="ne", x=-12)
        hud = tk.Frame(hud_b, bg="#060d18")
        hud.pack()
        self._hud_cap_btn = tk.Button(
            hud, text=_t("⊙ 拍照", "⊙ Capture"),
            font=("Monospace", 10, "bold"),
            fg=C["cyan"], bg="#060d18",
            activeforeground=C["wh"], activebackground="#0a1830",
            relief=tk.FLAT, bd=0, padx=12, pady=8, cursor="hand2",
            command=self._manual
        )
        self._hud_cap_btn.pack()

        self._build_sidebar(side)

    def _build_sidebar(self, s):
        p = dict(padx=14)

        # ── 标题 ─────────────────────────────────────────────
        tk.Frame(s, height=6, bg=C["sbg"]).pack()
        hdr = tk.Canvas(s, width=self.SW - 20, height=34,
                         bg=C["sbg"], highlightthickness=0)
        hdr.pack(padx=10)
        w, bl = self.SW - 20, 10
        for x1,y1,x2,y2 in [(0,0,bl,0),(0,0,0,bl),(w,0,w-bl,0),(w,0,w,bl),
                              (0,33,bl,33),(0,33,0,33-bl),(w,33,w-bl,33),(w,33,w,33-bl)]:
            hdr.create_line(x1,y1,x2,y2, fill=C["cyan"], width=2)
        # 应用内标题跟窗口标题、任务栏名称保持一致，固定英文。
        hdr.create_text(w//2, 17, text="Flipscan  ·  ENum",
                         font=("Sans", 11, "bold"), fill=C["cyan"])

        self._sep(s, pady=5)

        # ── 保存位置（去掉单独的标题行，靠路径本身+提示色即可辨认）──
        top_row = tk.Frame(s, bg=C["sbg"])
        top_row.pack(fill=tk.X, padx=10)
        db = tk.Frame(top_row, bg=C["line"], padx=1, pady=1)
        db.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._dir_lbl = tk.Label(db, text=self._short_path(self.output_dir),
                                  font=("Sans", 9), fg=C["cyan"], bg=C["cbg"],
                                  anchor="w", padx=6, pady=4,
                                  wraplength=self.SW - 150, justify="left",
                                  cursor="hand2")
        self._dir_lbl.pack(fill=tk.BOTH, expand=True)
        self._dir_lbl.bind("<Button-1>", lambda _: self._open_folder())
        tk.Button(top_row, text=_t("更改", "Change"), font=("Sans", 9, "bold"),
                  fg=C["cyan"], bg=C["clow"],
                  activeforeground=C["wh"], activebackground=C["cyan"],
                  relief=tk.FLAT, bd=0, padx=10, pady=4, cursor="hand2",
                  command=self._change_folder
                  ).pack(side=tk.RIGHT, padx=(4,0))


        # ── 开始/停止 ──────────────────────────────────────────
        self._btn_outer = tk.Frame(s, bg=C["gldm"], padx=2, pady=2)
        self._btn_outer.pack(fill=tk.X, padx=10)
        self._toggle_btn = tk.Button(
            self._btn_outer, text=_t("▶  开始记录", "▶  Start Recording"),
            font=("Sans", 13, "bold"), fg=C["glow"], bg=C["gldm"],
            activeforeground=C["wh"], activebackground="#006633",
            relief=tk.FLAT, bd=0, pady=9, cursor="hand2",
            command=self._toggle)
        self._toggle_btn.pack(fill=tk.X)

        # ── 状态行（LED + 状态 + 张数）───────────────────────
        sf = tk.Frame(s, bg=C["sbg"])
        sf.pack(fill=tk.X, padx=14, pady=(6, 0))
        self._led = tk.Canvas(sf, width=10, height=10,
                               bg=C["sbg"], highlightthickness=0)
        self._led.pack(side=tk.LEFT, padx=(0,6))
        self._led_dot = self._led.create_oval(0,0,9,9, fill=C["mid"], outline="")
        self._status_lbl = tk.Label(sf, text=State.IDLE.value,
                                     font=("Sans", 11, "bold"),
                                     fg=C["mid"], bg=C["sbg"])
        self._status_lbl.pack(side=tk.LEFT)
        self._count_lbl = tk.Label(sf, text=_t("000 张", "000 shots"),
                                    font=("Monospace", 10, "bold"),
                                    fg=C["cyan"], bg=C["sbg"])
        self._count_lbl.pack(side=tk.RIGHT)

        # ── 运动量分段条 ──────────────────────────────────────
        seg_row = tk.Frame(s, bg=C["sbg"])
        seg_row.pack(fill=tk.X, padx=14, pady=(4,0))
        self._segs = []
        for _ in range(14):
            seg = tk.Frame(seg_row, width=14, height=8, bg=C["dim"])
            seg.pack(side=tk.LEFT, padx=1)
            seg.pack_propagate(False)
            self._segs.append(seg)

        # ── 稳定进度 ──────────────────────────────────────────
        prog_bg = tk.Frame(s, bg=C["dim"], height=4)
        prog_bg.pack(fill=tk.X, padx=14, pady=(3,0))
        prog_bg.pack_propagate(False)
        self._prog_fill = tk.Frame(prog_bg, bg=C["glow"], height=4)
        self._prog_fill.place(x=0, y=0, relheight=1, width=0)
        self._prog_bg = prog_bg

        self._sep(s, pady=5)

        # ── 处理选项（精简文案+更紧的行距，省纵向空间）─────────────
        self.scan_var        = tk.BooleanVar(value=False)
        self.auto_crop_var   = tk.BooleanVar(value=False)
        self.auto_rotate_var = tk.BooleanVar(value=True)
        # 扫描件增强：白底黑字、保留红章；可单独用，也可配合裁剪（推荐先裁再增强）
        # 英文文案刻意从简：侧栏固定 340px，过长会被按钮截断
        self._make_opt_btn(s, _t("扫描增强（白底黑字·留红章）",
                                 "Scan Enhance (keep seals)"), self.scan_var)
        # 自动裁剪：裁出纸张，开启后才显示“转竖向”子项
        self._opt_btn_crop = self._make_opt_btn(
            s, _t("自动裁剪（裁出纸张）", "Auto Crop (detect paper)"), self.auto_crop_var,
            on_change=self._update_rotate_state)
        self._opt_btn_rotate = self._make_opt_btn(
            s, _t("转竖向", "Rotate to portrait"), self.auto_rotate_var, sub=True)
        self._update_rotate_state()

        # ── 设置：点开才弹独立窗口，不占侧栏地方 ──────────────────
        # 摄像头/更新/检测参数调节都是"设一次就不用再管"的东西，天天要点的
        # 是上面那几个开关。以前做成侧栏里可折叠的一段，收起来也还留一行、
        # 展开更是把主界面顶下去；现在改成点开才弹出的独立窗口，侧栏任何
        # 时候都只有这一行按钮，不随它增减。
        tk.Button(s, text=_t("设置…", "Settings…"),
                  font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["wh"], activebackground=C["cyan"],
                  relief=tk.FLAT, bd=0, highlightthickness=0, anchor="w",
                  padx=10, pady=4, cursor="hand2",
                  command=self._open_more_settings
                  ).pack(fill=tk.X, padx=10, pady=(6, 0))
        self._sep(s, pady=5)


        # ── 照片操作栏 ────────────────────────────────────────
        af = tk.Frame(s, bg=C["sbg"])
        af.pack(fill=tk.X, padx=10, pady=(0, 4))
        self._sel_del_btn = tk.Button(af, text=_t("删除已选", "Delete selected"),
                  font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["wh"], activebackground=C["red"],
                  relief=tk.FLAT, bd=0, highlightthickness=0,
                  padx=10, pady=3, cursor="hand2", state=tk.DISABLED,
                  command=self._delete_selected)
        self._sel_del_btn.pack(side=tk.LEFT)
        tk.Button(af, text=_t("一键删除全部", "Delete all"),
                  font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["wh"], activebackground=C["red"],
                  relief=tk.FLAT, bd=0, highlightthickness=0,
                  padx=10, pady=3, cursor="hand2",
                  command=self._delete_all).pack(side=tk.RIGHT)

        # ── 滚动区 ────────────────────────────────────────────
        wrap = tk.Frame(s, bg=C["sbg"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=6)
        self._tc = tk.Canvas(wrap, bg=C["sbg"], highlightthickness=0)
        sb_w = tk.Scrollbar(wrap, orient="vertical", command=self._tc.yview,
                             bg=C["sbg"], troughcolor=C["sbg"])
        self._tc.configure(yscrollcommand=sb_w.set)
        sb_w.pack(side=tk.RIGHT, fill=tk.Y)
        self._tc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._sf = tk.Frame(self._tc, bg=C["sbg"])
        self._sw = self._tc.create_window((0,0), window=self._sf, anchor="nw")

        def _cfg(e):
            self._tc.configure(scrollregion=self._tc.bbox("all"))
        self._sf.bind("<Configure>", _cfg)

        def _cfg_canvas(e):
            # 画布自身首次真正获得布局尺寸时同步内嵌框架宽度；
            # 若只靠 _sf 的 <Configure> 回写，窗口刚打开、尚未完成布局时
            # self._tc.winfo_width() 会读到 1，之后没有照片导致内容高度
            # 不变化就永远不会再触发同步，整块历史区域会视觉消失。
            self._tc.itemconfig(self._sw, width=e.width)
        self._tc.bind("<Configure>", _cfg_canvas)

        # 历史照片区
        self._recent_row, self._recent_hdr, self._recent_body = \
            self._make_hist_section(self._sf, "recent")

        tk.Frame(self._sf, height=1, bg=C["line"]).pack(fill=tk.X, pady=4)

        # 本次拍摄
        tk.Label(self._sf, text=_t("本次拍摄", "This session"), font=("Sans", 8),
                 fg=C["mid"], bg=C["sbg"], anchor="w", padx=6
                 ).pack(fill=tk.X, pady=(0,4))
        self._cur_frame = tk.Frame(self._sf, bg=C["sbg"])
        self._cur_frame.pack(fill=tk.X)


    # ── 辅助构建 ─────────────────────────────────────────────

    def _sep(self, p, pady=8):
        tk.Frame(p, height=1, bg=C["line"]).pack(fill=tk.X, padx=6, pady=pady)

    def _open_more_settings(self):
        """设置弹窗：单例，重复点击只把已开的窗口提到前面。"""
        if self._more_dialog is not None and self._more_dialog.winfo_exists():
            self._more_dialog.lift()
            self._more_dialog.focus_force()
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(_t("设置 · FlipScan", "Settings · FlipScan"))
        dlg.configure(bg=C["sbg"])
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.protocol("WM_DELETE_WINDOW", lambda: self._close_more_settings(dlg))
        self._more_dialog = dlg
        self._build_more_settings_dialog(dlg)
        tk.Button(dlg, text=_t("关闭", "Close"),
                  font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
                  activeforeground=C["wh"], activebackground=C["cyan"],
                  relief=tk.FLAT, bd=0, highlightthickness=0,
                  padx=10, pady=4, cursor="hand2",
                  command=lambda: self._close_more_settings(dlg)
                  ).pack(anchor="e", padx=14, pady=(4, 10))
        # 内容画完再相对主窗口居中（否则 geometry 用的是未布局尺寸，会跑到左上角）
        self.root.update_idletasks()
        dlg.update_idletasks()
        self._center_on_main(dlg)

    def _center_on_main(self, dlg):
        """把弹窗放到主窗口正中；主窗口还没映射时退回屏幕中心。"""
        self.root.update_idletasks()
        dlg.update_idletasks()
        dw = dlg.winfo_reqwidth() or dlg.winfo_width() or 370
        dh = dlg.winfo_reqheight() or dlg.winfo_height() or 420
        try:
            mx = self.root.winfo_rootx()
            my = self.root.winfo_rooty()
            mw = self.root.winfo_width()
            mh = self.root.winfo_height()
            if mw <= 1 or mh <= 1:
                raise tk.TclError("main not mapped")
            x = mx + max((mw - dw) // 2, 0)
            y = my + max((mh - dh) // 2, 0)
        except tk.TclError:
            sw = dlg.winfo_screenwidth()
            sh = dlg.winfo_screenheight()
            x = max((sw - dw) // 2, 0)
            y = max((sh - dh) // 2, 0)
        dlg.geometry(f"+{x}+{y}")

    def _close_more_settings(self, dlg):
        dlg.destroy()
        self._more_dialog = None

    def _build_more_settings_dialog(self, parent):
        """Build Flipscan's capture, page-detection, and other settings."""
        self._settings_tab_bar = tk.Frame(parent, bg=C["sbg"])
        self._settings_tab_bar.pack(fill=tk.X, padx=10, pady=(10, 0))

        self._settings_content = tk.Frame(
            parent, width=350, height=330, bg=C["sbg"])
        self._settings_content.pack(fill=tk.BOTH, padx=4, pady=(8, 0))
        # Keep the dialog stable when moving between short and long sections.
        self._settings_content.pack_propagate(False)

        builders = {
            "capture_camera": self._build_capture_camera_settings,
            "page_detection": self._build_advanced_page_detection_settings,
            "other": self._build_other_settings,
        }
        tab_buttons = {}

        def _show_tab(section):
            for child in self._settings_content.winfo_children():
                child.destroy()
            builders[section](self._settings_content)
            for name, button in tab_buttons.items():
                selected = name == section
                button.config(
                    fg=C["cyan"] if selected else C["mid"],
                    bg=C["clow"] if selected else C["cbg"])

        for section, label_zh, label_en in (
                ("capture_camera", "拍摄与摄像头", "Capture & camera"),
                ("page_detection", "高级翻页检测", "Advanced page detection"),
                ("other", "其他", "Other")):
            button = tk.Button(
                self._settings_tab_bar, text=_t(label_zh, label_en),
                font=("Sans", 9, "bold"), fg=C["mid"], bg=C["cbg"],
                activeforeground=C["wh"], activebackground=C["clow"],
                relief=tk.FLAT, bd=0, highlightthickness=0,
                padx=12, pady=7, cursor="hand2",
                command=lambda name=section: _show_tab(name))
            button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=1)
            tab_buttons[section] = button

        _show_tab("capture_camera")

    def _build_capture_camera_settings(self, parent):
        """Settings used for normal capture and camera selection."""
        # 默认保存文件夹（与侧栏「改目录」同一条配置，下次启动仍生效）
        dir_box = tk.Frame(parent, bg=C["sbg"])
        dir_box.pack(fill=tk.X, padx=14, pady=(2, 8))
        tk.Label(
            dir_box,
            text=_t("默认保存文件夹", "Default save folder"),
            font=("Sans", 9, "bold"), fg=C["lit"], bg=C["sbg"], anchor="w"
            ).pack(fill=tk.X)
        row = tk.Frame(dir_box, bg=C["sbg"])
        row.pack(fill=tk.X, pady=(4, 0))
        self._settings_dir_lbl = tk.Label(
            row, text=self._short_path(self.output_dir),
            font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
            anchor="w", padx=8, pady=4)
        self._settings_dir_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            row, text=_t("更改…", "Change…"),
            font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
            activeforeground=C["wh"], activebackground=C["cyan"],
            relief=tk.FLAT, bd=0, highlightthickness=0,
            padx=10, pady=3, cursor="hand2",
            command=self._change_folder
            ).pack(side=tk.RIGHT, padx=(6, 0))

        # 点「开始记录」时先自动拍一张当前画面：适合"按下去之前书已经翻到
        # 要拍的那一页"的用法。默认关，避免普通工作流多拍一张。
        self.capture_on_start_var = tk.BooleanVar(
            value=bool(self.cfg.get("capture_on_start", False)))
        self._make_opt_btn(
            parent,
            _t("开始记录时先拍一张当前画面",
               "Capture current frame when starting"),
            self.capture_on_start_var, on_change=self._save_capture_on_start)
        tk.Label(
            parent,
            text=_t("适合书已经翻到要拍的那页、按下就想立刻拍下来的场景",
                    "Useful when the page is already in position and you want an immediate shot"),
            font=("Sans", 8), fg=C["dim"], bg=C["sbg"],
            wraplength=310, justify=tk.LEFT, anchor="w"
            ).pack(fill=tk.X, padx=14, pady=(0, 8))

        # 摄像头（与主界面的「保存位置 + 更改」同款：当前值 + 更改按钮）。
        cam_row = tk.Frame(parent, bg=C["sbg"])
        cam_row.pack(fill=tk.X, padx=10, pady=(6, 0))
        cb = tk.Frame(cam_row, bg=C["line"], padx=1, pady=1)
        cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._cam_lbl = tk.Label(
            cb, text="", font=("Sans", 9), fg=C["lit"], bg=C["cbg"],
            anchor="w", padx=6, pady=4, cursor="hand2")
        self._cam_lbl.pack(fill=tk.BOTH, expand=True)
        self._cam_lbl.bind("<Button-1>", lambda _: self._change_camera())
        tk.Button(
            cam_row, text=_t("更改", "Change"), font=("Sans", 9, "bold"),
            fg=C["cyan"], bg=C["clow"],
            activeforeground=C["wh"], activebackground=C["cyan"],
            relief=tk.FLAT, bd=0, padx=10, pady=4, cursor="hand2",
            command=self._change_camera
            ).pack(side=tk.RIGHT, padx=(4, 0))

        # 只有配置里固定的正是当前这台，勾选框才亮。
        pinned = (self.cfg.get("remember_camera")
                  and self.cfg.get("camera") == self._current_cam_path())
        self.remember_var = tk.BooleanVar(value=bool(pinned))
        self._remember_btn = self._make_opt_btn(
            parent, _t("固定这个摄像头", "Remember this camera"),
            self.remember_var, sub=True,
            on_change=self._on_remember_toggled)
        self._refresh_cam_label()
        self._cam_lbl.bind("<Configure>", self._on_cam_lbl_configure)

    def _build_advanced_page_detection_settings(self, parent):
        """Detection tuning controls intended for non-document cameras."""
        tk.Label(
            parent,
            text=_t("本工具针对高拍仪调校，普通摄像头可能需要调下面的参数",
                    "Tuned for document cameras; a regular webcam may need the settings below"),
            font=("Sans", 8), fg=C["dim"], bg=C["sbg"],
            wraplength=310, justify=tk.LEFT, anchor="w"
            ).pack(fill=tk.X, padx=14, pady=(0, 2))

        # 三个可调项。调完立即应用并保存，不需要重启。
        self._tune_vars = {}
        for key, label_zh, label_en, lo, hi, cur, hint_zh, hint_en in (
            ("motion_threshold", "翻页灵敏度", "Flip sensitivity",
             50, 1200, MOTION_THRESHOLD,
             "数值越小越灵敏（画面暗、对比低的摄像头调小）",
             "Lower = more sensitive (lower it for dim or low-contrast cameras)"),
            ("settle_frames", "稳定判定（秒）", "Settling time (s)",
             0.2, 3.0, STABLE_SECONDS,
             "画面静止多久才算翻完页（手抖、对焦慢的调大）",
             "How long the image must hold still (raise it for shaky or slow-focus cameras)"),
            ("capture_delay", "快门延迟（秒）", "Shutter delay (s)",
             0.0, 2.0, CAPTURE_DELAY,
             "判定稳定后再等一会儿才拍（等自动对焦拉清楚）",
             "Extra wait before the shot (lets autofocus settle)"),
        ):
            row = tk.Frame(parent, bg=C["sbg"])
            row.pack(fill=tk.X, padx=14, pady=(4, 0))
            tk.Label(
                row, text=_t(label_zh, label_en), font=("Sans", 9),
                fg=C["lit"], bg=C["sbg"], anchor="w"
                ).pack(side=tk.LEFT)
            saved = self.cfg.get(key)
            var = tk.DoubleVar(value=float(saved) if saved else float(cur))
            self._tune_vars[key] = var
            val_lbl = tk.Label(
                row, text="", font=("Monospace", 9),
                fg=C["cyan"], bg=C["sbg"])
            val_lbl.pack(side=tk.RIGHT)
            scale = tk.Scale(
                parent, from_=lo, to=hi, orient=tk.HORIZONTAL,
                variable=var, showvalue=False,
                resolution=(1 if key == "motion_threshold" else 0.1),
                bg=C["sbg"], fg=C["lit"], troughcolor=C["cbg"],
                highlightthickness=0, bd=0, sliderrelief=tk.FLAT,
                activebackground=C["cyan"], length=280)
            scale.pack(fill=tk.X, padx=14)
            tk.Label(
                parent, text=_t(hint_zh, hint_en), font=("Sans", 8),
                fg=C["dim"], bg=C["sbg"], wraplength=310,
                justify=tk.LEFT, anchor="w"
                ).pack(fill=tk.X, padx=14, pady=(0, 4))

            def _on_change(_value, k=key, v=var, label=val_lbl):
                label.config(text=(f"{v.get():.0f}" if k == "motion_threshold"
                                   else f"{v.get():.1f}"))
                self.cfg[k] = v.get()
                apply_tuning(self.cfg)
                save_config(self.cfg)
            scale.config(command=_on_change)
            _on_change(None)

        # 一键回到高拍仪的默认参数——调乱了不用自己回想原值是多少。
        def _reset_tuning():
            for key in ("motion_threshold", "settle_frames", "capture_delay"):
                self.cfg[key] = None
            save_config(self.cfg)
            for key, default in (("motion_threshold", MOTION_THRESHOLD),
                                 ("settle_frames", STABLE_SECONDS),
                                 ("capture_delay", CAPTURE_DELAY)):
                if key in self._tune_vars:
                    self._tune_vars[key].set(float(default))
            # No OK dialog for settings changes — brief title flash only.
            old = self.settings_win.title() if hasattr(self, "settings_win") else ""
            try:
                self.settings_win.title(_t("已恢复默认参数", "Defaults restored"))
                self.settings_win.after(
                    1600, lambda: self.settings_win.title(old or _t("设置", "Settings"))
                )
            except Exception:
                pass
        tk.Button(
            parent, text=_t("恢复默认参数", "Reset to defaults"),
            font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
            activeforeground=C["wh"], activebackground=C["cyan"],
            relief=tk.FLAT, bd=0, highlightthickness=0,
            padx=10, pady=3, cursor="hand2", command=_reset_tuning
            ).pack(anchor="w", padx=14, pady=(2, 4))

    def _build_other_settings(self, parent):
        """Application update settings unrelated to capture behavior."""
        self.auto_update_var = tk.BooleanVar(
            value=bool(self.cfg.get("auto_check_updates", True)))
        self._make_opt_btn(
            parent, _t("自动更新", "Update automatically"),
            self.auto_update_var, on_change=self._save_auto_update)
        upd_row = tk.Frame(parent, bg=C["sbg"])
        upd_row.pack(fill=tk.X, padx=14, pady=(6, 0))
        tk.Label(
            upd_row, text=f"v{__version__}", font=("Monospace", 9),
            fg=C["dim"], bg=C["sbg"]
            ).pack(side=tk.LEFT)
        self._upd_btn = tk.Button(
            upd_row, text=_t("现在检查更新", "Check for updates"),
            font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
            activeforeground=C["wh"], activebackground=C["cyan"],
            relief=tk.FLAT, bd=0, highlightthickness=0,
            padx=10, pady=3, cursor="hand2", command=self._check_updates)
        self._upd_btn.pack(side=tk.RIGHT)




    def _make_opt_btn(self, parent, text, var, sub=False, on_change=None):
        """带 ✓/○ 的自定义开关按钮。"""
        indent = 22 if sub else 14
        btn = tk.Button(parent, font=("Sans", 9 if sub else 10),
                         fg=C["mid"], bg=C["sbg"],
                         activeforeground=C["lit"], activebackground=C["sbg"],
                         relief=tk.FLAT, bd=0, highlightthickness=0, anchor="w",
                         pady=2, cursor="hand2")
        btn.pack(fill=tk.X, padx=indent)

        def _update():
            btn.config(text=f"✓  {text}" if var.get() else f"○  {text}",
                       fg=C["glow"] if var.get() else C["mid"])

        def _toggle():
            var.set(not var.get())
            _update()
            if on_change:
                on_change()

        btn.config(command=_toggle)
        _update()
        # 挂出来给外部用：代码里直接 var.set() 时按钮不会自己重画，
        # 需要显式调一次同步（例如切换摄像头后回填「固定」状态）。
        btn.sync = _update
        return btn

    # ── 自动更新 ─────────────────────────────────────────────

    def _save_auto_update(self):
        """开关一动就落盘，不用等下次退出——用户点了就该记住。"""
        self.cfg["auto_check_updates"] = bool(self.auto_update_var.get())
        save_config(self.cfg)

    def _save_capture_on_start(self):
        self.cfg["capture_on_start"] = bool(self.capture_on_start_var.get())
        save_config(self.cfg)

    def _check_updates(self):
        """「现在检查更新」：联网查，有新版就问要不要装。

        用户主动点的，所以查不到也要明说——静默失败会让人以为按钮坏了
        （启动时的自动检查正相反，那个必须安静）。
        """
        from tkinter import messagebox
        self._upd_btn.config(state=tk.DISABLED,
                             text=_t("检查中…", "Checking…"))
        self._upd_btn.update_idletasks()      # 先把"检查中"画出来再阻塞
        try:
            release = enum_update.fetch_latest_release(GITHUB_REPO, timeout=10)
        finally:
            self._upd_btn.config(state=tk.NORMAL,
                                 text=_t("现在检查更新", "Check for updates"))
        title = "Flipscan"
        if release is None:
            messagebox.showinfo(title, _t(
                "查不到更新信息。可能是没联网，或者 GitHub 暂时限流了，稍后再试。",
                "Could not reach the update server. You may be offline, or "
                "GitHub may be rate-limiting; try again later."))
            return
        if not enum_update.is_newer(release["version"], __version__):
            messagebox.showinfo(title, _t(
                f"已经是最新版本（{__version__}）。",
                f"You are on the latest version ({__version__})."))
            return
        base = os.path.dirname(os.path.abspath(__file__))
        if not enum_update.writable(base):
            messagebox.showerror(title, _t(
                f"没有写入权限：{base}\n这份程序装在系统目录里，"
                "请用管理员权限重装，或手动更新。",
                f"No write permission for {base}\nThis copy lives in a system "
                "directory; reinstall with admin rights or update manually."))
            return
        ok, detail = enum_update.apply_update(base, release)
        if ok:
            messagebox.showinfo(title, _t(
                f"已更新到 {release['version']}。重启高拍仪后生效。\n\n{detail}",
                f"Updated to {release['version']}. Restart Flipscan to use it."
                f"\n\n{detail}"))
        else:
            messagebox.showerror(title, detail)

    def _update_rotate_state(self):
        """裁剪关闭时收起旋转子选项，开启时展开（紧跟在裁剪按钮之后）。"""
        if self.auto_crop_var.get():
            self._opt_btn_rotate.pack(fill=tk.X, padx=22,
                                      after=self._opt_btn_crop)
        else:
            self._opt_btn_rotate.pack_forget()

    def _open_folder(self):
        """在文件管理器中打开当前保存文件夹。"""
        import subprocess
        path = self.output_dir
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
        subprocess.Popen(["xdg-open", path])

    def _make_hist_section(self, parent, key):
        """创建可折叠历史区块，返回 (row, hdr_btn, body)。"""
        row = tk.Frame(parent, bg=C["sbg"])
        row.pack(fill=tk.X, pady=(2,1))

        hdr = tk.Button(row, text=hist_header_text("▶", 0),
                         font=("Sans", 9), fg=C["mid"], bg=C["cbg"],
                         activeforeground=C["cyan"], activebackground=C["clow"],
                         relief=tk.FLAT, bd=0, pady=7, anchor="w", padx=8,
                         cursor="hand2")
        hdr.pack(side=tk.LEFT, fill=tk.X, expand=True)

        body = tk.Frame(parent, bg=C["sbg"])

        open_flag = {"v": False}
        hdr._open_flag = open_flag

        def toggle():
            if open_flag["v"]:
                body.pack_forget()
                open_flag["v"] = False
                hdr.config(text=hdr.cget("text").replace("▼", "▶"))
            else:
                open_flag["v"] = True
                hdr.config(text=hdr.cget("text").replace("▶", "▼"))
                # 无照片时不展开 body，避免占用空白空间
                if self._recent_paths:
                    body.pack(fill=tk.X, after=row)
                    self._tc.after(80, lambda: self._tc.yview_moveto(0.0))
        hdr.config(command=toggle)

        # 右侧是“选择本区照片”的选择框，不是删除按钮；真正删除统一走“删除已选”。
        sel = tk.Canvas(row, width=34, height=31, bg=C["cbg"], highlightthickness=0,
                        cursor="hand2")
        sel.pack(side=tk.RIGHT)
        sel._box_id = sel.create_rectangle(10, 8, 24, 22, outline=C["mid"], width=2)
        sel._check_id = sel.create_line(12, 15, 15, 19, 22, 10,
                                        fill=C["cyan"], width=2, state="hidden",
                                        capstyle=tk.ROUND, joinstyle=tk.ROUND)
        sel.bind("<Button-1>", lambda e: self._toggle_section_selection(key))
        if key == "recent":
            self._recent_select_canvas = sel

        return row, hdr, body

    # ── 删除确认对话框 ────────────────────────────────────────

    def _confirm_delete(self, paths, section_name, callback):
        if not paths:
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(_t("确认删除", "Confirm delete")); dlg.configure(bg=C["sbg"])
        dlg.geometry(_t("360x170", "470x190")); dlg.resizable(False, False)
        dlg.transient(self.root); dlg.grab_set()
        self.root.update_idletasks()
        x = self.root.winfo_x() + self.root.winfo_width()  // 2 - 180
        y = self.root.winfo_y() + self.root.winfo_height() // 2 - 85
        dlg.geometry(f"+{x}+{y}")
        tk.Label(dlg, text=_t(f"删除 {len(paths)} 张照片？", f"Delete {len(paths)} photos?"),
                 font=("Sans", 12, "bold"), fg=C["lit"], bg=C["sbg"]
                 ).pack(padx=20, pady=(20,4), anchor="w")
        tk.Label(dlg, text=_t(f"来自「{section_name}」\n文件将被永久删除，不可恢复。",
                              f"From \u201c{section_name}\u201d\nThe files are deleted permanently and cannot be recovered."),
                 font=("Sans", 9), fg=C["text"], bg=C["sbg"], justify="left"
                 ).pack(padx=20, anchor="w")
        bf = tk.Frame(dlg, bg=C["sbg"])
        bf.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=14)
        def do_delete():
            for p in paths:
                try: os.remove(p)
                except Exception: pass
            callback()
            dlg.destroy()
        tk.Button(bf, text=_t("取消", "Cancel"), command=dlg.destroy,
                  font=("Sans", 10), fg=C["mid"], bg=C["cbg"],
                  relief=tk.FLAT, bd=0, padx=14, pady=8, cursor="hand2"
                  ).pack(side=tk.RIGHT, padx=(6,0))
        tk.Button(bf, text=_t("确认删除", "Confirm delete"), command=do_delete,
                  font=("Sans", 10, "bold"), fg="white", bg=C["red"],
                  relief=tk.FLAT, bd=0, padx=14, pady=8, cursor="hand2"
                  ).pack(side=tk.RIGHT)

    def _reload_section(self, key):
        """删除后重新加载对应区块。"""
        self._load_id += 1
        self._load_history(self.output_dir)

    # ── 文件夹 ────────────────────────────────────────────────

    def _short_path(self, path):
        parts = path.rstrip("/").split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else path

    def _change_folder(self):
        FolderPicker(self.root, self.output_dir, self._on_folder_chosen)

    # ── 摄像头切换 ────────────────────────────────────────────

    def _current_cam_path(self):
        """当前摄像头的设备路径（写配置和查显示名都用它）。"""
        return camera_device_path(self.cam_idx)

    def _refresh_cam_label(self, _event=None):
        """刷新当前摄像头名，按标签真实像素宽度截断，绝不撑破固定宽侧栏。"""
        text = device_display_name(self._current_cam_path())
        avail = self._cam_lbl.winfo_width() - 14        # 减掉 padx=6 两边
        if avail < 40:
            # 首次构建时还没完成布局（winfo_width()==1），用估算值兜底
            avail = self.SW - 20 - 78 - 18
        try:
            f = tkfont.Font(font=self._cam_lbl.cget("font"))
            if f.measure(text) > avail:
                while text and f.measure(text + "…") > avail:
                    text = text[:-1]
                text += "…"
        except Exception:
            pass                                        # 量不出来就原样显示
        self._cam_lbl.config(text=text)

    def _on_cam_lbl_configure(self, event):
        """标签宽度变化时重新截断。只在宽度真的变了才做，避免自触发循环。"""
        if getattr(self, "_cam_lbl_w", None) == event.width:
            return
        self._cam_lbl_w = event.width
        self._refresh_cam_label()

    def _on_remember_toggled(self):
        """勾/取消「固定这个摄像头」→ 立刻写配置，不用等退出。"""
        remember_camera_setting(self._current_cam_path(), self.remember_var.get())

    def _change_camera(self):
        if self._cam_picker is not None and self._cam_picker.winfo_exists():
            self._cam_picker.lift()
            return
        self._cam_picker = CameraPicker(
            self.root, self._current_cam_path(),
            bool(self.remember_var.get()), self._on_camera_chosen)

    def _on_camera_chosen(self, idx, remember):
        self._apply_camera(f"/dev/video{idx}", remember)

    def _apply_camera(self, device_path, remember):
        """热切换摄像头：换句柄 → 重置检测基准 → 刷新界面 → 记住选择。"""
        ok = self.cam.switch(device_path)
        if ok:
            self.cam_idx = self.cam._idx
            # 换了摄像头画面整幅都变，不清掉基准帧会被当成一次剧烈翻页而误拍
            self.prev = self.cam.read()
            self._motion_buf.clear()
            self.state       = State.WAITING if self.recording else State.IDLE
            self.stable_t    = None
            self.flip_start  = None
            self.peak_motion = 0
        else:
            self._cam_banner.show_error(
                _t("设备可能已被其他程序占用或已拔出，已保持原来的摄像头。",
                   "The device may be busy or unplugged; kept the current camera."))
        self.remember_var.set(bool(remember))
        self._remember_btn.sync()
        self._refresh_cam_label()
        # 只在切换成功时写配置：把打不开的设备固定下来等于给下次挖坑
        if ok:
            remember_camera_setting(self._current_cam_path(), remember)

    def _on_folder_chosen(self, chosen):
        self.output_dir = chosen
        self.cfg["output_dir"] = chosen
        save_config(self.cfg)
        self._dir_lbl.config(text=self._short_path(chosen))
        if getattr(self, "_settings_dir_lbl", None) is not None:
            try:
                if self._settings_dir_lbl.winfo_exists():
                    self._settings_dir_lbl.config(text=self._short_path(chosen))
            except tk.TclError:
                pass
        self._clear_section(self._recent_body, "recent")
        self._load_id += 1
        self._load_history(chosen)

    # ── 历史记录加载（分近期/更早）───────────────────────────

    def _clear_section(self, body, key):
        for w in body.winfo_children():
            w.destroy()
        if key == "recent":
            self._recent_paths = []
        else:
            self._older_paths = []
        body_path = str(body)
        self._photo_cards = [(c,p,kw) for c,p,kw in self._photo_cards
                              if c.winfo_exists() and c.winfo_parent() != body_path]
        self._rebuild_select_controls()
        self._selected_paths.clear()
        self._update_sel_bar()

    def _load_history(self, folder):
        prefix = "▼" if self._recent_hdr._open_flag["v"] else "▶"
        if not os.path.isdir(folder):
            self._history_snapshot = None
            self._recent_hdr.config(text=hist_header_text(prefix, 0))
            return

        self._history_snapshot = self._folder_snapshot(folder)

        current_session = set(self.saved)
        all_paths = sorted([
            os.path.join(folder, f) for f in os.listdir(folder)
            if os.path.splitext(f)[1] in IMG_EXTS
            and os.path.join(folder, f) not in current_session
        ])

        self._recent_paths = all_paths
        self._older_paths  = []

        n = len(all_paths)
        self._recent_hdr.config(text=hist_header_text(prefix, n))

        # 立即清空旧缩略图，让删除效果即时生效
        for w in self._recent_body.winfo_children():
            w.destroy()
        self._hist_refs.clear()
        self._rebuild_select_controls()
        self._selected_paths.clear()
        self._update_sel_bar()

        if not all_paths:
            # 清空后无照片，即使处于展开状态也不占位置
            if self._recent_hdr._open_flag["v"]:
                self._recent_body.pack_forget()
            return

        my_id = self._load_id
        threading.Thread(target=self._load_hist_bg,
                         args=(all_paths, [], my_id), daemon=True).start()

    def _initial_load_history(self):
        """窗口初次布局完成后加载历史；已有照片时直接展开显示。"""
        self._load_id += 1
        self._load_history(self.output_dir)

    def _folder_snapshot(self, folder):
        if not os.path.isdir(folder):
            return None
        snap = []
        try:
            for f in os.listdir(folder):
                if os.path.splitext(f)[1] not in IMG_EXTS:
                    continue
                p = os.path.join(folder, f)
                try:
                    st = os.stat(p)
                except FileNotFoundError:
                    continue
                snap.append((p, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            return None
        return tuple(sorted(snap))

    def _close_preview_if_active_deleted(self, deleted):
        if self._preview_active_path not in deleted:
            return
        self._clear_preview_mask()
        if self._overlay is not None:
            try:
                if self._overlay.winfo_exists():
                    self._overlay.destroy()
            except Exception:
                pass
        self._overlay = None
        self._preview_active_path = None
        self._preview_close_cb = None
        self._preview_save_cb = None

    def _sync_deleted_files(self):
        """同步外部删除，清除残留卡片，避免点击不存在文件后无反应或卡顿。"""
        stale = {p for _, p, _ in self._photo_cards if not os.path.exists(p)}
        stale.update(p for p in self._recent_paths if not os.path.exists(p))
        stale.update(p for p in self.saved if not os.path.exists(p))
        if not stale:
            return False
        self._close_preview_if_active_deleted(stale)
        self._remove_cards_for_paths(stale)
        self.saved = [p for p in self.saved if p not in stale]
        self._update_sel_bar()
        return True

    def _poll_history_changes(self):
        if not self.running:
            return
        old = self._history_snapshot
        new = self._folder_snapshot(self.output_dir)
        deleted_synced = self._sync_deleted_files()
        if new != old:
            self._load_id += 1
            self._load_history(self.output_dir)
        elif deleted_synced:
            self._history_snapshot = new
        self._history_poll_job = self.root.after(2000, self._poll_history_changes)

    def _load_hist_bg(self, recent, older, load_id):
        def load_batch(paths):
            items = []
            for p in paths:
                if load_id != self._load_id:
                    return None
                try:
                    img = Image.open(p)
                    img.load()
                    # 注意：ImageTk.PhotoImage 必须在主线程创建（Tk 非线程安全），
                    # 这里只传递解码好的 PIL Image，真正转换放到 _render_hist 里做
                    items.append((p, img))
                except Exception:
                    pass
            return items

        r_items = load_batch(recent)
        o_items = load_batch(older)
        if load_id == self._load_id:
            self.root.after(0, self._render_hist, r_items or [], o_items or [])

    def _render_hist(self, r_items, o_items):
        for w in self._recent_body.winfo_children():
            w.destroy()
        cur_path = str(self._cur_frame)
        # 清理"本次拍摄"中已被删除文件的卡片
        kept = []
        for card, path, kw in self._photo_cards:
            if not card.winfo_exists():
                continue
            if card.winfo_parent() == cur_path:
                if os.path.exists(path):
                    kept.append((card, path, kw))
                else:
                    card.destroy()
        self._photo_cards = kept
        self._hist_refs.clear()
        self._rebuild_select_controls()
        self._selected_paths.clear()
        self._update_sel_bar()
        self._layout_grid(self._cur_frame)   # 删除后重排“本次拍摄”，避免留空洞

        for path, img in r_items:
            photo = ImageTk.PhotoImage(self._prepare_thumb_image(img))
            self._thumb_card(self._recent_body, path, photo, self._hist_refs)

        n = len(r_items)
        prefix = "▼" if self._recent_hdr._open_flag["v"] else "▶"
        self._recent_hdr.config(text=hist_header_text(prefix, n))
        # 始终默认折叠，不自动展开；仅在用户已手动展开时按是否有内容同步显示/隐藏 body
        if self._recent_hdr._open_flag["v"]:
            if n:
                self._recent_body.pack(fill=tk.X, after=self._recent_row)
            else:
                self._recent_body.pack_forget()
        if self._preview_active_path and self._preview_close_cb:
            self._apply_preview_mask(self._preview_active_path, self._preview_close_cb)
    # ── 搜索 ──────────────────────────────────────────────────

    def _update_sel_bar(self):
        n = len(self._selected_paths)
        if n:
            self._sel_del_btn.config(text=_t(f"删除已选 ({n}张)", f"Delete selected ({n})"),
                                      state=tk.NORMAL, fg=C["wh"], bg=C["red"])
        else:
            self._sel_del_btn.config(text=_t("删除已选", "Delete selected"),
                                      state=tk.DISABLED, fg=C["mid"], bg=C["cbg"])
        self._refresh_selection_marks()

    def _toggle_section_selection(self, key):
        """历史区标题右侧选择框：只选中/取消选中，不直接删除。"""
        paths = self._recent_paths if key == "recent" else self._older_paths
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        if any(p not in self._selected_paths for p in paths):
            self._selected_paths.update(paths)
        else:
            self._selected_paths.difference_update(paths)
        self._update_sel_bar()

    def _set_header_select_checked(self, checked, partial=False):
        sel = getattr(self, "_recent_select_canvas", None)
        if not sel or not sel.winfo_exists():
            return
        color = C["cyan"] if checked or partial else C["mid"]
        sel.itemconfig(sel._box_id, outline=color)
        sel.itemconfig(sel._check_id, state="normal" if checked else "hidden")

    def _rebuild_select_controls(self):
        self._select_controls = {}
        for card, path, kw in self._photo_cards:
            setter = getattr(card, "_set_checked", None)
            if card.winfo_exists() and setter is not None:
                self._select_controls.setdefault(path, []).append((card, setter))

    def _remove_cards_for_paths(self, paths):
        """删除文件后同步移除所有对应缩略图卡片，避免 UI 残留。"""
        paths = set(paths)
        kept = []
        for card, path, kw in self._photo_cards:
            if path in paths:
                if card.winfo_exists():
                    card.destroy()
            elif card.winfo_exists():
                kept.append((card, path, kw))
        self._photo_cards = kept
        self._recent_paths = [p for p in self._recent_paths if p not in paths]
        self._older_paths  = [p for p in self._older_paths if p not in paths]
        self._selected_paths.difference_update(paths)
        for path in paths:
            self._preview_view_state.pop(path, None)
        self._rebuild_select_controls()

    def _refresh_selection_marks(self):
        """根据 _selected_paths 同步所有缩略图选择框和历史标题选择框。"""
        stale = []
        for path, controls in list(self._select_controls.items()):
            kept = []
            for card, setter in controls:
                if card.winfo_exists():
                    checked = path in self._selected_paths
                    setter(checked)
                    card.config(highlightbackground=C["cyan"] if checked else C["line"])
                    kept.append((card, setter))
            if kept:
                self._select_controls[path] = kept
            else:
                stale.append(path)
        for path in stale:
            self._select_controls.pop(path, None)
        recent = [p for p in self._recent_paths if os.path.exists(p)]
        checked_count = sum(1 for p in recent if p in self._selected_paths)
        self._set_header_select_checked(
            bool(recent) and checked_count == len(recent),
            partial=0 < checked_count < len(recent))

    def _delete_selected(self):
        if not self._selected_paths:
            return
        n = len(self._selected_paths)
        dlg = tk.Toplevel(self.root)
        dlg.title(_t("确认删除", "Confirm delete"))
        dlg.configure(bg=C["sbg"])
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry(_t("320x120", "430x140"))
        pw = self.root.winfo_x() + self.root.winfo_width() // 2 - 160
        ph = self.root.winfo_y() + self.root.winfo_height() // 2 - 60
        dlg.geometry(f"+{pw}+{ph}")
        tk.Label(dlg, text=_t(f"永久删除 {n} 张照片？", f"Permanently delete {n} photos?"),
                 font=("Sans", 11), fg=C["lit"], bg=C["sbg"]
                 ).pack(pady=(22, 8))
        bf = tk.Frame(dlg, bg=C["sbg"])
        bf.pack()
        def do():
            deleted = set(self._selected_paths)
            for p in list(self._selected_paths):
                try: os.remove(p)
                except Exception: pass
            self._remove_cards_for_paths(deleted)
            if self._preview_active_path in deleted and self._overlay is not None:
                self._clear_preview_mask()
                if self._overlay.winfo_exists():
                    self._overlay.destroy()
                self._overlay = None
                self._preview_active_path = None
                self._preview_close_cb = None
                self._preview_save_cb = None
            self._selected_paths.clear()
            dlg.destroy()
            self._update_sel_bar()
            self._load_id += 1
            self._load_history(self.output_dir)
        tk.Button(bf, text=_t("删除", "Delete"), command=do,
                  font=("Sans", 10, "bold"), fg="white", bg=C["red"],
                  relief=tk.FLAT, bd=0, padx=14, pady=6, cursor="hand2"
                  ).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text=_t("取消", "Cancel"), command=dlg.destroy,
                  font=("Sans", 10), fg=C["mid"], bg=C["cbg"],
                  relief=tk.FLAT, bd=0, padx=14, pady=6, cursor="hand2"
                  ).pack(side=tk.LEFT)

    def _delete_all(self):
        imgs = [os.path.join(self.output_dir, f)
                for f in os.listdir(self.output_dir)
                if os.path.splitext(f)[1] in IMG_EXTS] if os.path.isdir(self.output_dir) else []
        if not imgs:
            return
        n = len(imgs)
        dlg = tk.Toplevel(self.root)
        dlg.title(_t("确认删除", "Confirm delete"))
        dlg.configure(bg=C["sbg"])
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry(_t("340x120", "450x140"))
        pw = self.root.winfo_x() + self.root.winfo_width() // 2 - 170
        ph = self.root.winfo_y() + self.root.winfo_height() // 2 - 60
        dlg.geometry(f"+{pw}+{ph}")
        tk.Label(dlg, text=_t(f"永久删除文件夹内全部 {n} 张照片？", f"Permanently delete all {n} photos in this folder?"),
                 font=("Sans", 11), fg=C["ambe"], bg=C["sbg"]
                 ).pack(pady=(22, 8))
        bf = tk.Frame(dlg, bg=C["sbg"])
        bf.pack()
        def do():
            for p in imgs:
                try: os.remove(p)
                except Exception: pass
            self._remove_cards_for_paths(imgs)
            if self._overlay is not None:
                self._clear_preview_mask()
                if self._overlay.winfo_exists():
                    self._overlay.destroy()
                self._overlay = None
                self._preview_active_path = None
                self._preview_close_cb = None
                self._preview_save_cb = None
            self._selected_paths.clear()
            dlg.destroy()
            self._update_sel_bar()
            self._load_id += 1
            self._load_history(self.output_dir)
        tk.Button(bf, text=_t("全部删除", "Delete all"), command=do,
                  font=("Sans", 10, "bold"), fg="white", bg=C["red"],
                  relief=tk.FLAT, bd=0, padx=14, pady=6, cursor="hand2"
                  ).pack(side=tk.LEFT, padx=6)
        tk.Button(bf, text=_t("取消", "Cancel"), command=dlg.destroy,
                  font=("Sans", 10), fg=C["mid"], bg=C["cbg"],
                  relief=tk.FLAT, bd=0, padx=14, pady=6, cursor="hand2"
                  ).pack(side=tk.LEFT)

    # ── 预览遮罩清理 ──────────────────────────────────────────

    def _clear_preview_mask(self):
        """清掉所有缩略图预览遮罩，防止切换/删除/重载后残留。"""
        for card, p, kw in self._photo_cards:
            mask = getattr(card, "_preview_mask", None)
            if mask is not None:
                try: mask.destroy()
                except Exception: pass
                card._preview_mask = None

    def _mask_card(self, card, on_click):
        """只在当前被预览的缩略图上盖「返回直播」遮罩。"""
        if getattr(card, "_preview_mask", None) is not None:
            return
        mask = tk.Frame(card, bg="#050c18", cursor="hand2",
                        highlightthickness=2, highlightbackground=C["cyan"])
        mask.place(relx=0, rely=0, relwidth=1, relheight=1)
        lbl = tk.Label(mask, text=_t("↩ 返回直播", "↩ Back to live"), font=("Sans", 12, "bold"),
                       fg=C["wh"], bg="#050c18", cursor="hand2")
        lbl.place(relx=0.5, rely=0.5, anchor="center")
        mask.bind("<Button-1>", lambda e: on_click())
        lbl.bind("<Button-1>",  lambda e: on_click())
        card._preview_mask = mask

    def _apply_preview_mask(self, path, on_click):
        """先清旧遮罩，再只给当前预览路径对应的缩略图加遮罩。"""
        self._clear_preview_mask()
        for card, p, kw in self._photo_cards:
            if p == path and card.winfo_exists():
                self._mask_card(card, on_click)

    # ── 缩略图 ────────────────────────────────────────────────

    def _thumb_size(self, cols=None):
        """按列数把可用宽度均分，得到缩略图的目标宽度（像素）。"""
        cols = cols or self.THUMB_COLS
        tw = max(self._tc.winfo_width() or 1, self.SW - 28)
        return max(70, (tw - 6) // cols - 6)

    def _make_thumb_photo(self, path, height=None, cols=None):
        """生成铺满格子的缩略图：保留原图比例、不裁内容，靠贴合格子形状
        本身来铺满，而不是留白凑合或者裁掉边缘。"""
        img = Image.open(path).convert("RGB")
        return ImageTk.PhotoImage(self._fit_thumb_canvas(img, self._thumb_size(cols)))

    def _prepare_thumb_image(self, img, height=None, cols=None):
        """后台已解码 PIL 图像时，在主线程生成铺满格子的缩略图。"""
        return self._fit_thumb_canvas(img.convert("RGB"), self._thumb_size(cols))

    def _fit_thumb_canvas(self, img, cell_w):
        """按图片自身的宽高比缩到目标宽度：格子的形状贴合照片本身的比例，
        既不裁掉内容（不用 ImageOps.fit 那种裁边策略），也不留白凑合
        （不用 ImageOps.contain 那种居中垫背景色的策略）——照片有多"高"，
        格子就有多高，天然铺满，原始比例和全部内容都保留。

        默认相机分辨率 3264×2448 是横向 4:3，没开"自动裁剪+转竖向"时存下来
        就是这个原始比例；旧代码把缩略图框写死成"近 A4 竖版" 1:1.34，
        横向照片塞进竖版框会在上下留出近一半的空白——这就是"缩略图有
        很多空位"的根因。

        极端比例（很宽的全景图、很窄的竖条）夹一下上下限，避免某一张图
        把整行网格的高度撑得离谱。"""
        ratio = (img.height / img.width) if img.width else 1.0
        ratio = max(0.4, min(ratio, 2.2))
        h = max(1, round(cell_w * ratio))
        return img.resize((cell_w, h), Image.LANCZOS)

    def _layout_grid(self, container):
        """把某容器下的缩略图卡片按列数重新网格布局，
        增删后自动无空洞、无需逐处 re-pack。
        “本次拍摄”固定单列（CUR_COLS），其余（历史照片）用 THUMB_COLS。"""
        cont  = str(container)
        cards = [c for c, p, kw in self._photo_cards
                 if c.winfo_exists() and c.winfo_parent() == cont]
        cols = self.CUR_COLS if container is self._cur_frame else self.THUMB_COLS
        for i, c in enumerate(cards):
            c.grid(row=i // cols, column=i % cols, sticky="nsew", padx=2, pady=2)
        for col in range(cols):
            container.grid_columnconfigure(col, weight=1, uniform="thumb")

    def _thumb_card(self, container, path, photo, refs):
        kw = dict()   # 兼容 _photo_cards 三元组；实际布局交给 _layout_grid
        card = tk.Frame(container, bg=C["cbg"], pady=0, padx=0,
                        highlightthickness=1, highlightbackground=C["line"])

        cb_ref = [None]

        def _select(event=None):
            set_checked = cb_ref[0]
            if path in self._selected_paths:
                self._selected_paths.discard(path)
                card.config(highlightbackground=C["line"])
                if set_checked:
                    set_checked(False)
            else:
                self._selected_paths.add(path)
                card.config(highlightbackground=C["cyan"])
                if set_checked:
                    set_checked(True)
            self._update_sel_bar()

        # 点击缩略图 → 全屏预览（不关闭摄像头）
        lbl = tk.Label(card, image=photo, bg=C["cbg"], cursor="hand2")
        lbl.pack(fill=tk.BOTH, expand=True)
        lbl._p = photo
        lbl.bind("<Button-1>", lambda e: self._open_photo_preview(path))
        refs.append(photo)

        # 用 Canvas 手绘选择框，而不是 ☐/☑ 这类容易在无对应字形的字体下
        # 显示成"缺字方框"（看起来像叉号/乱码）的 Unicode 符号。
        cb = tk.Canvas(card, width=24, height=24, bg=C["cbg"],
                        highlightthickness=1, highlightbackground=C["line"], cursor="hand2")
        box_id   = cb.create_rectangle(5, 5, 19, 19, outline=C["mid"], width=2)
        check_id = cb.create_line(7, 12, 10, 16, 17, 7,
                                   fill=C["cyan"], width=2, state="hidden",
                                   capstyle=tk.ROUND, joinstyle=tk.ROUND)

        def _set_checked(checked):
            cb.itemconfig(box_id, outline=C["cyan"] if checked else C["mid"])
            cb.itemconfig(check_id, state="normal" if checked else "hidden")

        cb.bind("<Button-1>", _select)
        cb.place(relx=1.0, rely=0.0, x=-7, y=7, anchor="ne")
        cb_ref[0] = _set_checked
        card._set_checked = _set_checked
        self._select_controls.setdefault(path, []).append((card, _set_checked))
        _set_checked(path in self._selected_paths)

        self._photo_cards.append((card, path, kw))
        self._layout_grid(container)
        return card

    def _add_thumb(self, path):
        try:
            photo = self._make_thumb_photo(path, height=112, cols=self.CUR_COLS)
        except Exception:
            return
        card = self._thumb_card(self._cur_frame, path, photo, self._refs)
        self._tc.after(80, lambda: self._tc.yview_moveto(1.0))

    def _open_photo_preview(self, path):
        """在摄像头画面上叠加全屏预览（保持原布局，不遮住右侧缩略图栏），
        摄像头继续运行，支持滚轮缩放/拖拽平移。"""
        if not os.path.exists(path):
            self._remove_cards_for_paths({path})
            self._update_sel_bar()
            self._load_id += 1
            self._load_history(self.output_dir)
            return
        if self._overlay is not None and self._overlay.winfo_exists():
            if self._preview_save_cb is not None:
                self._preview_save_cb()
            self._clear_preview_mask()
            self._overlay.destroy()
            self._overlay = None
            self._preview_active_path = None
            self._preview_close_cb = None
            self._preview_save_cb = None
        try:
            pil_img = Image.open(path).convert("RGB")
            np_img  = np.array(pil_img)   # 用 numpy/cv2 做缩放，比 PIL resize 快很多
            img_w, img_h = pil_img.width, pil_img.height
        except Exception:
            return

        overlay = tk.Frame(self._pf, bg=C["bg"])
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        self._overlay = overlay

        saved = self._preview_view_state.get(path, {})
        state = {"scale": saved.get("scale", 1.0),
                  "ox": saved.get("ox", 0.0), "oy": saved.get("oy", 0.0),
                  "drag": None,
                  "photo": None, "cache_size": None, "cache_img": None}
        redraw_job = {"id": None}

        # ── 顶栏 ──────────────────────────────────────────────────
        top_bar = tk.Frame(overlay, bg=C["sbg"], pady=6)
        top_bar.pack(side=tk.TOP, fill=tk.X)

        tk.Label(top_bar, text=friendly_photo_name(path),
                 font=("Monospace", 9), fg=C["text"], bg=C["sbg"]
                 ).pack(side=tk.LEFT, padx=14)

        def _save_view_state():
            self._preview_view_state[path] = {
                "scale": state["scale"], "ox": state["ox"], "oy": state["oy"]}

        def _close():
            _save_view_state()
            self._clear_preview_mask()
            self._preview_active_path = None
            self._preview_close_cb    = None
            self._preview_save_cb     = None
            if redraw_job["id"] is not None:
                canvas.after_cancel(redraw_job["id"])
                redraw_job["id"] = None
            overlay.destroy()
            self._overlay = None

        self._preview_active_path = path
        self._preview_close_cb    = _close
        self._preview_save_cb     = _save_view_state
        self._apply_preview_mask(path, _close)

        # 缩放按钮
        zoom_f = tk.Frame(top_bar, bg=C["sbg"])
        zoom_f.pack(side=tk.RIGHT, padx=(0, 4))

        canvas = tk.Canvas(overlay, bg=C["bg"], highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        def _redraw():
            canvas.delete("all")
            w, h = canvas.winfo_width(), canvas.winfo_height()
            if w < 10 or h < 10:
                return
            fit_scale = min(w / img_w, h / img_h)
            eff_scale = fit_scale * state["scale"]
            try:
                if state["scale"] <= 1.0:
                    # 未放大：整图安全缩小/等比显示，输出尺寸不超过画布，绝不会占用超大内存
                    nw = max(1, int(round(img_w * eff_scale)))
                    nh = max(1, int(round(img_h * eff_scale)))
                    cache_key = ("fit", nw, nh)
                    if state["cache_size"] == cache_key and state["cache_img"] is not None:
                        img2 = state["cache_img"]
                    else:
                        small = cv2.resize(np_img, (nw, nh), interpolation=cv2.INTER_LINEAR)
                        img2  = Image.fromarray(small)
                        state["cache_size"], state["cache_img"] = cache_key, img2
                    photo = ImageTk.PhotoImage(img2)
                    state["photo"] = photo
                    canvas.create_image(w // 2, h // 2, anchor="center", image=photo)
                else:
                    # 放大：只裁剪当前可视区域再缩放到画布大小——
                    # 绝不把整张图放大到超大尺寸，避免撑爆内存导致闪退
                    crop_w0 = w / eff_scale
                    crop_h0 = h / eff_scale
                    # crop_w0:crop_h0 恒等于 w:h；若独立地把两边分别夹到图片边界内，
                    # 比例就被打破，resize 到画布 (w,h) 时会出现挤压变形（缩小接近
                    # scale==1 边界时最明显，一过边界切回上面的整图分支又恢复正常）。
                    # 因此要用同一个缩放系数整体收缩，保持比例不变。
                    clamp = min(1.0, img_w / crop_w0, img_h / crop_h0)
                    crop_w = max(1, min(img_w, int(round(crop_w0 * clamp))))
                    crop_h = max(1, min(img_h, int(round(crop_h0 * clamp))))
                    cx = img_w / 2 + state["ox"]
                    cy = img_h / 2 + state["oy"]
                    x0 = max(0, min(int(round(cx - crop_w / 2)), img_w - crop_w))
                    y0 = max(0, min(int(round(cy - crop_h / 2)), img_h - crop_h))
                    state["ox"] = x0 + crop_w / 2 - img_w / 2
                    state["oy"] = y0 + crop_h / 2 - img_h / 2
                    cropped = np_img[y0:y0 + crop_h, x0:x0 + crop_w]
                    cache_key = ("crop", x0, y0, crop_w, crop_h, w, h)
                    if state["cache_size"] == cache_key and state["cache_img"] is not None:
                        img2 = state["cache_img"]
                    else:
                        small = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
                        img2  = Image.fromarray(small)
                        state["cache_size"], state["cache_img"] = cache_key, img2
                    photo = ImageTk.PhotoImage(img2)
                    state["photo"] = photo
                    canvas.create_image(w // 2, h // 2, anchor="center", image=photo)
            except Exception:
                return

        def _redraw_now():
            redraw_job["id"] = None
            _redraw()

        def _redraw_debounced():
            # 合并短时间内的高频事件（连续拖拽/滚轮），避免堵塞主线程
            if redraw_job["id"] is not None:
                canvas.after_cancel(redraw_job["id"])
            redraw_job["id"] = canvas.after(16, _redraw_now)

        def _zoom(factor):
            if factor == 0:
                state["scale"] = 1.0
                state["ox"] = state["oy"] = 0
            else:
                state["scale"] = max(1.0, min(state["scale"] * factor, 20.0))
            _redraw()

        for txt, fac in [("−", 1/1.3), (_t("适合", "Fit"), 0), ("+", 1.3)]:
            tk.Button(zoom_f, text=txt, font=("Monospace", 9),
                      fg=C["cyan"], bg=C["cbg"],
                      activeforeground=C["wh"], activebackground=C["clow"],
                      relief=tk.FLAT, bd=0, padx=9, pady=3, cursor="hand2",
                      command=lambda f=fac: _zoom(f)).pack(side=tk.LEFT, padx=1)

        def _scroll(event):
            f = 1.15 if (event.delta > 0 or event.num == 4) else 1 / 1.15
            state["scale"] = max(1.0, min(state["scale"] * f, 20.0))
            _redraw_debounced()

        def _drag_start(e):
            state["drag"] = (e.x, e.y, state["ox"], state["oy"])

        def _drag_move(e):
            if state["drag"] is None or state["scale"] <= 1.0:
                return
            sx, sy, ox, oy = state["drag"]
            dx, dy = e.x - sx, e.y - sy
            w, h = canvas.winfo_width(), canvas.winfo_height()
            if w > 10 and h > 10:
                eff_scale = min(w / img_w, h / img_h) * state["scale"]
                if eff_scale > 0:
                    # 屏幕像素位移换算成源图像素位移，和裁剪渲染的坐标系保持一致；
                    # 不设 10px 阈值、不等 after 延迟，拖动时立即重绘，手感才是实时的。
                    state["ox"] = ox - dx / eff_scale
                    state["oy"] = oy - dy / eff_scale
                    _redraw()

        def _release(e):
            state["drag"] = None

        canvas.bind("<MouseWheel>", _scroll)
        canvas.bind("<Button-4>",   _scroll)
        canvas.bind("<Button-5>",   _scroll)
        canvas.bind("<ButtonPress-1>",   _drag_start)
        canvas.bind("<B1-Motion>",       _drag_move)
        canvas.bind("<ButtonRelease-1>", _release)
        canvas.bind("<Escape>",          lambda e: _close())  # 键盘兜底，确保总能退出
        canvas.bind("<Configure>",       lambda e: _redraw_debounced())
        canvas.bind("<Enter>",           lambda e: canvas.focus_set())
        overlay.after(60, lambda: (canvas.focus_set(), _redraw()))

        # ── 底栏：删除按钮 ────────────────────────────────────────
        bot_bar = tk.Frame(overlay, bg=C["sbg"], pady=12)
        bot_bar.pack(side=tk.BOTTOM, fill=tk.X)

        def _delete():
            dlg = tk.Toplevel(self.root)
            dlg.title(_t("确认删除", "Confirm delete")); dlg.configure(bg=C["sbg"])
            dlg.transient(self.root); dlg.grab_set()
            dlg.geometry(_t("320x120", "430x140"))
            dlg.geometry(f"+{self.root.winfo_x()+self.root.winfo_width()//2-160}"
                         f"+{self.root.winfo_y()+self.root.winfo_height()//2-60}")
            tk.Label(dlg, text=_t("永久删除这张照片？", "Permanently delete this photo?"),
                     font=("Sans", 11), fg=C["lit"], bg=C["sbg"]
                     ).pack(pady=(22, 8))
            bf = tk.Frame(dlg, bg=C["sbg"]); bf.pack()

            def do():
                try: os.remove(path)
                except Exception: pass
                self._preview_view_state.pop(path, None)
                self._clear_preview_mask()
                self._preview_active_path = None
                self._preview_close_cb    = None
                self._preview_save_cb     = None
                if redraw_job["id"] is not None:
                    canvas.after_cancel(redraw_job["id"])
                    redraw_job["id"] = None
                dlg.destroy()
                overlay.destroy()
                self._overlay = None
                self._selected_paths.discard(path)
                self._update_sel_bar()
                self._load_id += 1
                self._load_history(self.output_dir)

            tk.Button(bf, text=_t("删除", "Delete"), command=do,
                      font=("Sans", 10, "bold"), fg="white", bg=C["red"],
                      relief=tk.FLAT, bd=0, padx=14, pady=6, cursor="hand2"
                      ).pack(side=tk.LEFT, padx=6)
            tk.Button(bf, text=_t("取消", "Cancel"), command=dlg.destroy,
                      font=("Sans", 10), fg=C["mid"], bg=C["cbg"],
                      relief=tk.FLAT, bd=0, padx=14, pady=6, cursor="hand2"
                      ).pack(side=tk.LEFT)

        tk.Button(bot_bar, text=_t("删除此照片", "Delete this photo"), font=("Sans", 11, "bold"),
                  fg="white", bg=C["red"],
                  activeforeground=C["wh"], activebackground="#cc1133",
                  relief=tk.FLAT, bd=0, padx=28, pady=10, cursor="hand2",
                  command=_delete).pack()

    # ── 开关 ──────────────────────────────────────────────────

    def _toggle(self):
        self.recording = not self.recording
        if self.recording:
            self.state = State.WAITING
            self.stable_t = self.flip_start = None
            self.peak_motion = 0
            self._motion_buf.clear()
            # 设置里开了「开始记录时先拍一张当前画面」：适合书已经翻到要拍
            # 的那页、按下就想立刻留一张的场景。跟正常翻页检测触发的拍照
            # 走同一个 _manual()，没有特殊路径，缩略图、计数都会正常更新。
            if self.cfg.get("capture_on_start", False):
                self._manual()
            self._toggle_btn.config(text=_t("■  停止记录", "■  Stop recording"),
                                     fg=C["red"], bg=C["rdim"],
                                     activeforeground=C["wh"],
                                     activebackground=C["red"])
            self._btn_outer.config(bg=C["rdim"])
            self._rec_lbl.config(text=_t("◉  录制中", "◉  Recording"), fg="#ff3366")
        else:
            self.state = State.IDLE
            self._toggle_btn.config(text=_t("▶  开始记录", "▶  Start recording"),
                                     fg=C["glow"], bg=C["gldm"],
                                     activeforeground=C["wh"],
                                     activebackground="#006633")
            self._btn_outer.config(bg=C["gldm"])
            self._rec_lbl.config(text=_t("◉  待机", "◉  Standby"), fg=C["dim"])

    def _manual(self):
        frame = self.cam.read()
        out   = process_captured(frame,
                                  self.auto_crop_var.get(),
                                  self.auto_rotate_var.get(),
                                  self.scan_var.get())
        path  = save_frame(out, self.output_dir)
        self.count += 1
        self.saved.append(path)
        self._add_thumb(path)
        self._hud_cap_btn.config(fg=C["wh"])
        self.root.after(300, lambda: self._hud_cap_btn.config(fg=C["cyan"]))

    def _quit(self):
        self.running = False
        if self._history_poll_job is not None:
            try: self.root.after_cancel(self._history_poll_job)
            except Exception: pass
            self._history_poll_job = None
        self.root.quit()

    # ── 摄像头共享（失焦→让出，获焦→夺回）──────────────────

    def _on_iconify(self, event):
        if event.widget is not self.root:
            return
        self._handle_background()

    def _on_restore(self, event):
        if event.widget is not self.root:
            return
        if self._cam_bg:
            self._handle_foreground()

    def _handle_background(self):
        self._cam_bg = True
        self._cam_monitoring = False
        self.cam.pause()
        self._cam_banner.hide()

    def _handle_foreground(self):
        self._cam_bg = False
        if self.cam.resume():
            self._cam_banner.hide()
        else:
            self._show_camera_conflict()

    def _show_camera_conflict(self):
        self._cam_banner.show_conflict(
            on_force=self._cam_force_take,
            on_wait=self._start_monitoring,
        )

    def _cam_force_take(self):
        """强制夺回摄像头（SIGTERM + SIGKILL 占用进程）"""
        force_take_camera(self.cam_idx)
        time.sleep(0.5)
        if self.cam.resume():
            self._cam_bg = False
            self._cam_monitoring = False
            self._cam_banner.show_forced(on_give_back=self._cam_give_back)
        else:
            self._start_monitoring()

    def _cam_give_back(self):
        """主动让出摄像头，开始监控等待恢复"""
        self.cam.pause()
        self._start_monitoring()

    def _start_monitoring(self):
        """启动后台线程：轮询其他应用是否已释放摄像头"""
        if self._cam_monitoring:
            return
        self._cam_monitoring = True
        self._cam_banner.show_monitoring()
        threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        """每 2 秒检查 /proc/*/fd/ 是否还有其他进程占用摄像头"""
        while self._cam_monitoring and self.running:
            time.sleep(2)
            others = [p for p in find_camera_users(self.cam_idx)
                       if p != os.getpid()]
            if not others:
                self._cam_monitoring = False
                self.root.after(0, self._on_camera_freed)
                return

    def _on_camera_freed(self):
        """其他应用已退出 → 自动夺回摄像头"""
        if self.cam.resume():
            self._cam_bg = False
            self._cam_banner.show_restored()
        else:
            self._start_monitoring()   # 还是拿不到，继续等

    # ── 实时直播缩放/平移 ─────────────────────────────────────

    def _live_scroll(self, event):
        f = 1.15 if (event.delta > 0 or event.num == 4) else 1 / 1.15
        self._live_zoom = max(1.0, min(self._live_zoom * f, 8.0))
        if self._live_zoom <= 1.0:
            self._live_ox = self._live_oy = 0.0
        self._render_live_preview(self.prev)

    def _live_drag_start(self, e):
        self._live_drag = (e.x, e.y, self._live_ox, self._live_oy)

    def _live_drag_move(self, e):
        if self._live_drag is None or self._live_zoom <= 1.0:
            return
        sx, sy, ox, oy = self._live_drag
        fh, fw = self.prev.shape[:2]
        pw2, ph2 = self._pf.winfo_width(), self._pf.winfo_height()
        if pw2 < 10 or ph2 < 10:
            return
        base_scale = max(pw2 / fw, ph2 / fh)
        eff_scale  = base_scale * self._live_zoom
        # 屏幕像素位移换算成源画面像素位移（拖拽方向与画面移动方向一致）
        self._live_ox = ox - (e.x - sx) / eff_scale
        self._live_oy = oy - (e.y - sy) / eff_scale
        self._render_live_preview(self.prev)

    def _live_drag_end(self, e):
        self._live_drag = None

    def _live_zoom_reset(self, e=None):
        self._live_zoom = 1.0
        self._live_ox = self._live_oy = 0.0
        self._render_live_preview(self.prev)

    def _render_live_preview(self, frame):
        """按当前直播缩放/平移状态立即重绘画面；拖拽时不等下一次 tick。"""
        if frame is None or (self._overlay and self._overlay.winfo_exists()):
            return
        pw2 = self._pf.winfo_width()
        ph2 = self._pf.winfo_height()
        if pw2 <= 10 or ph2 <= 10:
            return

        fh, fw     = frame.shape[:2]
        base_scale = max(pw2 / fw, ph2 / fh)
        eff_scale  = base_scale * self._live_zoom
        crop_w = min(fw, max(1, int(round(pw2 / eff_scale))))
        crop_h = min(fh, max(1, int(round(ph2 / eff_scale))))
        x0 = int(round(fw / 2 + self._live_ox - crop_w / 2))
        y0 = int(round(fh / 2 + self._live_oy - crop_h / 2))
        x0 = max(0, min(x0, fw - crop_w))
        y0 = max(0, min(y0, fh - crop_h))
        # 把裁剪后的偏移同步回状态，避免拖拽越界后下次从越界位置继续累加
        self._live_ox = x0 + crop_w / 2 - fw / 2
        self._live_oy = y0 + crop_h / 2 - fh / 2
        cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]
        small   = cv2.resize(cropped, (pw2, ph2), interpolation=cv2.INTER_LINEAR)
        rgb     = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        img     = Image.fromarray(rgb)
        photo   = ImageTk.PhotoImage(img)
        self.preview.config(image=photo)
        self.preview._r = photo

    # ── LED 闪烁 ──────────────────────────────────────────────

    def _blink_tick(self):
        if not self.running: return
        if not hasattr(self, "_blink"): self._blink = False
        self._blink = not self._blink
        if self.state == State.CAPTURING:
            col = "#ffffff" if self._blink else C["mid"]
        elif self.state == State.FLIPPING:
            col = C["ambe"] if self._blink else C["dim"]
        elif self.recording:
            col = STATE_COLOR[self.state] if self._blink else C["dim"]
        else:
            col = C["mid"]
        self._led.itemconfig(self._led_dot, fill=col)
        self.root.after(500, self._blink_tick)

    def _update_segs(self, motion):
        pct = min(motion / METER_MAX, 1.0)
        lit = int(pct * len(self._segs))
        n   = len(self._segs)
        for i, seg in enumerate(self._segs):
            seg.config(bg=(C["red"]  if i < lit and i >= n*0.8 else
                           C["ambe"] if i < lit and i >= n*0.5 else
                           C["glow"] if i < lit and self.recording else
                           C["mid"]  if i < lit else C["dim"]))

    # ── 主循环 ───────────────────────────────────────────────

    def _tick(self):
        if not self.running: return

        frame  = self.cam.read()
        raw_m  = motion_score(self.prev, frame)
        self._motion_buf.append(raw_m)
        motion = int(sum(self._motion_buf) / len(self._motion_buf))

        if self.recording:
            if self.state == State.WAITING:
                if motion > MOTION_THRESHOLD:
                    self.state = State.FLIPPING
                    self.flip_start  = time.time()
                    self.peak_motion = motion

            elif self.state == State.FLIPPING:
                self.peak_motion = max(self.peak_motion, motion)
                if motion <= MOTION_THRESHOLD:
                    dur = time.time() - self.flip_start
                    if self.peak_motion >= MIN_FLIP_MOTION and dur >= MIN_FLIP_SECONDS:
                        self.state    = State.STABILIZING
                        self.stable_t = time.time()
                    else:
                        self.state, self.peak_motion = State.WAITING, 0

            elif self.state == State.STABILIZING:
                if motion > MOTION_THRESHOLD:
                    self.state       = State.FLIPPING
                    self.flip_start  = time.time()
                    self.peak_motion = motion
                elif time.time() - self.stable_t >= STABLE_SECONDS + CAPTURE_DELAY:
                    self.state = State.CAPTURING

        progress = 0.0
        if self.state == State.STABILIZING and self.stable_t:
            wait = STABLE_SECONDS + CAPTURE_DELAY
            progress = min((time.time() - self.stable_t) / wait, 1.0) if wait > 0 else 1.0

        if self.state == State.CAPTURING:
            out  = process_captured(frame,
                                     self.auto_crop_var.get(),
                                     self.auto_rotate_var.get(),
                                     self.scan_var.get())
            path = save_frame(out, self.output_dir)
            self.count += 1
            self.saved.append(path)
            self._add_thumb(path)
            self.state, self.stable_t, self.peak_motion = State.WAITING, None, 0
            progress = 0.0

        self.prev = frame

        # UI 更新
        color = STATE_COLOR[self.state]
        self._status_lbl.config(text=self.state.value, fg=color)
        self._count_lbl.config(
            text=_t(f"{self.count:03d} 张", f"{self.count:03d} shots"))
        self._update_segs(motion)

        pw = self._prog_bg.winfo_width()
        if pw > 2:
            self._prog_fill.place(width=int(pw * progress))

        # 预览（填充模式，无黑边，支持滚轮缩放/拖拽平移）
        self._render_live_preview(frame)

        self.root.after(50, self._tick)


# ── 入口 ────────────────────────────────────────────────────────

def _set_window_icon(root):
    """用 PIL 绘制 64×64 图标并设置到窗口。"""
    img = Image.new("RGBA", (64, 64), (5, 12, 24, 255))
    d   = ImageDraw.Draw(img)
    c   = (0, 200, 255)
    for x1,y1,x2,y2 in [
        (3,3,15,3),(3,3,3,15),(61,3,49,3),(61,3,61,15),
        (3,61,15,61),(3,61,3,49),(61,61,49,61),(61,61,61,49)]:
        d.line([(x1,y1),(x2,y2)], fill=c, width=3)
    d.rectangle([12,14,52,50], outline=(20,50,80), width=1)
    d.line([(12,32),(52,32)], fill=(0,200,255,200), width=2)
    d.ellipse([9,29,15,35], fill=c)
    d.ellipse([49,29,55,35], fill=c)
    photo = ImageTk.PhotoImage(img)
    root.iconphoto(True, photo)
    root._icon_ref = photo   # 防止 GC

def _limit_resource_impact():
    """降低本进程的 CPU/IO 调度优先级，让桌面其它程序（鼠标、窗口管理器等）
    在系统繁忙时优先于本程序，避免摄像头常驻处理拖慢整台电脑。"""
    try:
        os.nice(10)  # 只降低优先级，CPU 空闲时仍能跑满，不影响拍照/检测功能
    except Exception:
        pass
    try:
        subprocess.run(["ionice", "-c", "2", "-n", "7", "-p", str(os.getpid())],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception:
        pass

def _make_prompt_root():
    root = tk.Tk(className="FlipScan")
    root.withdraw()
    return root

def _center_prompt(win):
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 3
    win.geometry(f"+{x}+{y}")

def _ask_kill_occupied(camera, users, seconds=3):
    """摄像头被占用时提示；倒计时结束默认结束占用进程。"""
    root = _make_prompt_root()
    result = {"value": "kill"}
    win = tk.Toplevel(root)
    win.title(_t("高拍仪被占用", "Document camera in use"))
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.configure(bg=C["sbg"])
    msg = (
        f"高拍仪摄像头 {camera_device_path(camera)} 正被其他应用占用。\n\n"
        f"{describe_pids(users)}\n\n"
        f"将于 {seconds} 秒后默认结束这些占用进程并重新打开高拍仪。"
    )
    tk.Label(win, text=msg, justify=tk.LEFT, bg=C["sbg"], fg=C["lit"],
             font=("Sans", 11), padx=18, pady=14, wraplength=680).pack(fill=tk.BOTH)
    countdown = tk.Label(win, bg=C["sbg"], fg=C["ambe"], font=("Sans", 11, "bold"))
    countdown.pack(fill=tk.X, padx=18, pady=(0, 10))
    buttons = tk.Frame(win, bg=C["sbg"])
    buttons.pack(fill=tk.X, padx=18, pady=(0, 16))

    def finish(value):
        result["value"] = value
        try: win.destroy()
        except Exception: pass
        try: root.quit()
        except Exception: pass

    tk.Button(buttons, text=_t("立即结束占用并打开高拍仪", "Free the camera and open it now"), command=lambda: finish("kill"),
              bg=C["red"], fg=C["wh"], relief=tk.FLAT, padx=12, pady=7).pack(side=tk.RIGHT)
    tk.Button(buttons, text=_t("取消启动", "Cancel"), command=lambda: finish("cancel"),
              bg=C["cbg"], fg=C["text"], relief=tk.FLAT, padx=12, pady=7).pack(side=tk.RIGHT, padx=(0, 8))

    remaining = {"value": seconds}
    def tick():
        n = remaining["value"]
        countdown.config(text=_t(f"倒计时：{n} 秒", f"{n} s left"))
        if n <= 0:
            finish("kill")
            return
        remaining["value"] = n - 1
        win.after(1000, tick)

    win.protocol("WM_DELETE_WINDOW", lambda: finish("cancel"))
    _center_prompt(win)
    tick()
    root.mainloop()
    try: root.destroy()
    except Exception: pass
    return result["value"]

def _ask_switch_internal(reason):
    """高拍仪非占用故障时，询问本次是否临时改用内置摄像头。"""
    root = _make_prompt_root()
    result = {"value": False}
    win = tk.Toplevel(root)
    win.title(_t("高拍仪无法打开", "Cannot open document camera"))
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.configure(bg=C["sbg"])
    msg = _t(
        "无法打开固定的高拍仪摄像头。\n\n"
        f"原因：{reason}\n\n"
        "是否仅本次临时切换到内置摄像头？\n"
        "不会保存这个选择；下次打开仍会优先使用高拍仪。",
        "Could not open the selected document camera.\n\n"
        f"Reason: {reason}\n\n"
        "Switch to the built-in camera just for this session?\n"
        "This choice is not saved; the document camera is still preferred next time."
    )
    tk.Label(win, text=msg, justify=tk.LEFT, bg=C["sbg"], fg=C["lit"],
             font=("Sans", 11), padx=18, pady=14, wraplength=660).pack(fill=tk.BOTH)
    buttons = tk.Frame(win, bg=C["sbg"])
    buttons.pack(fill=tk.X, padx=18, pady=(0, 16))

    def finish(value):
        result["value"] = value
        try: win.destroy()
        except Exception: pass
        try: root.quit()
        except Exception: pass

    tk.Button(buttons, text=_t("本次使用内置摄像头", "Use built-in camera this time"), command=lambda: finish(True),
              bg=C["cyan"], fg=C["bg"], relief=tk.FLAT, padx=12, pady=7).pack(side=tk.RIGHT)
    tk.Button(buttons, text=_t("取消启动", "Cancel"), command=lambda: finish(False),
              bg=C["cbg"], fg=C["text"], relief=tk.FLAT, padx=12, pady=7).pack(side=tk.RIGHT, padx=(0, 8))
    win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    _center_prompt(win)
    root.mainloop()
    try: root.destroy()
    except Exception: pass
    return result["value"]

def _open_camera_with_policy(camera):
    users = find_camera_users(camera)
    if users:
        if _ask_kill_occupied(camera, users) != "kill":
            return None
        force_take_camera(camera)
        time.sleep(0.5)
    try:
        return CameraThread(camera)
    except RuntimeError as e:
        users = find_camera_users(camera)
        if users:
            if _ask_kill_occupied(camera, users) != "kill":
                return None
            force_take_camera(camera)
            time.sleep(0.5)
            try:
                return CameraThread(camera)
            except RuntimeError as retry_error:
                e = retry_error
        reason = f"{e}；设备：{camera_device_path(camera)}"
        if str(camera) != "0" and _ask_switch_internal(reason):
            return CameraThread(0)
        return None

def maybe_check_updates(cfg):
    """启动时静默自动更新：有新版且目录可写则直接装，不通知、不确认。"""
    if not cfg.get("auto_check_updates", True):
        return

    def done(ok, release, detail):
        # 装完什么都不弹。下次启动生效——正拍着照把自己换掉是最糟的体验。
        print(f"[更新] {'已自动更新到 ' + str(release.get('version','')) if ok else '自动更新未完成: ' + str(detail)}")

    enum_update.auto_update_in_background(
        GITHUB_REPO, __version__, __file__, on_done=done, delay=15.0)


def main():
    _limit_resource_impact()
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--camera", default="auto")
    args, _ = ap.parse_known_args()

    # 摄像头来源优先级：命令行 --camera > 配置里固定的 > 交互挑选
    camera   = args.camera
    cfg      = load_config()
    remember = None            # None = 用户这次没表态，不动配置
    if camera in (None, "", "auto"):
        saved = cfg.get("camera") if cfg.get("remember_camera") else None
        if saved and os.path.exists(saved):
            camera = saved
        else:
            note = None
            if saved:
                # 固定的设备被拔了或重新编号了。这不是错误，别退出，
                # 让用户重选一个，并说清楚为什么又来问。
                note = _t(f"原来固定的摄像头 {saved} 已经找不到了，请重新选择。",
                          f"The remembered camera {saved} is no longer available. "
                          f"Please pick another one.")
            camera, remember = select_camera_interactive(
                note=note, remember_init=bool(cfg.get("remember_camera")))
            if camera is None:
                sys.exit(0)
        if remember is not None:      # 只有用户真的被问过才改配置
            remember_camera_setting(camera_device_path(camera), remember)

    acquire_lock()
    eloam = bool(find_eloam_pids())
    if eloam:
        stop_eloam()
        time.sleep(0.5)
    cam = _open_camera_with_policy(camera)
    if cam is None:
        if eloam: restart_eloam()
        release_lock()
        sys.exit(1)
    root = tk.Tk(className="FlipScan")
    _set_window_icon(root)
    App(root, cam, eloam)
    maybe_check_updates(cfg)
    root.mainloop()
    cam.release()
    release_lock()
    if eloam: restart_eloam()

if __name__ == "__main__":
    main()

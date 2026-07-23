#!/bin/bash
# Copyright (C) 2026 ENum
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version. This program is distributed WITHOUT ANY WARRANTY; see the GNU General
# Public License (LICENSE) for details.
#
# ============================================================================
#  ENum 一键安装器 · 可复用模板 (install-template.sh)
#  --------------------------------------------------------------------------
#  作用：把一个程序装好，在【桌面】和【应用程序列表】各放一个已经允许运行的
#        图标。默认装到用户目录、全程不需要管理员密码、兼容各 Linux 发行版。
#
#  交互模式：在终端里直接运行（stdin 是 TTY 且不带任何参数）时进入问答：
#     ① 安装位置：用户目录（默认）/ 自定义路径 / 系统目录 /opt
#        （系统级走 sudo，密码由 sudo 自己在终端提示，本脚本不经手密码）
#     ② 是否开机自启动（仅 ASK_AUTOSTART=1 的项目会问）
#     ③ 是否启动时自动检查更新（仅 ASK_AUTO_UPDATE=1 的项目会问，默认开）
#     ④ 是否放桌面图标（免「信任」确认，双击即可启动）
#     ⑤ 项目自定义问题（CUSTOMIZE_HOOK，各项目 install.sh 自己实现）
#  非 TTY（管道/脚本调用）或带了任何参数时，走下面的非交互逻辑，完全向后兼容。
#
#  复用到新项目：拷贝本文件为该项目的 install.sh，改【配置区】几行；
#  有安装期专属问题就重写 CUSTOMIZE_HOOK()。
#
#  普通用法：      bash install.sh
#  高级用法(可选)：
#     bash install.sh --system            系统级安装(需管理员，装到 /opt + /usr/share)
#     bash install.sh --prefix ~/apps     指定安装位置(把程序复制到 该目录/APP_ID 再建图标)
#     bash install.sh --no-desktop-icon   只进应用菜单，不在桌面放图标
#     bash install.sh --autostart         设置开机自启动(非交互时用)
#     bash install.sh --auto-update       开启启动时自动检查更新(非交互时用)
#     bash install.sh --no-auto-update    关闭启动时自动检查更新(非交互时用)
#     bash install.sh --uninstall         卸载(移除桌面/菜单图标与自启动；--prefix/--system 同时给则一并删程序副本)
#     bash install.sh --help              查看帮助
# ============================================================================
set -e

# ── 配置区（换项目时只改这里）───────────────────────────────────────────────
APP_ID="flipscan"                          # 唯一标识（.desktop 文件名，用英文小写连字符）
APP_WMCLASS="Flipscan"                     # WM_CLASS 的 Class 段。**必须是 Flipscan 而不是显示名 FlipScan**：
                                           # tkinter 对 className 强制走 capitalize()，传什么都得到 Flipscan，
                                           # 这里写成 FlipScan 就与实际窗口对不上，任务栏会退回默认图标。
APP_NAME="FlipScan"                        # 菜单/桌面显示名称（英文，默认）
APP_COMMENT="Auto-capture every page you flip, with scan enhancement"        # 悬浮说明（英文）
APP_COMMENT_ZH="高拍仪翻页自动拍照 + 扫描增强"                        # 悬浮说明（中文，留空则同上）
APP_ICON="flipscan.png"       # 图标：仓库内自带的高拍仪图标。原来用主题名 camera-photo，
                             # 而 Yaru 主题里它只有 symbolic(单色线稿)版本，桌面与应用
                             # 列表按不同规则回退，于是两处显示成不一样的图标
EXEC_REL="run.sh"                        # 仓库内的启动入口（相对本脚本；.py 会自动用 python 拉起）
EXEC_ARGS="--capture"                             # 启动参数（没有就留空）
RUN_IN_TERMINAL="false"                  # 需要终端窗口就改 true
CATEGORIES="Graphics;Scanning;"                     # 应用菜单分类
NEEDS_VENV="1"                           # 需要 Python venv 装依赖就填 1
VENV_DIR="gemini_env"                         # venv 目录名
PYDEPS="opencv-python google-generativeai"                                # venv 里要装的 pip 包，空格分隔
VENV_SYSTEM_SITE="1"                     # venv 是否继承系统包（用到 PyGObject 等系统库时需要）
ASK_AUTOSTART="0"                        # 交互时是否询问「开机自启动」（1=问；只有需要常驻的项目才开）
AUTOSTART_ARGS=""                        # 自启动条目额外附加的启动参数（如 --hidden）
ASK_AUTO_UPDATE="1"                      # 交互时是否询问「启动时自动检查更新」（1=问）
CONFIG_PATHS="$HOME/.config/flipscan"
DATA_PATHS=""

POST_INSTALL_NOTE="纯拍照即装即用；AI 分析需在 analyze_papers.py 填 Gemini API Key。"                     # 安装完额外提示（可留空）
POST_INSTALL_NOTE_EN="Capture works out of the box; AI analysis needs a Gemini API key in analyze_papers.py."                  # 上一条的英文版（留空则英文环境也显示上一条）

# 项目自定义安装钩子：各项目的 install.sh 重写本函数，在安装过程中问自己的
# 问题/写自己的配置。默认空实现。调用时机：程序文件已就位、图标尚未生成。
# 可用变量：INSTALL_DIR RUN_DIR SUDO MODE INTERACTIVE UI_LANG(zh|en)
# 可用函数：say "中文" "English"    输出一行（自动按语言挑选）
#           tr_ "中文" "English"    只取字符串不换行
# ── Flipscan 专属：安装时枚举摄像头让用户选，固定写入 ~/.config/flipscan/config.json ──
# 程序启动时按「命令行 --camera > 配置固定(remember_camera) > 交互挑选」取摄像头，
# 这里写的就是中间那层；装完随时可在程序侧栏「更改」里换。
CUSTOMIZE_HOOK() {
  # ① 把「启动时自动检查更新」的答案落进 ~/.config/flipscan/config.json。
  #    读-改-写整份配置，保留已有的摄像头设置，不自己拼 JSON 免得写坏。
  #    以桌面用户身份跑——sudo 装到 /opt 时配置仍属于坐在电脑前的那个人。
  as_desk_user env HOME="$DESK_HOME" python3 - "$WANT_AUTO_UPDATE" <<'PYEOF' 2>/dev/null || true
import json, os, sys
base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
    os.path.expanduser("~"), ".config")
path = os.path.join(base, "flipscan", "config.json")
try:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}
except Exception:
    cfg = {}
cfg["auto_check_updates"] = (sys.argv[1] == "1")
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.replace(tmp, path)
PYEOF

  # ② 摄像头挑选（交互时才问）
  [ "$INTERACTIVE" = "1" ] || return 0
  echo ""
  say "▶ 检测摄像头 ..." "▶ Detecting cameras ..."
  local devs="" d n
  if command -v v4l2-ctl >/dev/null 2>&1; then
    devs="$(v4l2-ctl --list-devices 2>/dev/null | grep -o '/dev/video[0-9]*' || true)"
  fi
  if [ -z "$devs" ]; then
    # 没有 v4l2-ctl 时用 OpenCV 逐个探测 /dev/video*
    devs="$("$PY_BIN" - 2>/dev/null <<'PYEOF' || true
import glob
try:
    import cv2
except Exception:
    raise SystemExit
for d in sorted(glob.glob("/dev/video[0-9]*"), key=lambda p: int(p[10:])):
    c = cv2.VideoCapture(d)
    if c.isOpened():
        print(d)
    c.release()
PYEOF
)"
  fi
  # 同一个 USB 摄像头会生成「视频 + 元数据」两个节点，只留采集节点(index=0)
  local keep=""
  for d in $devs; do
    n="${d#/dev/video}"
    if [ -f "/sys/class/video4linux/video$n/index" ] && \
       [ "$(cat "/sys/class/video4linux/video$n/index")" != "0" ]; then continue; fi
    keep="$keep $d"
  done
  devs="$keep"
  if [ -z "${devs// /}" ]; then
    say "未检测到摄像头，跳过（首次启动时程序会再询问）。" \
        "No camera detected; skipped (the app will ask on first launch)."
    return 0
  fi
  local i=0 name
  say "检测到以下摄像头：" "Cameras found:"
  local _cam_choices=()
  _cam_choices+=("0|$(tr_ "跳过（首次启动时再选）" "Skip (choose at first launch)")")
  for d in $devs; do
    i=$((i+1))
    n="${d#/dev/video}"
    name="$(cat "/sys/class/video4linux/video$n/name" 2>/dev/null || echo "$d")"
    printf '  %d) %s · %s\n' "$i" "$d" "$name"
    eval "_cam_$i=\$d"
    _cam_choices+=("$i|$d · $name")
  done
  if [ "${USE_GUI:-0}" = "1" ]; then
    if enum_gui_eval list "$(tr_ "选择摄像头" "Pick a camera")" \
        "$(tr_ "选择 FlipScan 的默认摄像头" "Choose the default camera for FlipScan")" \
        "${_cam_choices[@]}"; then
      _c="${CHOICE:-0}"
    else
      _c="0"
    fi
  else
    printf '%s' "$(tr_ "选择默认摄像头 [1-$i]（回车=跳过，首次启动时再选）: " \
                       "Pick the default camera [1-$i] (Enter=skip, choose at first launch): ")"
    read -r _c || _c=""
  fi
  case "$_c" in ''|0|*[!0-9]*) return 0 ;; esac
  { [ "$_c" -ge 1 ] && [ "$_c" -le "$i" ]; } || return 0
  eval "d=\$_cam_$_c"
  local cfg_dir="${XDG_CONFIG_HOME:-$DESK_HOME/.config}/flipscan"
  as_desk_user mkdir -p "$cfg_dir"
  # 读-改-写，和上面写 auto_check_updates 那段一样。
  # 原来这里是直接 printf 一整个 JSON 覆盖过去，会把刚刚问到的
  # auto_check_updates 抹掉——用户明确选了"不要自动检查更新"，装完却变回默认的
  # "要"，等于把隐私选择悄悄反转了。别再自己拼 JSON。
  as_desk_user env FLIPSCAN_CAM="$d" python3 - "$cfg_dir/config.json" << 'PYEOF'
import json, os, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}
except Exception:
    cfg = {}
cfg["camera"] = os.environ["FLIPSCAN_CAM"]
cfg["remember_camera"] = True
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.replace(tmp, path)
PYEOF
  say "已固定默认摄像头：$d（可在程序侧栏「更改」里随时换）" \
      "Default camera pinned: $d (change anytime via the sidebar Change button)"
}
# ────────────────────────────────────────────────────────────────────────────

# ── 界面语言：LC_ALL > LC_MESSAGES > LANG；zh* → 中文，其余 → 英文 ─────────
_loc="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
case "$_loc" in zh*) UI_LANG="zh" ;; *) UI_LANG="en" ;; esac
tr_() { if [ "$UI_LANG" = "zh" ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }
say() { printf '%s\n' "$(tr_ "$1" "$2")"; }

# ── 解析参数 ────────────────────────────────────────────────────────────────
NARGS=$#
MODE="user"          # user | system
PREFIX=""            # 自定义安装位置
WANT_DESKTOP_ICON="1"
WANT_AUTOSTART="0"
# 自动检查更新默认开：有新版且目录可写时会静默安装；用户随时可以在设置里关掉。
# 而"装完就再也收不到安全修复"的默认值对用户更不利。
WANT_AUTO_UPDATE="1"
DO_UNINSTALL="0"
WANT_YES="0"
WANT_KEEP_CONFIG="0"
WANT_INPLACE="0"
while [ $# -gt 0 ]; do
  case "$1" in
    --system) MODE="system" ;;
    --prefix) PREFIX="$2"; shift ;;
    --prefix=*) PREFIX="${1#*=}" ;;
    --no-desktop-icon) WANT_DESKTOP_ICON="0" ;;
    --autostart) WANT_AUTOSTART="1" ;;
    --auto-update) WANT_AUTO_UPDATE="1" ;;
    --no-auto-update) WANT_AUTO_UPDATE="0" ;;
    --uninstall) DO_UNINSTALL="1" ;;
    --keep-config) WANT_KEEP_CONFIG="1" ;;
    --inplace) WANT_INPLACE="1" ;;
    --yes|-y) WANT_YES="1" ;;
    --cli) ENUM_FORCE_CLI=1 ;;
    --help|-h)
      # 帮助必须跟随系统语言。以前这里是把文件开头的注释块原样打出来，那块注释
      # 是中文写给维护者看的，于是英文用户 --help 也只能看到中文——而英文版
      # README/Release 恰恰都在教英文用户跑这个脚本。
      if [ "$UI_LANG" = "zh" ]; then
        cat << HELPZH
$APP_NAME 安装器

用法：
  bash $(basename "$0")                  在终端直接运行 = 交互式安装（推荐）
  bash $(basename "$0") [选项]           带任何选项 = 全程不提问

选项：
  --system              装到系统目录 /opt，所有用户可用（需要管理员密码）
  --prefix <目录>       装到指定目录
  --no-desktop-icon     只进应用菜单，不放桌面图标
  --autostart           打开开机自启动
  --auto-update         打开"启动时自动检查更新"
  --no-auto-update      关闭"启动时自动检查更新"
  --uninstall           卸载（移除桌面图标、菜单项、自启动项）
  --help, -h            显示这份帮助

交互式安装会依次问：装到哪里、要不要开机自启、要不要自动检查更新、
要不要桌面图标，以及本程序自己的设置项。一路回车即为默认值。
默认的用户级安装不需要管理员密码，桌面图标已标记为受信任，双击即可启动。
HELPZH
      else
        cat << HELPEN
$APP_NAME installer

Usage:
  bash $(basename "$0")                  run it in a terminal = interactive install (recommended)
  bash $(basename "$0") [options]        any option = fully non-interactive

Options:
  --system              install system-wide to /opt (asks for your admin password)
  --prefix <dir>        install to a directory of your choice
  --no-desktop-icon     application menu entry only, no desktop icon
  --autostart           start automatically when you log in
  --auto-update         check for updates at startup
  --no-auto-update      do not check for updates at startup
  --uninstall           remove the desktop icon, menu entry and autostart entry
  --help, -h            show this help

The interactive install asks where to install, whether to start at login,
whether to check for updates, whether to place a desktop icon, and this
program's own settings. Press Enter for the default everywhere.
A default user-level install needs no admin password, and the desktop icon
is marked trusted so it launches on double-click.
HELPEN
      fi
      exit 0 ;;
    *) say "未知参数: $1（--help 查看用法）" "Unknown option: $1 (see --help)"; exit 1 ;;
  esac
  shift
done

SRC="$(cd "$(dirname "$0")" && pwd)"

# ── 图形界面（有 DISPLAY 时默认走 GUI；ENUM_FORCE_CLI=1 或 --cli 强制终端）──
enum_gui_available() {
  [ "${ENUM_FORCE_CLI:-0}" = "1" ] && return 1
  [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 1
  [ -f "$SRC/enum-gui-ask.py" ] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null
}
enum_gui_eval() {
  local out
  out="$(python3 "$SRC/enum-gui-ask.py" "$@")" || return $?
  eval "$out"
  return 0
}

# ── ENum Setup 单实例锁（与 EnumSetup / 各 install.sh 共用）────────────────
_ENUM_LOCK_DIR="${XDG_RUNTIME_DIR:-/tmp}"
_ENUM_LOCK_FILE="$_ENUM_LOCK_DIR/enum-setup-${UID:-$(id -u)}.lock"
enum_setup_acquire_lock() {
  if [ "${ENUM_SETUP_NESTED:-0}" = "1" ]; then return 0; fi
  mkdir -p "$_ENUM_LOCK_DIR"
  exec 9>"$_ENUM_LOCK_FILE"
  if ! flock -n 9; then
    say "已有安装器在运行，请先关掉另一个窗口。" \
        "Another installer is already running; close it first."
    exit 1
  fi
}
enum_is_dev_tree() {
  case "$SRC" in
    */Enum\ Code/github/*|*/Enum\ Code/*) return 0 ;;
  esac
  [ "${ENUM_DEV_TREE:-0}" = "1" ] && return 0
  return 1
}
enum_scan_remove_desktop() {
  local desk
  for desk in \
      "$(as_desk_user env HOME="$DESK_HOME" xdg-user-dir DESKTOP 2>/dev/null || true)" \
      "$DESK_HOME/Desktop" "$DESK_HOME/桌面"; do
    [ -n "$desk" ] && [ -d "$desk" ] || continue
    rm -f "$desk/$APP_ID.desktop" 2>/dev/null || true
    for f in "$desk"/*.desktop; do
      [ -f "$f" ] || continue
      if grep -qE "Exec=.*$APP_ID|/enum/$APP_ID/" "$f" 2>/dev/null; then
        rm -f "$f" 2>/dev/null || true
      fi
    done
  done
}

enum_setup_acquire_lock

# ── 交互模式：无参数时进入；有图形界面优先 GUI，否则终端问答 ───────────────
INTERACTIVE=0
USE_GUI=0
if enum_gui_available; then USE_GUI=1; fi
if [ "$NARGS" -eq 0 ]; then
  if [ "$USE_GUI" = "1" ] || [ -t 0 ]; then INTERACTIVE=1; fi
fi
if [ "$DO_UNINSTALL" = "1" ] && [ "$USE_GUI" = "1" ] && [ "${ENUM_SETUP_NESTED:-0}" != "1" ] \
   && [ "${ENUM_UNINSTALL_CONFIRMED:-0}" != "1" ] && [ "${WANT_YES:-0}" != "1" ]; then
  INTERACTIVE=1
fi

if [ "$INTERACTIVE" = "1" ] && [ "$USE_GUI" = "1" ]; then
  if [ "$DO_UNINSTALL" = "1" ]; then
    _keep_ui=1
    case "$SRC" in */Enum\ Code/github/*|*/Enum\ Code/*) _keep_ui=0 ;; esac
    [ "${ENUM_DEV_TREE:-0}" = "1" ] && _keep_ui=0
    if ! enum_gui_eval uninstall "$APP_NAME" "$_keep_ui"; then
      say "已取消。" "Cancelled."; exit 0
    fi
    [ "${CANCELLED:-0}" = "1" ] && { say "已取消。" "Cancelled."; exit 0; }
    ENUM_UNINSTALL_CONFIRMED=1
  else
    if ! enum_gui_eval install "$APP_NAME" "${ASK_AUTOSTART:-0}" "${ASK_AUTO_UPDATE:-0}"; then
      say "已取消。" "Cancelled."; exit 0
    fi
    [ "${CANCELLED:-0}" = "1" ] && { say "已取消。" "Cancelled."; exit 0; }
    if [ -n "${PREFIX:-}" ]; then
      case "$PREFIX" in "~"|"~/"*) PREFIX="$HOME${PREFIX#\~}" ;; esac
    fi
  fi
elif [ "$INTERACTIVE" = "1" ]; then
  say "▶ 安装 $APP_NAME" "▶ Installing $APP_NAME"
  echo ""
  say "安装位置：" "Install location:"
  say "  1) 用户程序目录 ~/.local/share/enum（默认）" "  1) User ~/.local/share/enum (default)"
  say "  2) 自定义路径" "  2) Custom path"
  say "  3) 系统目录 /opt/enum" "  3) System /opt/enum"
  say "  4) 开发树原地" "  4) In-place from source"
  printf '%s' "$(tr_ "请选择 [1/2/3/4]（回车=1）: " "Choose [1/2/3/4] (Enter=1): ")"
  read -r _choice || _choice=""
  case "$_choice" in
    2)
      printf '%s' "$(tr_ "请输入安装路径: " "Install path: ")"
      read -r _p || _p=""
      case "$_p" in "~"|"~/"*) _p="$HOME${_p#\~}" ;; esac
      if [ -n "$_p" ]; then PREFIX="$_p"; else say "未输入路径，改用用户目录。" "No path given, using user directory."; fi
      ;;
    3) MODE="system" ;;
    4) WANT_INPLACE="1" ;;
    *) : ;;
  esac
  if [ "${ASK_AUTOSTART:-0}" = "1" ]; then
    printf '%s' "$(tr_ "开机自启动 $APP_NAME？[y/N]: " "Start $APP_NAME at login? [y/N]: ")"
    read -r _a || _a=""
    case "$_a" in y|Y|yes|YES) WANT_AUTOSTART="1" ;; *) WANT_AUTOSTART="0" ;; esac
  fi
  if [ "${ASK_AUTO_UPDATE:-0}" = "1" ]; then
    printf '%s' "$(tr_ "启动时自动检查更新？[Y/n]: " "Check for updates at startup? [Y/n]: ")"
    read -r _u || _u=""
    case "$_u" in n|N|no|NO) WANT_AUTO_UPDATE="0" ;; *) WANT_AUTO_UPDATE="1" ;; esac
  fi
  printf '%s' "$(tr_ "在桌面放一个图标？[Y/n]: " "Desktop icon? [Y/n]: ")"
  read -r _d || _d=""
  case "$_d" in n|N|no|NO) WANT_DESKTOP_ICON="0" ;; *) WANT_DESKTOP_ICON="1" ;; esac
  echo ""
fi

# 需要 root 权限时的包装：系统级用 sudo，用户级为空（永不弹认证）
# 密码永远由 sudo 自己在终端提示，本脚本不读取、不保存任何密码。
SUDO=""
if [ "$MODE" = "system" ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else
    say "⚠ --system 需要 root，但没有 sudo；请用 root 运行，或改用默认的用户级安装。" \
        "⚠ --system needs root but sudo is missing; run as root or use the default user-level install."
    exit 1
  fi
fi

# 桌面用户：即使整个脚本被 root 运行（sudo bash install.sh --system），
# 桌面图标/自启动仍要落到真正坐在桌面前的那个用户身上，gio 也要以他的
# dbus 会话执行，否则「信任」标记无效。
if [ "$(id -u)" = "0" ] && [ -n "${SUDO_USER:-}" ]; then
  DESK_USER="$SUDO_USER"
  DESK_HOME="$(getent passwd "$DESK_USER" | cut -d: -f6)"
  [ -z "$DESK_HOME" ] && DESK_HOME="$HOME"
else
  DESK_USER="$(id -un)"
  DESK_HOME="$HOME"
fi
as_desk_user() {
  if [ "$(id -u)" = "0" ] && [ -n "${SUDO_USER:-}" ]; then
    sudo -u "$DESK_USER" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u "$DESK_USER")/bus" "$@"
  else
    "$@"
  fi
}

# 目标目录：系统级 → /usr/share/applications；用户级 → ~/.local/share/applications
if [ "$MODE" = "system" ]; then
  APPS_DIR="/usr/share/applications"
  DEFAULT_PREFIX="/opt/enum"
else
  APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  DEFAULT_PREFIX="$SRC"      # 用户级默认原地运行，不复制
fi

# ── 图标主题目录 ────────────────────────────────────────────────────────────
# 为什么不能图省事在 .desktop 里写绝对路径：freedesktop 规范确实允许
# Icon= 用绝对路径，任务栏（窗口图标）也认；但 GNOME 的【程序列表 / 应用网格】
# 是按**图标主题里的名字**查的，绝对路径在那儿会退化成默认的齿轮/方块图标
# ——用户看到的现象就是「任务栏图标变了、程序列表没变」。所以统一做法：
#   ① 图标文件拷进 hicolor/<尺寸>/apps/<图标名>.<后缀>
#   ② gtk-update-icon-cache 重建缓存（缓存过期会让 GTK 直接无视新拷进去的文件）
#   ③ .desktop 里 Icon= 只写图标名
# 这样任务栏、程序列表、Dash、搜索结果拿到的是同一个图标。
if [ "$MODE" = "system" ]; then
  ICONS_ROOT="/usr/share/icons/hicolor"
else
  ICONS_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
fi

# 读 PNG 头里的真实宽度（第 16~19 字节，大端）。不是 PNG 或没有 od 就返回空。
png_width() { od -An -tu4 -j16 -N4 --endian=big "$1" 2>/dev/null | tr -d ' \n'; }

# install_icon <图标源文件> <目标图标名>：装进主题目录，并把图标名回填 ICON_VALUE
install_icon() {
  local src="$1" name="$2" ext dir w
  ext="${src##*.}"
  if [ "$ext" = "svg" ] || [ "$ext" = "svgz" ]; then
    dir="$ICONS_ROOT/scalable/apps"
  else
    w="$(png_width "$src")"
    case "$w" in
      16|22|24|32|36|48|64|72|96|128|192|256|512) dir="$ICONS_ROOT/${w}x${w}/apps" ;;
      *) dir="$ICONS_ROOT/256x256/apps" ;;   # 读不出尺寸/非标准档位：放 256，GTK 自己缩放
    esac
  fi
  $SUDO mkdir -p "$dir"
  $SUDO cp -f "$src" "$dir/$name.$ext"
  $SUDO chmod 644 "$dir/$name.$ext"
  ICON_VALUE="$name"
}

# remove_icon <图标名>：把该名字在 hicolor 各尺寸下的副本都删掉
remove_icon() {
  [ -d "$ICONS_ROOT" ] || return 0
  $SUDO find "$ICONS_ROOT" -type f \
    \( -name "$1.png" -o -name "$1.svg" -o -name "$1.svgz" -o -name "$1.xpm" \) \
    -delete 2>/dev/null || true
}

# 重建图标缓存。注意：hicolor 下若留着一个**过期**的 icon-theme.cache，
# GTK 会优先信缓存、完全看不见新拷进去的图标，所以缓存更新失败时必须把它删掉
# （没有缓存 GTK 会退回逐目录扫描，慢一点但结果正确）。
refresh_icon_cache() {
  [ -d "$ICONS_ROOT" ] || return 0
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    $SUDO gtk-update-icon-cache -f -t "$ICONS_ROOT" >/dev/null 2>&1 \
      || $SUDO gtk-update-icon-cache -f -t --ignore-theme-index "$ICONS_ROOT" >/dev/null 2>&1 \
      || $SUDO rm -f "$ICONS_ROOT/icon-theme.cache" 2>/dev/null || true
  else
    $SUDO rm -f "$ICONS_ROOT/icon-theme.cache" 2>/dev/null || true
  fi
}


# 桌面目录：跨发行版/跨语言，用 xdg-user-dir，兜底 ~/Desktop（始终按桌面用户算）
DESKTOP_DIR="$(as_desk_user env HOME="$DESK_HOME" xdg-user-dir DESKTOP 2>/dev/null || true)"
[ -z "$DESKTOP_DIR" ] || [ ! -d "$DESKTOP_DIR" ] && DESKTOP_DIR="$DESK_HOME/Desktop"

# 自启动目录（同样按桌面用户算）
AUTOSTART_DIR="$DESK_HOME/.config/autostart"
[ "$DESK_HOME" = "$HOME" ] && AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"

# 程序实际所在目录（--prefix 或 --system 会把程序复制过去；否则原地）
# 强制默认程序目录与配置分离（覆盖脚本前面可能把 DEFAULT_PREFIX 设成 $SRC 的旧逻辑）
if [ "${WANT_INPLACE:-0}" = "1" ]; then
  INSTALL_DIR="$SRC"
elif [ -n "$PREFIX" ]; then
  INSTALL_DIR="$PREFIX/$APP_ID"
elif [ "$MODE" = "system" ]; then
  INSTALL_DIR="/opt/enum/$APP_ID"
else
  INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/enum/$APP_ID"
fi

# 自定义路径落在没有写权限的目录（如 /opt）时同样走 sudo——
# 「想装进管理员的文件夹，输入密码就行」，密码仍由 sudo 自己提示。
if [ -z "$SUDO" ] && [ "$INSTALL_DIR" != "$SRC" ]; then
  _probe="$(dirname "$INSTALL_DIR")"
  while [ ! -e "$_probe" ]; do _probe="$(dirname "$_probe")"; done
  if [ ! -w "$_probe" ]; then
    if command -v sudo >/dev/null 2>&1; then
      say "目标目录需要管理员权限，sudo 可能提示输入密码。" \
          "Target directory needs admin rights; sudo may prompt for your password."
      SUDO="sudo"
    else
      say "⚠ 目标目录不可写且没有 sudo，请换一个路径。" \
          "⚠ Target directory is not writable and sudo is missing; choose another path."
      exit 1
    fi
  fi
fi

# ── 卸载 ────────────────────────────────────────────────────────────────────
if [ "$DO_UNINSTALL" = "1" ]; then
  $SUDO rm -f "$APPS_DIR/$APP_ID.desktop"
  rm -f "$AUTOSTART_DIR/$APP_ID.desktop" 2>/dev/null || true
  enum_scan_remove_desktop
  command -v update-desktop-database >/dev/null 2>&1 && $SUDO update-desktop-database "$APPS_DIR" 2>/dev/null || true
  remove_icon "$APP_ID"
  for _pair in $EXTRA_ICONS; do remove_icon "${_pair%%:*}"; done
  refresh_icon_cache
  _dev=0
  enum_is_dev_tree && _dev=1
  if [ "$_dev" = "1" ]; then
    say "检测到 Enum 开发树：不删除源码与配置（只移除菜单/桌面/自启）。" \
        "Dev tree detected: source and config kept (menu/desktop/autostart only)."
  else
    if [ "$INSTALL_DIR" != "$SRC" ] && [ -d "$INSTALL_DIR" ]; then
      $SUDO rm -rf "$INSTALL_DIR"
      say "已删除程序目录：$INSTALL_DIR" "Removed program directory: $INSTALL_DIR"
    fi
    if [ "${WANT_KEEP_CONFIG:-0}" != "1" ]; then
      for _c in $CONFIG_PATHS; do
        case "$_c" in "~"|"~/"*) _c="$HOME${_c#\~}" ;; esac
        [ -e "$_c" ] && rm -rf "$_c" && say "已删除配置：$_c" "Removed config: $_c"
      done
      for _d in $DATA_PATHS; do
        case "$_d" in "~"|"~/"*) _d="$HOME${_d#\~}" ;; esac
        [ -e "$_d" ] && rm -rf "$_d" && say "已删除数据：$_d" "Removed data: $_d"
      done
    else
      say "已按 --keep-config 保留配置与数据。" "Kept config/data per --keep-config."
    fi
  fi
  say "✅ 已卸载。" "✅ Uninstalled."
  if [ "${USE_GUI:-0}" = "1" ]; then
    enum_gui_eval info "$(tr_ "卸载完成" "Uninstalled")" "$(tr_ "$APP_NAME 已卸载。" "$APP_NAME has been uninstalled.")" || true
  fi
  exit 0
fi

[ "$INTERACTIVE" = "1" ] || say "▶ 安装 $APP_NAME（模式：$MODE）" "▶ Installing $APP_NAME (mode: $MODE)"

# ── 复制程序到目标位置（仅当不是原地运行）──────────────────────────────────
if [ "$INSTALL_DIR" != "$SRC" ]; then
  say "▶ 复制程序到 $INSTALL_DIR ..." "▶ Copying program to $INSTALL_DIR ..."
  $SUDO mkdir -p "$INSTALL_DIR"
  # 排除 .git 和虚拟环境目录，两个原因：
  # ① 体积——.git 是整个仓库历史，venv 动辄几百 MB，装到目标位置纯属浪费；
  # ② 重装会失败——git 对象文件是 444 只读，`cp -a` 覆盖不了它们，
  #    第二次跑安装器会满屏"权限不够"并直接退出（自动升级要重跑安装器，必须能重复执行）。
  # 用 tar 管道而不是 cp：能可靠地排除目录，且保留权限/符号链接。
  ( cd "$SRC" && tar cf - \
      --exclude=./.git --exclude=./.git/* \
      --exclude=./__pycache__ --exclude='./*/__pycache__' \
      --exclude=./.venv --exclude=./venv --exclude=./gemini_env --exclude=./vocotype \
      --exclude=./node_modules \
      . ) | $SUDO tar xf - -C "$INSTALL_DIR"
fi
RUN_DIR="$INSTALL_DIR"

# ── Python 依赖（装进 venv，不动系统）──────────────────────────────────────
if [ "$NEEDS_VENV" = "1" ] && [ -n "$PYDEPS" ]; then
  if [ ! -d "$RUN_DIR/$VENV_DIR" ]; then
    say "▶ 创建虚拟环境 $VENV_DIR ..." "▶ Creating virtualenv $VENV_DIR ..."
    if [ "$VENV_SYSTEM_SITE" = "1" ]; then
      $SUDO python3 -m venv --system-site-packages "$RUN_DIR/$VENV_DIR"
    else
      $SUDO python3 -m venv "$RUN_DIR/$VENV_DIR"
    fi
  fi
  say "▶ 安装依赖（可能要几分钟）..." "▶ Installing dependencies (may take a few minutes)..."
  $SUDO "$RUN_DIR/$VENV_DIR/bin/pip" install --upgrade pip >/dev/null || true
  $SUDO "$RUN_DIR/$VENV_DIR/bin/pip" install $PYDEPS || \
    say "⚠ 有依赖没装上（可能缺系统库，见上方输出与 README），可稍后手动补装。" \
        "⚠ Some dependencies failed (missing system libs? see output and README); install them later if needed."
fi

$SUDO chmod +x "$RUN_DIR/$EXEC_REL" 2>/dev/null || true

# ── 图标：装进图标主题目录，.desktop 里只写图标名（原因见前面 install_icon）──
ICON_VALUE="$APP_ICON"        # 配置里写的本来就是主题图标名时，原样使用
for _cand in "$RUN_DIR/$APP_ICON" "$SRC/$APP_ICON"; do
  if [ -f "$_cand" ]; then install_icon "$_cand" "$APP_ID"; break; fi
done
# 附带图标（如设置界面用的第二个图标）：EXTRA_ICONS="名字:相对路径 名字:相对路径"
for _pair in $EXTRA_ICONS; do
  _n="${_pair%%:*}"; _p="${_pair#*:}"
  for _cand in "$RUN_DIR/$_p" "$SRC/$_p"; do
    if [ -f "$_cand" ]; then
      _keep="$ICON_VALUE"; install_icon "$_cand" "$_n"; ICON_VALUE="$_keep"; break
    fi
  done
done
refresh_icon_cache

# .py 入口用 python 拉起；有 venv 用 venv 的 python
if [ "$NEEDS_VENV" = "1" ] && [ -x "$RUN_DIR/$VENV_DIR/bin/python" ]; then PY_BIN="$RUN_DIR/$VENV_DIR/bin/python"; else PY_BIN="python3"; fi
case "$EXEC_REL" in
  *.py) EXEC_LINE="$PY_BIN \"$RUN_DIR/$EXEC_REL\"" ;;
  *)    EXEC_LINE="\"$RUN_DIR/$EXEC_REL\"" ;;
esac
[ -n "$EXEC_ARGS" ] && EXEC_LINE="$EXEC_LINE $EXEC_ARGS"

# ── 项目自定义安装钩子（各项目问自己的问题/写自己的配置）────────────────────
CUSTOMIZE_HOOK

# make_desktop [额外Exec参数]
make_desktop() {
local extra="${1:-}"
local exec_line="$EXEC_LINE"
[ -n "$extra" ] && exec_line="$exec_line $extra"
cat <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
Comment=$APP_COMMENT
EOF
# 应用列表和任务栏里一律用英文名（上面的 Name=），不给 Name[zh_CN]：
# 名字是这个程序在系统里的身份，各语言各叫各的反而难找、难搜、难交流。
# 界面语言由程序自己在启动时按 locale 适配，中文系统打开后仍然是中文界面。
# 悬浮说明（Comment）保留中文，它不是名称，只是鼠标停留时的一句解释。
[ -n "$APP_COMMENT_ZH" ] && printf 'Comment[zh_CN]=%s\nComment[zh_TW]=%s\n' "$APP_COMMENT_ZH" "$APP_COMMENT_ZH"
cat <<EOF
Exec=$exec_line
Icon=$ICON_VALUE
Terminal=$RUN_IN_TERMINAL
Categories=$CATEGORIES
StartupNotify=false
StartupWMClass=$APP_WMCLASS
EOF
}

# ── 写进【应用程序列表】────────────────────────────────────────────────────
$SUDO mkdir -p "$APPS_DIR"
make_desktop | $SUDO tee "$APPS_DIR/$APP_ID.desktop" >/dev/null
$SUDO chmod +x "$APPS_DIR/$APP_ID.desktop"
command -v update-desktop-database >/dev/null 2>&1 && $SUDO update-desktop-database "$APPS_DIR" 2>/dev/null || true
say "▶ 已添加到应用程序列表" "▶ Added to the application menu"

# ── 写到【桌面】并标记为「已允许运行」──────────────────────────────────────
if [ "$WANT_DESKTOP_ICON" = "1" ]; then
  as_desk_user mkdir -p "$DESKTOP_DIR"
  DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"
  make_desktop | as_desk_user tee "$DESKTOP_FILE" >/dev/null
  as_desk_user chmod +x "$DESKTOP_FILE"
  # GNOME/Nautilus 下标记可信，双击直接运行不弹「允许启动」确认。
  # gio 必须跑在桌面用户自己的 dbus 会话里（as_desk_user 已处理 root/sudo 场景）。
  as_desk_user gio set "$DESKTOP_FILE" metadata::trusted true 2>/dev/null || true
  say "▶ 已在桌面添加图标：$DESKTOP_FILE" "▶ Desktop icon added: $DESKTOP_FILE"
else
  say "▶ 已跳过桌面图标" "▶ Skipped desktop icon"
fi

# ── 开机自启动 ──────────────────────────────────────────────────────────────
if [ "$WANT_AUTOSTART" = "1" ]; then
  as_desk_user mkdir -p "$AUTOSTART_DIR"
  { make_desktop "$AUTOSTART_ARGS"; printf 'X-GNOME-Autostart-enabled=true\n'; } | as_desk_user tee "$AUTOSTART_DIR/$APP_ID.desktop" >/dev/null
  say "▶ 已设置开机自启动：$AUTOSTART_DIR/$APP_ID.desktop" "▶ Autostart enabled: $AUTOSTART_DIR/$APP_ID.desktop"
fi

# ── 让【程序列表】立刻用上新图标 ────────────────────────────────────────────
# GNOME Shell 一收到 .desktop 的变化就会重画应用网格，所以**名字**是立刻更新的。
# 但图标主题那边有大约 5 秒的重扫节流（GTK 的 rescan_if_needed），刚拷进去的
# 图标可能还没进 Shell 的图标索引，于是那一次重画取到的仍是回退图标——看起来
# 就是「名字变了、图标没变」。等过了节流窗口再 touch 一次 .desktop，逼 Shell
# 再重画一次，这次图标一定在了。放后台跑，不拖慢安装，也不需要用户做任何事。
nudge_app_grid() {
  ( sleep 8; for _f in "$@"; do $SUDO touch "$_f" 2>/dev/null || true; done ) >/dev/null 2>&1 &
}
nudge_app_grid "$APPS_DIR/$APP_ID.desktop"

echo ""
if [ "$WANT_DESKTOP_ICON" = "1" ]; then
  say "✅ 安装完成。可在应用列表搜索「$APP_NAME」或双击桌面图标启动。" \
      "✅ Installed. Search \"$APP_NAME\" in the app menu or double-click the desktop icon."
else
  say "✅ 安装完成。可在应用列表搜索「$APP_NAME」启动。" \
      "✅ Installed. Search \"$APP_NAME\" in the app menu to launch."
fi
[ -n "$POST_INSTALL_NOTE" ] && say "   $POST_INSTALL_NOTE" "   ${POST_INSTALL_NOTE_EN:-$POST_INSTALL_NOTE}"
if [ "${USE_GUI:-0}" = "1" ]; then
  _msg="$(tr_ "✅ $APP_NAME 安装完成。" "✅ $APP_NAME installed.")"
  [ -n "$POST_INSTALL_NOTE" ] && _msg="$_msg
$(tr_ "$POST_INSTALL_NOTE" "${POST_INSTALL_NOTE_EN:-$POST_INSTALL_NOTE}")"
  enum_gui_eval info "$(tr_ "安装完成" "Installed")" "$_msg" || true
fi
exit 0

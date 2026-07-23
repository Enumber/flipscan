#!/usr/bin/env python3
"""ENum installer/uninstaller GUI (Gtk3). Prints KEY=value lines for bash to eval.

Modes (argv[1]):
  install   — location + option checkboxes
  uninstall — confirm + optional keep-config
  setup     — EnumSetup multi-app picker
  list      — single-choice list (title, text, then choices as remaining args)
  entry     — text entry (title, text, optional default)
  question  — yes/no (title, text); exit 0=yes 1=no
  info      — info dialog; always exit 0
"""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402


def ui_lang() -> str:
    loc = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "zh" if loc.lower().startswith("zh") else "en"


def T(zh: str, en: str) -> str:
    return zh if ui_lang() == "zh" else en


def emit(**kwargs) -> None:
    for k, v in kwargs.items():
        print(f"{k}={v}")


def run_window(win: Gtk.Window) -> int:
    result = {"code": 1}

    def on_destroy(*_a):
        Gtk.main_quit()

    win.connect("destroy", on_destroy)
    win.show_all()
    Gtk.main()
    return int(result.get("code", 1)), result


def dialog_install(app_name: str, ask_autostart: bool, ask_auto_update: bool) -> int:
    win = Gtk.Window(title=T(f"安装 {app_name}", f"Install {app_name}"))
    win.set_default_size(480, 360)
    win.set_border_width(14)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    win.add(box)
    box.pack_start(
        Gtk.Label(
            label=T(
                "程序默认装到 ~/.local/share/enum/，配置在 ~/.config（分开存放）。",
                "Programs go under ~/.local/share/enum/; config stays in ~/.config.",
            ),
            wrap=True,
            xalign=0,
        ),
        False,
        False,
        0,
    )

    box.pack_start(Gtk.Label(label=T("安装位置", "Install location"), xalign=0), False, False, 0)
    loc_store = [
        ("user", T("用户目录 ~/.local/share/enum（默认）", "User ~/.local/share/enum (default)")),
        ("custom", T("自定义路径", "Custom path")),
        ("system", T("系统 /opt/enum（需管理员）", "System /opt/enum (admin)")),
        ("inplace", T("开发树原地（不复制）", "In-place from source (no copy)")),
    ]
    radios = []
    group = None
    for key, label in loc_store:
        r = Gtk.RadioButton.new_with_label_from_widget(group, label)
        if group is None:
            group = r
        r._enum_key = key  # type: ignore[attr-defined]
        radios.append(r)
        box.pack_start(r, False, False, 0)

    path_entry = Gtk.Entry()
    path_entry.set_placeholder_text(T("自定义安装根路径…", "Custom install root…"))
    path_entry.set_sensitive(False)
    box.pack_start(path_entry, False, False, 0)

    def on_loc_toggled(btn):
        if btn.get_active():
            path_entry.set_sensitive(btn._enum_key == "custom")  # type: ignore[attr-defined]

    for r in radios:
        r.connect("toggled", on_loc_toggled)

    chk_desk = Gtk.CheckButton(label=T("在桌面放图标", "Desktop icon"))
    chk_desk.set_active(True)
    box.pack_start(chk_desk, False, False, 0)

    chk_auto = Gtk.CheckButton(label=T("开机自启动", "Start at login"))
    chk_auto.set_active(False)
    if ask_autostart:
        box.pack_start(chk_auto, False, False, 0)

    chk_upd = Gtk.CheckButton(label=T("启动时自动检查更新", "Check for updates at startup"))
    chk_upd.set_active(True)
    if ask_auto_update:
        box.pack_start(chk_upd, False, False, 0)

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_row.set_halign(Gtk.Align.END)
    box.pack_end(btn_row, False, False, 0)
    btn_cancel = Gtk.Button(label=T("取消", "Cancel"))
    btn_ok = Gtk.Button(label=T("安装", "Install"))
    btn_ok.get_style_context().add_class("suggested-action")
    btn_row.pack_start(btn_cancel, False, False, 0)
    btn_row.pack_start(btn_ok, False, False, 0)

    state = {"ok": False}

    def finish(ok: bool):
        state["ok"] = ok
        win.destroy()

    btn_cancel.connect("clicked", lambda *_: finish(False))
    btn_ok.connect("clicked", lambda *_: finish(True))
    win.connect("delete-event", lambda *_: (finish(False), True)[1])

    win.show_all()
    Gtk.main()
    if not state["ok"]:
        emit(CANCELLED=1)
        return 1

    mode = "user"
    for r in radios:
        if r.get_active():
            mode = r._enum_key  # type: ignore[attr-defined]
            break
    prefix = path_entry.get_text().strip() if mode == "custom" else ""
    emit(
        CANCELLED=0,
        MODE=("system" if mode == "system" else "user"),
        WANT_INPLACE=(1 if mode == "inplace" else 0),
        PREFIX=prefix,
        WANT_DESKTOP_ICON=(1 if chk_desk.get_active() else 0),
        WANT_AUTOSTART=(1 if (ask_autostart and chk_auto.get_active()) else 0),
        WANT_AUTO_UPDATE=(1 if (not ask_auto_update or chk_upd.get_active()) else 0),
    )
    return 0


def dialog_uninstall(app_name: str, show_keep_config: bool) -> int:
    win = Gtk.Window(title=T(f"卸载 {app_name}", f"Uninstall {app_name}"))
    win.set_default_size(440, 220)
    win.set_border_width(14)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    win.add(box)
    box.pack_start(
        Gtk.Label(
            label=T(
                f"确定卸载 {app_name}？\n对外安装会删除程序与配置；开发树只移除图标。",
                f"Uninstall {app_name}?\nRelease installs remove program+config; "
                "dev-tree uninstall only removes icons.",
            ),
            wrap=True,
            xalign=0,
        ),
        False,
        False,
        0,
    )
    chk_keep = Gtk.CheckButton(label=T("保留配置与数据", "Keep config and data"))
    chk_keep.set_active(False)
    if show_keep_config:
        box.pack_start(chk_keep, False, False, 0)

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_row.set_halign(Gtk.Align.END)
    box.pack_end(btn_row, False, False, 0)
    btn_cancel = Gtk.Button(label=T("取消", "Cancel"))
    btn_ok = Gtk.Button(label=T("卸载", "Uninstall"))
    btn_ok.get_style_context().add_class("destructive-action")
    btn_row.pack_start(btn_cancel, False, False, 0)
    btn_row.pack_start(btn_ok, False, False, 0)

    state = {"ok": False}

    def finish(ok: bool):
        state["ok"] = ok
        win.destroy()

    btn_cancel.connect("clicked", lambda *_: finish(False))
    btn_ok.connect("clicked", lambda *_: finish(True))
    win.connect("delete-event", lambda *_: (finish(False), True)[1])
    win.show_all()
    Gtk.main()
    if not state["ok"]:
        emit(CANCELLED=1)
        return 1
    emit(CANCELLED=0, WANT_KEEP_CONFIG=(1 if chk_keep.get_active() else 0))
    return 0


def dialog_setup(apps: list[str]) -> int:
    win = Gtk.Window(title=T("ENum 安装中心", "ENum Setup"))
    win.set_default_size(520, 480)
    win.set_border_width(14)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.add(box)
    box.pack_start(
        Gtk.Label(
            label=T("选择要安装或卸载的 ENum 应用", "Pick ENum apps to install or uninstall"),
            xalign=0,
        ),
        False,
        False,
        0,
    )

    app_checks = []
    for name in apps:
        c = Gtk.CheckButton(label=name)
        c.set_active(True)
        app_checks.append((name, c))
        box.pack_start(c, False, False, 0)

    box.pack_start(Gtk.Separator(), False, False, 4)
    box.pack_start(Gtk.Label(label=T("安装位置", "Install location"), xalign=0), False, False, 0)
    loc_keys = [
        ("user", T("用户目录 ~/.local/share/enum（默认）", "User ~/.local/share/enum (default)")),
        ("custom", T("自定义路径", "Custom path")),
        ("system", T("系统 /opt/enum", "System /opt/enum")),
        ("inplace", T("各应用开发树原地", "In-place from each app tree")),
    ]
    radios = []
    group = None
    for key, label in loc_keys:
        r = Gtk.RadioButton.new_with_label_from_widget(group, label)
        if group is None:
            group = r
        r._enum_key = key  # type: ignore[attr-defined]
        radios.append(r)
        box.pack_start(r, False, False, 0)
    path_entry = Gtk.Entry()
    path_entry.set_sensitive(False)
    box.pack_start(path_entry, False, False, 0)

    def on_loc(btn):
        if btn.get_active():
            path_entry.set_sensitive(btn._enum_key == "custom")  # type: ignore[attr-defined]

    for r in radios:
        r.connect("toggled", on_loc)

    chk_desk = Gtk.CheckButton(label=T("桌面图标", "Desktop icons"))
    chk_desk.set_active(True)
    chk_auto = Gtk.CheckButton(label=T("开机自启动（支持的应用）", "Autostart (where supported)"))
    chk_upd = Gtk.CheckButton(label=T("启动时自动检查更新", "Check for updates at startup"))
    chk_upd.set_active(True)
    chk_un = Gtk.CheckButton(label=T("卸载（而不是安装）", "Uninstall instead of install"))
    for w in (chk_desk, chk_auto, chk_upd, chk_un):
        box.pack_start(w, False, False, 0)

    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_row.set_halign(Gtk.Align.END)
    box.pack_end(btn_row, False, False, 0)
    btn_cancel = Gtk.Button(label=T("取消", "Cancel"))
    btn_ok = Gtk.Button(label=T("开始", "Start"))
    btn_ok.get_style_context().add_class("suggested-action")
    btn_row.pack_start(btn_cancel, False, False, 0)
    btn_row.pack_start(btn_ok, False, False, 0)

    state = {"ok": False}

    def finish(ok: bool):
        state["ok"] = ok
        win.destroy()

    btn_cancel.connect("clicked", lambda *_: finish(False))
    btn_ok.connect("clicked", lambda *_: finish(True))
    win.connect("delete-event", lambda *_: (finish(False), True)[1])
    win.show_all()
    Gtk.main()
    if not state["ok"]:
        emit(CANCELLED=1)
        return 1

    mode = "user"
    for r in radios:
        if r.get_active():
            mode = r._enum_key  # type: ignore[attr-defined]
            break
    selected = [n for n, c in app_checks if c.get_active()]
    emit(
        CANCELLED=0,
        MODE=mode if mode != "custom" else "user",
        PREFIX=(path_entry.get_text().strip() if mode == "custom" else ""),
        WANT_INPLACE=(1 if mode == "inplace" else 0),
        WANT_DESKTOP_ICON=(1 if chk_desk.get_active() else 0),
        WANT_AUTOSTART=(1 if chk_auto.get_active() else 0),
        WANT_AUTO_UPDATE=(1 if chk_upd.get_active() else 0),
        DO_UNINSTALL=(1 if chk_un.get_active() else 0),
        APPS=" ".join(selected),
    )
    return 0


def dialog_list(title: str, text: str, choices: list[str]) -> int:
    """choices are 'id|label' or plain label (id=1-based index)."""
    win = Gtk.Window(title=title)
    win.set_default_size(420, 280)
    win.set_border_width(14)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.add(box)
    if text:
        box.pack_start(Gtk.Label(label=text, wrap=True, xalign=0), False, False, 0)
    radios = []
    group = None
    parsed = []
    for i, c in enumerate(choices, 1):
        if "|" in c:
            cid, lab = c.split("|", 1)
        else:
            cid, lab = str(i), c
        parsed.append((cid, lab))
        r = Gtk.RadioButton.new_with_label_from_widget(group, lab)
        if group is None:
            group = r
        r._enum_id = cid  # type: ignore[attr-defined]
        radios.append(r)
        box.pack_start(r, False, False, 0)
    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_row.set_halign(Gtk.Align.END)
    box.pack_end(btn_row, False, False, 0)
    btn_cancel = Gtk.Button(label=T("取消", "Cancel"))
    btn_ok = Gtk.Button(label=T("确定", "OK"))
    btn_ok.get_style_context().add_class("suggested-action")
    btn_row.pack_start(btn_cancel, False, False, 0)
    btn_row.pack_start(btn_ok, False, False, 0)
    state = {"ok": False}

    def finish(ok: bool):
        state["ok"] = ok
        win.destroy()

    btn_cancel.connect("clicked", lambda *_: finish(False))
    btn_ok.connect("clicked", lambda *_: finish(True))
    win.connect("delete-event", lambda *_: (finish(False), True)[1])
    win.show_all()
    Gtk.main()
    if not state["ok"]:
        emit(CANCELLED=1)
        return 1
    chosen = parsed[0][0]
    for r in radios:
        if r.get_active():
            chosen = r._enum_id  # type: ignore[attr-defined]
            break
    emit(CANCELLED=0, CHOICE=chosen)
    return 0


def dialog_entry(title: str, text: str, default: str = "") -> int:
    win = Gtk.Window(title=title)
    win.set_default_size(420, 160)
    win.set_border_width(14)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.add(box)
    if text:
        box.pack_start(Gtk.Label(label=text, wrap=True, xalign=0), False, False, 0)
    entry = Gtk.Entry()
    entry.set_text(default)
    box.pack_start(entry, False, False, 0)
    btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_row.set_halign(Gtk.Align.END)
    box.pack_end(btn_row, False, False, 0)
    btn_cancel = Gtk.Button(label=T("取消", "Cancel"))
    btn_ok = Gtk.Button(label=T("确定", "OK"))
    btn_ok.get_style_context().add_class("suggested-action")
    btn_row.pack_start(btn_cancel, False, False, 0)
    btn_row.pack_start(btn_ok, False, False, 0)
    state = {"ok": False}

    def finish(ok: bool):
        state["ok"] = ok
        win.destroy()

    btn_cancel.connect("clicked", lambda *_: finish(False))
    btn_ok.connect("clicked", lambda *_: finish(True))
    entry.connect("activate", lambda *_: finish(True))
    win.connect("delete-event", lambda *_: (finish(False), True)[1])
    win.show_all()
    entry.grab_focus()
    Gtk.main()
    if not state["ok"]:
        emit(CANCELLED=1)
        return 1
    emit(CANCELLED=0, VALUE=entry.get_text())
    return 0


def dialog_question(title: str, text: str) -> int:
    dlg = Gtk.MessageDialog(
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.YES_NO,
        text=title,
        secondary_text=text,
    )
    resp = dlg.run()
    dlg.destroy()
    return 0 if resp == Gtk.ResponseType.YES else 1


def dialog_info(title: str, text: str) -> int:
    dlg = Gtk.MessageDialog(
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=title,
        secondary_text=text,
    )
    dlg.run()
    dlg.destroy()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: enum-gui-ask.py <mode> ...", file=sys.stderr)
        return 2
    mode = sys.argv[1]
    if mode == "install":
        app = sys.argv[2] if len(sys.argv) > 2 else "App"
        ask_as = sys.argv[3] == "1" if len(sys.argv) > 3 else False
        ask_upd = sys.argv[4] == "1" if len(sys.argv) > 4 else False
        return dialog_install(app, ask_as, ask_upd)
    if mode == "uninstall":
        app = sys.argv[2] if len(sys.argv) > 2 else "App"
        keep = sys.argv[3] == "1" if len(sys.argv) > 3 else True
        return dialog_uninstall(app, keep)
    if mode == "setup":
        apps = sys.argv[2:] or ["Vokey", "FlipScan", "DeskCTL", "BeeBEEP", "DigitalClock4"]
        return dialog_setup(apps)
    if mode == "list":
        title = sys.argv[2] if len(sys.argv) > 2 else "Choose"
        text = sys.argv[3] if len(sys.argv) > 3 else ""
        return dialog_list(title, text, sys.argv[4:])
    if mode == "entry":
        title = sys.argv[2] if len(sys.argv) > 2 else "Input"
        text = sys.argv[3] if len(sys.argv) > 3 else ""
        default = sys.argv[4] if len(sys.argv) > 4 else ""
        return dialog_entry(title, text, default)
    if mode == "question":
        return dialog_question(sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3] if len(sys.argv) > 3 else "")
    if mode == "info":
        return dialog_info(sys.argv[2] if len(sys.argv) > 2 else "", sys.argv[3] if len(sys.argv) > 3 else "")
    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    # Avoid Gtk complaining when no display; caller should check first.
    GLib.set_prgname("enum-setup")
    sys.exit(main())

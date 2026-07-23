<div align="center">

# Flipscan

**Flip a page, get a scan — auto-capture for document cameras**

**English** · [中文](README.zh-CN.md)

</div>

<div align="center">

<img src="docs/control-panel.png" width="320" alt="Flipscan control panel">

</div>

---

Point an overhead document camera (or any webcam) at a book or a stack of papers. Flipscan watches the video, detects when you turn a page, waits until the image is steady, and takes the shot automatically — so you can scan a whole book without ever touching the shutter. Optional scan enhancement can clean shots into readable pages (white background, black text, red stamps preserved); it is **off by default**.

### Features

- **Page-flip auto capture** — a frame-difference state machine (Idle → Watching → Page turning → Settling → Capturing) detects the flip, then fires once the picture holds still.
- **Scan enhancement (optional)** — per-channel illumination normalisation, background whitening, contrast boost and sharpening. Red seals/stamps survive. Default **off**; turn on from the control panel.
- **Perspective crop + auto-rotate** — finds the paper's four corners, flattens it, and turns landscape shots upright. Crop and scan enhancement are independent toggles; auto-rotate is a sub-option of crop and only applies when crop is on.
- **Session gallery** — thumbnails of what you just shot plus earlier sessions, with click-to-zoom, multi-select delete and delete-all.
- **Bilingual UI** — follows your system locale (Chinese if `LANG` contains `zh`/`cn`, otherwise English).
- **Switch cameras without restarting** — pick another camera under **Settings → Capture**, and the live view moves over instantly; tick *Remember this camera* and it is used automatically next time.
- **Settings popup** — Capture (camera, start-on-open, shutter delay), Detection (flip-tuning knobs), and Other (updates). Opens as one reusable popup; closed it takes no space in the capture window.
- **Camera hand-off** — if another app is holding the camera you can take it over and give it back; Eloam vendor software is paused and restored automatically.
- **Optional AI analysis** — `analyze_papers.py` sends pages (images, PDF or video) to Gemini and prints the analysis.

### Quick install (no sudo)

```bash
bash install.sh
```

Run in a terminal, the installer walks you through a short **interactive setup**: install location (user directory / custom path / system-wide `/opt`, the latter via `sudo`), whether to put a desktop icon, and — if cameras are detected — which one to pin as the default (written to `~/.config/flipscan/config.json`; you can change it anytime from the in-app *Change* button). Press Enter everywhere for the defaults.

It builds a virtualenv (`gemini_env/`) with OpenCV, and puts a ready-to-run icon on your **desktop** and in your **application menu**. A default user install needs no admin password. When run non-interactively (piped, or with any flag) it behaves exactly like before — one-shot, no questions.

Note: a plain user install runs the program **in place**, from the folder you cloned into — don't delete or move it afterwards, or the icons will break. Use `--prefix` if you want the files copied somewhere permanent.

<details>
<summary>Advanced options</summary>

```bash
bash install.sh --system            # system-wide (needs admin): /opt + /usr/share
bash install.sh --prefix ~/apps     # copy the program to ~/apps/flipscan, then make icons
bash install.sh --no-desktop-icon   # menu entry only
bash install.sh --uninstall         # remove icons / menu entry / autostart
bash install.sh --help              # usage
```

`--uninstall` (GUI confirmation when a display is available) only removes what the matching mode installed, so to undo a system install use
`bash install.sh --system --uninstall`. When `--prefix` or `--system` is given alongside
`--uninstall`, the copied program directory is deleted too.
</details>

### Requirements

Python 3.10+, OpenCV and Tk. `install.sh` pip-installs `opencv-python` and `google-generativeai` into the venv for you; Tk and v4l-utils come from your distro:

| Distro | Extra system packages |
|---|---|
| Debian/Ubuntu | `sudo apt install python3-tk v4l-utils` |
| Fedora | `sudo dnf install python3-tkinter v4l-utils` |
| Arch | `sudo pacman -S tk v4l-utils` |

### Usage

The desktop icon runs `run.sh --capture`, which opens the capture window. From a terminal:

```bash
./run.sh --capture                       # auto-detects / asks which camera
./run.sh --capture --camera 1            # pick by index
./run.sh --capture --camera /dev/video2  # or by device path

./run.sh --images page1.jpg page2.jpg    # AI-analyse existing images or PDFs
./run.sh --video recording.mp4           # AI-analyse a video
```

`--camera` is the only flag the capture window takes; everything else is done in the UI. Without `--capture`, `run.sh` passes its arguments straight to `analyze_papers.py`.

**Which camera gets used**, in order of priority:

1. `--camera` on the command line — always wins, and never changes what is remembered.
2. The remembered camera, if you ticked *Remember this camera* and the device is still there.
3. Otherwise the usual auto-detection: a device whose name looks like a document camera wins; if there is only one camera it is used directly; if there are several, a dialog lists them all so you can pick.

Built-in laptop webcams are usually `/dev/video0`; an external document camera is typically a higher number.

**Changing camera while the program is running:** the control panel shows the current camera right under the save folder, with its own **Change** button. It lists every capture device it can find; pick one and the live view switches over immediately — no restart, and nothing you have already captured is lost. If the device turns out to be busy or unplugged, FlipScan keeps the camera you were using and says so in the preview.

**Default save folder:** the sidebar **Change** button and **Settings → Capture & camera → Default save folder** write the same `output_dir` into `~/.config/flipscan/config.json`, so the next launch opens that folder. The settings window itself opens **centred on the main window**.

**Remembering a camera:** tick **Remember this camera** in the control panel (or *Remember my choice* in the startup dialog). The choice is written straight away to:

```
~/.config/flipscan/config.json        # or $XDG_CONFIG_HOME/flipscan/config.json
```

```json
{
  "camera": "/dev/video2",
  "remember_camera": true
}
```

Untick the box to go back to auto-detection. Deleting the file resets everything; if the file is unreadable or malformed, Flipscan ignores it and falls back to auto-detection rather than refusing to start.

**If the remembered camera is gone** — unplugged, or renumbered by the kernel after a reboot — Flipscan does not fail. It shows the camera-picker dialog at startup with a note saying the remembered device could not be found, and whatever you pick there becomes the new remembered camera (if the tick box is on).

**In the window:**

| Control | What it does |
|---|---|
| **▶ Start Recording** | Arms auto-capture. Press again to stop. |
| **⊙ Capture** | Takes one shot immediately, whether or not recording is on. |
| **Change** (top row) | Picks the save folder (a built-in browser with bookmarks and *New Folder*). |
| **Change** (camera row) | Switches to another camera while running; the live view follows immediately. |
| **Remember this camera** | Pins the current camera so the next launch uses it without asking. |
| **Scan Enhance (keep seals)** | White background / black text, red stamps kept. Off by default. |
| **Auto Crop (detect paper)** | Detects the paper's corners and flattens it. |
| **Rotate to portrait** | Turns landscape captures upright. Sub-option of Auto Crop — it is hidden and has no effect while crop is off. |
| Scroll / drag / double-click on the preview | Zoom, pan, reset zoom. |
| **Delete selected** / **Delete all** | Clean up the gallery (asks first). |

Captures are saved to `拍照结果/` on a Chinese system and `Captures/` otherwise, both next to the script, as `试卷_<timestamp>.jpg` / `scan_<timestamp>.jpg`.

### Tuning the flip detection

Prefer **Settings → Detection** in the app (values persist under `~/.config/flipscan/`). Defaults match the constants in `flipscan.py` if you edit the source instead:

| Setting / constant | Default | Meaning |
|---|---|---|
| `MOTION_THRESHOLD` | `300` | Motion level above which the frame counts as "moving". |
| `MIN_FLIP_MOTION` | `8000` | Peak motion a gesture must reach to be treated as a page flip. |
| `MIN_FLIP_SECONDS` | `0.3` | Shortest movement still accepted as a flip. |
| `STABLE_SECONDS` | `0.8` | How long the picture must hold still before the shutter fires. |
| `CAPTURE_DELAY` | (see Settings) | Extra wait after settle before the shutter (Settings → Capture). |
| `DIFF_THRESH` | `35` | Per-pixel difference threshold. |
| `MOTION_SMOOTH` | `6` | Frames of smoothing on the motion signal. |
| `DETECT_RES` | `(480, 360)` | Resolution the detector runs at (preview/capture stay full-res). |

Raise `MIN_FLIP_MOTION` if stray hand movement triggers shots; lower it if real flips are missed. Raise `STABLE_SECONDS` / shutter delay if pages are still wobbling when the shot is taken.

### AI analysis (optional)

Fill in `API_KEY` at the top of `analyze_papers.py` with a Gemini key from <https://aistudio.google.com> → *Get API key*. **Don't commit the file once your real key is in it.**

It uses `gemini-2.5-flash` and a built-in Chinese prompt that asks for a breakdown of an exam paper (question list, topics covered, key/difficult points, common mistakes). Edit `PROMPT` in the same file for anything else. `--images` accepts `.jpg`, `.png` and `.pdf`; `--video` uploads the file and waits for Gemini to finish processing it.

Analysis is a separate step — the capture window does not call it.

### Updating

Flipscan updates itself in place — you never have to re-clone.

- **Automatic (default on).** A few seconds after launch it asks GitHub for the latest
  release. If a newer one exists and the install directory is writable, it **downloads and
  installs quietly** (no desktop notification). Photos and `~/.config/flipscan/` are
  preserved. Restart Flipscan to run the new files. Network/timeout/unwritable failures are
  skipped silently.
- **Turning it off.** **Settings → Other → Update automatically** (on by default). The
  installer also asks once. With it off, Flipscan only goes online for a manual check.
- **Manual.** **Settings → Other → Check for updates** checks immediately and can install
  (git `pull --ff-only` or release tarball overlay, with a backup beside the install
  directory).

If Flipscan is installed in a system directory such as `/opt`, in-place update needs write
permission; re-run `install.sh` with `sudo` instead.

### Note for Eloam users

If the vendor's software is holding the camera, Flipscan pauses it automatically and restores it afterwards. For other brands you'll be asked whether to take the camera over.

### Help wanted

Honestly, I'm not an expert — I built this to scan exam papers and tuned the flip-detection and enhancement parameters by trial and error, so they may not suit every document camera. Bug reports, feature requests, better approaches and PRs are very welcome. 🙂

---

## License

**GPL-3.0** — see [`LICENSE`](LICENSE). Copyright (c) 2026 ENum.

This program is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. It is distributed in the
hope that it will be useful, but **without any warranty**.

In short: use it, change it, share it — but if you distribute a modified version, that
version has to stay open under the GPL too.

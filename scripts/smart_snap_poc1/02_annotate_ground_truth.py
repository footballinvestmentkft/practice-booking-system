#!/usr/bin/env python3
"""
02_annotate_ground_truth.py — Smart Snap POC-1 interactive annotator (v2 UI).

Protocol (one figure, event-driven — no ginput):
  GT_R1:    Click ball centre. Model overlay HIDDEN. Confirm or retry.
  GT_R2:    Re-annotate independently. Model still HIDDEN. R1 shown faintly.
  RAW_TAP:  3 quick taps (as on phone). Each needs confirm.
  LOUPE_TAP: 3 precise taps. Model prediction now VISIBLE as reference.

Controls:
  Left click        — place candidate marker (needs Confirm to save)
  Megerősít  button — save click, advance to next mode / rep
  Újrapróbálom      — discard candidate, re-click
  Nincs labda       — mark frame no-ball (no GT coords), advance
  Scroll wheel      — zoom in/out centered on cursor
  Right-drag        — pan
  Esc / close       — pause (saved modes kept), continue on next run

Saves incrementally: ground_truth.json flushed after every confirmed mode.
On restart: resumes exactly from where each frame left off.

Usage:
    python scripts/smart_snap_poc1/02_annotate_ground_truth.py
    python scripts/smart_snap_poc1/02_annotate_ground_truth.py --frame <id>
    python scripts/smart_snap_poc1/02_annotate_ground_truth.py --reset-frame <id>
    python scripts/smart_snap_poc1/02_annotate_ground_truth.py --reset-frame <id> --frame <id>
    python scripts/smart_snap_poc1/02_annotate_ground_truth.py --no-ball <id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib
try:
    matplotlib.use("TkAgg")
except Exception:
    pass  # fall back to whatever backend is available

try:
    import matplotlib.pyplot as plt
    import matplotlib.widgets as mwidgets
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

try:
    import numpy as np
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from scripts.smart_snap_poc1.config import (
    DATASET_DIR,
    GROUND_TRUTH_PATH,
    GT_AGREEMENT_THRESHOLD_PX,
    MANIFEST_PATH,
)


# ── Persistence ───────────────────────────────────────────────────────────────

def load_gt() -> dict:
    if os.path.isfile(GROUND_TRUTH_PATH):
        with open(GROUND_TRUTH_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {"schema_version": "2.0", "poc": "smart_snap_poc1", "frames": {}}


def save_gt(gt: dict) -> None:
    with open(GROUND_TRUTH_PATH, "w", encoding="utf-8") as fh:
        json.dump(gt, fh, indent=2, default=str)


def load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _agreement_px(x1: float, y1: float, x2: float, y2: float,
                  img_w: int, img_h: int) -> float:
    dx = (x1 - x2) * img_w
    dy = (y1 - y2) * img_h
    return (dx ** 2 + dy ** 2) ** 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Session report ────────────────────────────────────────────────────────────

def _session_report(gt: dict, manifest: dict) -> None:
    all_modes = set(["GT_R1", "GT_R2", "RAW_TAP", "LOUPE_TAP"])
    total_positive = sum(1 for f in manifest["frames"] if not f.get("is_no_ball"))
    no_ball_marked = sum(
        1 for e in gt["frames"].values()
        if e.get("is_no_ball") and e.get("gt_provenance") in ("human_no_ball", "marked_no_ball")
    )
    full_human = sum(
        1 for e in gt["frames"].values()
        if set(e.get("completed_modes", [])) >= all_modes
        and not e.get("is_no_ball")
        and e.get("gt_provenance", "").startswith("human")
    )
    partial_human = sum(
        1 for e in gt["frames"].values()
        if e.get("completed_modes")
        and not set(e.get("completed_modes", [])) >= all_modes
        and e.get("gt_round_1") is not None
    )
    prov_sim_only = sum(
        1 for e in gt["frames"].values()
        if e.get("gt_provenance") == "provisional_simulation"
        and not e.get("completed_modes")
    )
    print()
    print("── Előző session mentett adatai ───────────────")
    print(f"  Teljesen annotált (valódi humán): {full_human + no_ball_marked}")
    print(f"    ebből no-ball (manuálisan):     {no_ball_marked}")
    print(f"  Részleges (GT_R1 megvan, de nem kész): {partial_human}")
    print(f"  Csak provisional_simulation (nem kezdett): {prov_sim_only}")
    print(f"  Annotálandó pozitív frame összesen: {total_positive}")
    print("───────────────────────────────────────────────")
    print()


# ── AnnotationSession (event-driven, no ginput) ───────────────────────────────

class AnnotationSession:
    MODES = ["GT_R1", "GT_R2", "RAW_TAP", "LOUPE_TAP"]
    REPS  = {"GT_R1": 1, "GT_R2": 1, "RAW_TAP": 3, "LOUPE_TAP": 3}

    _LABELS = {
        "GT_R1":     "GT Round 1 — kattints a labda közepére  [modell REJTVE]",
        "GT_R2":     "GT Round 2 — annotáld újra függetlenül  [modell REJTVE]",
        "RAW_TAP":   "Raw Tap — gyors kattintás, mint telfonon  (3 ismétlés)",
        "LOUPE_TAP": "Loupe Tap — precíz kattintás  (3 ismétlés)  [modell látható]",
    }
    _COLORS = {
        "GT_R1":     "#00FF44",
        "GT_R2":     "#00CCFF",
        "RAW_TAP":   "#FF9900",
        "LOUPE_TAP": "#FF44FF",
    }
    _KEYS = {
        "GT_R1":     "gt_round_1",
        "GT_R2":     "gt_round_2",
        "RAW_TAP":   "human_raw_tap",
        "LOUPE_TAP": "human_loupe_tap",
    }

    def __init__(self, frame_id: str, img_path: str, img_w: int, img_h: int,
                 existing: Optional[dict] = None,
                 model_x: Optional[float] = None,
                 model_y: Optional[float] = None,
                 db_corrected_x: Optional[float] = None,
                 db_corrected_y: Optional[float] = None,
                 frame_idx: int = 0, total_frames: int = 1) -> None:
        self.frame_id       = frame_id
        self.img_path       = img_path
        self.img_w          = img_w
        self.img_h          = img_h
        self.model_x        = model_x
        self.model_y        = model_y
        self.db_corrected_x = db_corrected_x
        self.db_corrected_y = db_corrected_y
        self.frame_idx      = frame_idx
        self.total_frames   = total_frames

        self.result          = dict(existing) if existing else {}
        self.completed_modes = list(self.result.get("completed_modes", []))
        self.pending         = [m for m in self.MODES if m not in self.completed_modes]

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, save_callback: Callable[[dict], None]) -> dict:
        """
        Open an interactive figure for this frame.
        save_callback is called with the updated result dict after every
        confirmed mode (incremental, immediate flush).
        Returns the final result dict.
        """
        if not self.pending:
            print(f"  [{self.frame_id}] Teljesen kész, kihagyva.")
            return self.result

        img = np.array(PILImage.open(self.img_path))

        # ── Figure ────────────────────────────────────────────────────────────
        fig = plt.figure(figsize=(13, 9), facecolor="#111122")
        try:
            fig.canvas.manager.set_window_title(f"GT Annotátor — {self.frame_id}")
        except Exception:
            pass

        # Image ax (leaves bottom 14% for buttons, top 10% for title)
        ax = fig.add_axes([0.01, 0.15, 0.98, 0.84])
        ax.set_facecolor("#0a0a0a")
        ax.imshow(img, extent=[0, self.img_w, self.img_h, 0], aspect="auto")
        ax.set_xlim(0, self.img_w)
        ax.set_ylim(self.img_h, 0)
        ax.set_axis_off()

        # Text overlays
        title_txt = fig.text(0.01, 0.995, "", fontsize=10, color="white",
                             ha="left", va="top", weight="bold",
                             transform=fig.transFigure)
        prog_txt  = fig.text(0.99, 0.995, "", fontsize=9, color="#999999",
                             ha="right", va="top", transform=fig.transFigure)
        hint_txt  = fig.text(0.01, 0.135, "", fontsize=8, color="#555566",
                             ha="left", va="top", transform=fig.transFigure)

        # ── Buttons ───────────────────────────────────────────────────────────
        # [Nincs labda]          [Megerősít]  [Újrapróbálom]
        ax_nb  = fig.add_axes([0.01, 0.02, 0.17, 0.09])
        ax_ok  = fig.add_axes([0.41, 0.02, 0.18, 0.09])
        ax_ret = fig.add_axes([0.61, 0.02, 0.18, 0.09])

        btn_noball  = mwidgets.Button(ax_nb,  "🚫  Nincs labda",
                                      color="#3a0000", hovercolor="#770000")
        btn_confirm = mwidgets.Button(ax_ok,  "✓  Megerősít",
                                      color="#0a200a", hovercolor="#1a5a1a")
        btn_retry   = mwidgets.Button(ax_ret, "↩  Újrapróbálom",
                                      color="#1a1a22", hovercolor="#333344")

        for btn, lbl_color in [(btn_noball, "#ff8888"),
                               (btn_confirm, "#668866"),
                               (btn_retry,   "#666677")]:
            btn.label.set_fontsize(10)
            btn.label.set_color(lbl_color)

        # ── Mutable session state ─────────────────────────────────────────────
        S = {
            "mode_idx":      0,
            "pend_x":        None,   # normalised [0,1]
            "pend_y":        None,
            "pend_marker":   None,   # matplotlib artist
            "reps":          [],     # accumulated reps for RAW_TAP / LOUPE_TAP
            "rep_markers":   [],
            "done":          False,
            "is_no_ball":    False,
            # pan
            "pan":           False,
            "pan_x0":        None,
            "pan_y0":        None,
            "pan_xlim0":     None,
            "pan_ylim0":     None,
        }

        # Persistent overlay artists (created once, toggled visible)
        _model_artists: list = []
        _r1_artist: list     = [None]   # list so closure can rebind

        if self.model_x is not None:
            mx = self.model_x * self.img_w
            my = self.model_y * self.img_h
            mc, = ax.plot(mx, my, "o", color="yellow", ms=14, mew=2,
                          markerfacecolor="none", visible=False, zorder=5)
            ml  = ax.text(mx + 16, my - 16, "model", fontsize=7,
                          color="yellow", visible=False, zorder=5)
            _model_artists.extend([mc, ml])

        def _set_model_visible(v: bool) -> None:
            for a in _model_artists:
                a.set_visible(v)

        # ── Helpers ───────────────────────────────────────────────────────────

        def _current_mode() -> Optional[str]:
            i = S["mode_idx"]
            return self.pending[i] if i < len(self.pending) else None

        def _reps_needed() -> int:
            m = _current_mode()
            return self.REPS.get(m, 1) if m else 0

        def _set_confirm_active(active: bool) -> None:
            c = "white" if active else "#555566"
            btn_confirm.label.set_color(c)
            btn_retry.label.set_color(c)

        def _clear_pending() -> None:
            if S["pend_marker"] is not None:
                try:
                    S["pend_marker"].remove()
                except Exception:
                    pass
                S["pend_marker"] = None
            S["pend_x"] = S["pend_y"] = None
            _set_confirm_active(False)

        def _update_ui() -> None:
            mode = _current_mode()
            if mode is None:
                title_txt.set_text(f"✓  {self.frame_id} — kész, következő frame...")
                title_txt.set_color("#00ff88")
                hint_txt.set_text("")
                fig.canvas.draw_idle()
                return
            color  = self._COLORS[mode]
            label  = self._LABELS[mode]
            n_reps = _reps_needed()
            rep_s  = (f"  [ismétlés {len(S['reps'])+1}/{n_reps}]"
                      if n_reps > 1 else "")
            title_txt.set_text(f"{label}{rep_s}")
            title_txt.set_color(color)
            prog_txt.set_text(
                f"Frame {self.frame_idx+1}/{self.total_frames}  ·  "
                f"kör {S['mode_idx']+1}/{len(self.pending)}"
            )
            hint_txt.set_text(
                "Scroll: zoom  ·  Jobb egér + húz: pásztáz  ·  "
                "Esc/ablak bezárás = szünet (mentett adatok megmaradnak)"
            )
            fig.canvas.draw_idle()

        def _save_mode(mode: str, nx: float, ny: float) -> None:
            """Persist one confirmed mode. Calls save_callback immediately."""
            key = self._KEYS[mode]
            ts  = _now()

            if _reps_needed() == 1:
                self.result[key] = {"x": nx, "y": ny,
                                    "annotated_at": ts, "data_source": "human"}
                self.completed_modes.append(mode)
                self.result["completed_modes"] = list(self.completed_modes)
                self.result["gt_provenance"]   = "human_annotated"

                if mode == "GT_R2" and "gt_round_1" in self.result:
                    r1    = self.result["gt_round_1"]
                    r2    = self.result["gt_round_2"]
                    agree = _agreement_px(r1["x"], r1["y"], r2["x"], r2["y"],
                                         self.img_w, self.img_h)
                    self.result["gt_agreement_px"]   = round(agree, 2)
                    self.result["gt_review_required"] = agree > GT_AGREEMENT_THRESHOLD_PX
                    self.result["gt_final"] = {
                        "x": (r1["x"] + r2["x"]) / 2,
                        "y": (r1["y"] + r2["y"]) / 2,
                    }
                    if self.result["gt_review_required"]:
                        print(f"  ⚠ {self.frame_id}: egyezés {agree:.1f}px "
                              f"> {GT_AGREEMENT_THRESHOLD_PX}px → R3 REVIEW FLAG")
            else:
                reps = list(S["reps"])
                self.result[key] = {
                    "reps":   reps,
                    "mean_x": sum(r["x"] for r in reps) / len(reps),
                    "mean_y": sum(r["y"] for r in reps) / len(reps),
                    "data_source": "human",
                    "annotated_at": ts,
                }
                self.completed_modes.append(mode)
                self.result["completed_modes"] = list(self.completed_modes)

            # Incremental flush
            save_callback(dict(self.result))

        def _advance() -> None:
            """Move to the next mode (or finish)."""
            # Clear rep state
            S["reps"]       = []
            for a in S["rep_markers"]:
                try: a.remove()
                except Exception: pass
            S["rep_markers"] = []
            S["mode_idx"]   += 1

            if S["mode_idx"] >= len(self.pending):
                S["done"] = True
                _update_ui()
                return

            new_mode = _current_mode()
            _set_model_visible(new_mode not in ("GT_R1", "GT_R2"))

            # Show GT_R1 faint reference when entering GT_R2
            if new_mode == "GT_R2" and "gt_round_1" in self.result:
                r1 = self.result["gt_round_1"]
                if _r1_artist[0] is not None:
                    try: _r1_artist[0].remove()
                    except Exception: pass
                a, = ax.plot(r1["x"] * self.img_w, r1["y"] * self.img_h,
                             "+", color="#00FF44", ms=20, mew=2,
                             alpha=0.45, zorder=4)
                _r1_artist[0] = a

            # Reveal model + DB ref after GT_R2 finishes (before RAW_TAP)
            if new_mode == "RAW_TAP":
                if self.db_corrected_x is not None:
                    ax.plot(self.db_corrected_x * self.img_w,
                            self.db_corrected_y * self.img_h,
                            "D", color="#FFFF44", ms=9, zorder=6, label="DB ref")

            _update_ui()

        # ── Event handlers ────────────────────────────────────────────────────

        def on_click(event):
            if S["done"]: return
            if event.inaxes != ax: return
            if event.button != 1: return
            if event.xdata is None or event.ydata is None: return

            _clear_pending()
            mode  = _current_mode()
            if mode is None: return
            color = self._COLORS[mode]

            nx = max(0.0, min(1.0, event.xdata / self.img_w))
            ny = max(0.0, min(1.0, event.ydata / self.img_h))
            m, = ax.plot(event.xdata, event.ydata, "x",
                         color=color, ms=22, mew=3, zorder=10)
            S["pend_marker"] = m
            S["pend_x"], S["pend_y"] = nx, ny
            _set_confirm_active(True)
            fig.canvas.draw_idle()

        def on_confirm(_event):
            if S["pend_x"] is None: return
            mode  = _current_mode()
            if mode is None: return
            color = self._COLORS[mode]
            nx, ny = S["pend_x"], S["pend_y"]
            px, py = nx * self.img_w, ny * self.img_h

            # Promote candidate → confirmed dot
            _clear_pending()
            dot, = ax.plot(px, py, "o", color=color, ms=10, alpha=0.85, zorder=9)
            S["rep_markers"].append(dot)

            n_reps = _reps_needed()
            if n_reps > 1:
                S["reps"].append({"rep": len(S["reps"]) + 1,
                                  "x": nx, "y": ny, "annotated_at": _now()})
                if len(S["reps"]) >= n_reps:
                    _save_mode(mode, nx, ny)   # nx/ny unused for multi-rep (uses S["reps"])
                    _advance()
                else:
                    _update_ui()
                    fig.canvas.draw_idle()
            else:
                _save_mode(mode, nx, ny)
                _advance()

        def on_retry(_event):
            if S["pend_x"] is None: return
            _clear_pending()
            fig.canvas.draw_idle()

        def on_no_ball(_event):
            _clear_pending()
            self.result.update({
                "is_no_ball":       True,
                "gt_round_1":       None,
                "gt_round_2":       None,
                "gt_final":         None,
                "human_raw_tap":    None,
                "human_loupe_tap":  None,
                "gt_provenance":    "human_no_ball",
                "completed_modes":  list(self.MODES),
                "annotated_at":     _now(),
            })
            save_callback(dict(self.result))
            title_txt.set_text("✗  Nincs labda — mentve, következő frame...")
            title_txt.set_color("#ff5555")
            _set_model_visible(False)
            fig.canvas.draw_idle()
            S["is_no_ball"] = S["done"] = True

        def on_scroll(event):
            if event.inaxes != ax: return
            factor = 0.72 if event.button == "up" else 1.38
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            cx = event.xdata if event.xdata is not None else (xlim[0] + xlim[1]) / 2
            cy = event.ydata if event.ydata is not None else (ylim[0] + ylim[1]) / 2
            ax.set_xlim([cx + (x - cx) * factor for x in xlim])
            ax.set_ylim([cy + (y - cy) * factor for y in ylim])
            fig.canvas.draw_idle()

        def on_press(event):
            if event.button == 3 and event.inaxes == ax:
                S.update(pan=True, pan_x0=event.x, pan_y0=event.y,
                         pan_xlim0=ax.get_xlim(), pan_ylim0=ax.get_ylim())

        def on_release(event):
            if event.button == 3:
                S["pan"] = False

        def on_motion(event):
            if not S["pan"]: return
            if event.x is None: return
            bbox = ax.get_window_extent()
            if bbox.width == 0 or bbox.height == 0: return
            x0l, x1l = S["pan_xlim0"]
            y0l, y1l = S["pan_ylim0"]
            dx = -(event.x - S["pan_x0"]) * (x1l - x0l) / bbox.width
            dy =  (event.y - S["pan_y0"]) * (y1l - y0l) / bbox.height
            ax.set_xlim(x0l + dx, x1l + dx)
            ax.set_ylim(y0l + dy, y1l + dy)
            fig.canvas.draw_idle()

        # ── Wire up ───────────────────────────────────────────────────────────
        btn_confirm.on_clicked(on_confirm)
        btn_retry.on_clicked(on_retry)
        btn_noball.on_clicked(on_no_ball)
        fig.canvas.mpl_connect("button_press_event",   on_click)
        fig.canvas.mpl_connect("button_press_event",   on_press)
        fig.canvas.mpl_connect("button_release_event", on_release)
        fig.canvas.mpl_connect("motion_notify_event",  on_motion)
        fig.canvas.mpl_connect("scroll_event",         on_scroll)

        # Initial state: show model only if first pending mode needs it
        first_mode = _current_mode()
        _set_model_visible(first_mode not in ("GT_R1", "GT_R2"))

        # If resuming at GT_R2, show the saved R1 reference
        if first_mode == "GT_R2" and "gt_round_1" in self.result:
            r1 = self.result["gt_round_1"]
            a, = ax.plot(r1["x"] * self.img_w, r1["y"] * self.img_h,
                         "+", color="#00FF44", ms=20, mew=2, alpha=0.45, zorder=4)
            _r1_artist[0] = a

        _update_ui()
        plt.show(block=False)
        while not S["done"]:
            try:
                plt.pause(0.05)
            except KeyboardInterrupt:
                break
            if not plt.fignum_exists(fig.number):
                break
        plt.close(fig)
        return dict(self.result)


# ── Seed from DB (optional shortcut for Type A frames) ────────────────────────

def seed_from_db(manifest: dict, gt: dict) -> int:
    seeded = 0
    for frame in manifest["frames"]:
        fid = frame["frame_id"]
        if frame["type"] != "A":
            continue
        if frame.get("db_corrected_x") is None:
            continue
        entry = gt["frames"].get(fid, {})
        if "gt_round_1" in entry:
            continue  # already has R1
        ts = _now()
        entry = gt["frames"].setdefault(fid, {})
        entry["gt_round_1"]       = {"x": frame["db_corrected_x"],
                                     "y": frame["db_corrected_y"],
                                     "annotated_at": ts, "data_source": "db_seeded"}
        entry["gt_provenance"]    = "seeded_from_db_round1"
        entry["completed_modes"]  = list(entry.get("completed_modes", [])) + ["GT_R1"]
        seeded += 1
    save_gt(gt)
    return seeded


# ── Reset single frame ────────────────────────────────────────────────────────

def reset_frame(frame_id: str, gt: dict) -> None:
    if frame_id not in gt["frames"]:
        print(f"  {frame_id}: not in ground_truth.json, nothing to reset.")
        return
    entry = gt["frames"][frame_id]
    for key in ("completed_modes", "gt_round_1", "gt_round_2", "gt_final",
                "gt_agreement_px", "gt_review_required", "gt_provenance",
                "human_raw_tap", "human_loupe_tap", "is_no_ball"):
        entry.pop(key, None)
    save_gt(gt)
    print(f"  Reset {frame_id}: annotáció törölve, újraannotálható.")


# ── Main ──────────────────────────────────────────────────────────────────────

def _img_size_from_file(img_path: str) -> tuple[int, int]:
    """Read actual JPEG dimensions when manifest has None."""
    try:
        with PILImage.open(img_path) as im:
            return im.width, im.height
    except Exception:
        return 640, 480


def run(target_frame: Optional[str] = None,
        seed_db: bool = False,
        no_ball_id: Optional[str] = None,
        reset_frame_id: Optional[str] = None,
        reannotate_pending: bool = False) -> None:

    if not (HAS_MATPLOTLIB and HAS_PIL):
        print("ERROR: matplotlib és Pillow szükséges.\n"
              "  pip install matplotlib Pillow", file=sys.stderr)
        sys.exit(1)

    manifest = load_manifest()
    gt       = load_gt()

    if reset_frame_id:
        reset_frame(reset_frame_id, gt)
        if not target_frame:
            return

    if seed_db:
        n = seed_from_db(manifest, gt)
        print(f"Seeded {n} Type A frame GT_R1 from DB.")

    if no_ball_id:
        gt["frames"][no_ball_id] = {
            "is_no_ball": True,
            "gt_round_1": None, "gt_round_2": None, "gt_final": None,
            "human_raw_tap": None, "human_loupe_tap": None,
            "gt_provenance": "marked_no_ball",
            "completed_modes": list(AnnotationSession.MODES),
            "annotated_at": _now(),
        }
        save_gt(gt)
        print(f"Marked {no_ball_id} as no-ball.")
        return

    # Previous session report
    _session_report(gt, manifest)

    all_modes = set(AnnotationSession.MODES)

    if reannotate_pending:
        # Re-annotation queue: GT entries flagged annotation_needs_redo=True
        requeue_ids = sorted(
            fid for fid, e in gt["frames"].items()
            if e.get("annotation_needs_redo")
        )
        if not requeue_ids:
            print("Nincs újraannotálandó frame (annotation_needs_redo=True).")
            return
        manifest_map = {f["frame_id"]: f for f in manifest["frames"]}
        # Build synthetic frame dicts; fill image size from file if manifest has None
        pending = []
        for fid in requeue_ids:
            mf = dict(manifest_map.get(fid, {"frame_id": fid}))
            img_path = os.path.join(DATASET_DIR, f"{fid}.jpg")
            if mf.get("image_width_px") is None or mf.get("image_height_px") is None:
                w, h = _img_size_from_file(img_path)
                mf["image_width_px"]  = w
                mf["image_height_px"] = h
            # Force positive (overrides manifest is_no_ball for re-annotation)
            mf["is_no_ball"] = False
            pending.append(mf)
        print(f"Re-annotálandó (annotation_needs_redo): {len(pending)} frame")
    else:
        frames = manifest["frames"]
        if target_frame:
            frames = [f for f in frames if f["frame_id"] == target_frame]
            if not frames:
                print(f"ERROR: '{target_frame}' not in manifest.", file=sys.stderr)
                sys.exit(1)

        # Only positive frames need interactive annotation
        pending = [
            f for f in frames
            if not f.get("is_no_ball")
            and not set(gt["frames"].get(f["frame_id"], {}).get("completed_modes", [])) >= all_modes
        ]

    if not pending:
        print("Minden frame teljesen annotált.")
        return

    print(f"Annotálandó: {len(pending)} frame")
    print("Az első frame ablak most nyílik meg...")

    for idx, frame in enumerate(pending):
        fid      = frame["frame_id"]
        img_path = os.path.join(DATASET_DIR, f"{fid}.jpg")
        if not os.path.isfile(img_path):
            print(f"  SKIP {fid}: nincs JPEG (futtasd: 01_extract_frames.py)")
            continue

        existing = gt["frames"].get(fid, {})

        def _make_cb(frame_id: str):
            def _cb(result: dict) -> None:
                gt["frames"][frame_id] = result
                save_gt(gt)
                modes = result.get("completed_modes", [])
                print(f"  ✓ MENTVE {frame_id}: {modes}")
            return _cb

        session = AnnotationSession(
            frame_id        = fid,
            img_path        = img_path,
            img_w           = frame.get("image_width_px") or 640,
            img_h           = frame.get("image_height_px") or 480,
            existing        = existing,
            model_x         = frame.get("model_x"),
            model_y         = frame.get("model_y"),
            db_corrected_x  = frame.get("db_corrected_x"),
            db_corrected_y  = frame.get("db_corrected_y"),
            frame_idx       = idx,
            total_frames    = len(pending),
        )
        result = session.run(save_callback=_make_cb(fid))
        # Final flush (captures any state set after last mode)
        gt["frames"][fid] = result
        save_gt(gt)

    # Summary
    total = len(manifest["frames"])
    done  = sum(1 for e in gt["frames"].values()
                if set(e.get("completed_modes", [])) >= all_modes)
    human = sum(1 for e in gt["frames"].values()
                if set(e.get("completed_modes", [])) >= all_modes
                and e.get("gt_provenance", "").startswith("human"))
    print()
    print("── Befejezés ──────────────────────────────────")
    print(f"  Teljesen kész összesen: {done}/{total}")
    print(f"  Ebből valódi humán:     {human}")
    print("───────────────────────────────────────────────")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GT annotátor v2 — event-driven UI.")
    parser.add_argument("--frame", metavar="FRAME_ID",
                        help="Csak egy frame annotálása.")
    parser.add_argument("--reset-frame", metavar="FRAME_ID",
                        help="Frame annotációjának törlése (újrakezdhető). "
                             "Kombinálható --frame-mel az azonnali újraannotáláshoz.")
    parser.add_argument("--seed-from-db", action="store_true",
                        help="GT_R1 feltöltése DB corrected_x/y-ból (Type A frame-ek).")
    parser.add_argument("--no-ball", metavar="FRAME_ID",
                        help="Frame jelölése no-ball-ként kattintás nélkül.")
    parser.add_argument("--reannotate-pending", action="store_true",
                        help="Csak a GT annotation_needs_redo=True frame-eket mutatja. "
                             "Manifest is_no_ball flag figyelmen kívül hagyva ezekre.")
    args = parser.parse_args()
    run(
        target_frame        = args.frame,
        seed_db             = args.seed_from_db,
        no_ball_id          = args.no_ball,
        reset_frame_id      = args.reset_frame,
        reannotate_pending  = args.reannotate_pending,
    )

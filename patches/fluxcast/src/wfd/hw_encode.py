"""Hardware H.264 encoder selection for WFD desktop capture.

Prefers VAAPI (then QSV) over libx264 when the device can encode. Power
policy still prefers GPU — it is cheaper than software x264 — but on battery
or the power-saver profile we bias toward lower bitrate / less GPU parallelism.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EncodePlan:
    name: str  # vaapi | qsv | libx264
    pre_input: list[str]
    vf: list[str]
    video_args: list[str]
    note: str


def _ffmpeg_has_encoder(name: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    blob = (result.stdout or "") + (result.stderr or "")
    # Match encoder table rows like "V....D h264_vaapi"
    return f" {name} " in f" {blob} " or f"\n{name} " in blob or f" {name}\n" in blob


def _vaapi_device() -> str:
    override = os.environ.get("FLUXCAST_WFD_VAAPI_DEVICE", "").strip()
    if override:
        return override
    for path in ("/dev/dri/renderD128", "/dev/dri/renderD129"):
        if os.path.exists(path):
            return path
    return "/dev/dri/renderD128"


def _on_mains_power() -> bool:
    """True when AC is online or no battery is present."""
    supply = "/sys/class/power_supply"
    try:
        names = os.listdir(supply)
    except OSError:
        return True
    saw_battery = False
    for name in names:
        base = os.path.join(supply, name)
        type_path = os.path.join(base, "type")
        try:
            kind = open(type_path, encoding="utf-8").read().strip().lower()
        except OSError:
            continue
        if kind == "mains":
            try:
                online = open(os.path.join(base, "online"), encoding="utf-8").read().strip()
            except OSError:
                continue
            if online == "1":
                return True
        elif kind == "battery":
            saw_battery = True
            try:
                status = open(os.path.join(base, "status"), encoding="utf-8").read().strip().lower()
            except OSError:
                continue
            if status == "charging" or status == "full":
                return True
    return not saw_battery


def _power_profile() -> str:
    ctl = shutil.which("powerprofilesctl")
    if not ctl:
        return "unknown"
    try:
        result = subprocess.run(
            [ctl, "get"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return (result.stdout or "").strip().lower() or "unknown"


def power_bias() -> str:
    """full | efficient — efficient on battery or power-saver profile."""
    override = os.environ.get("FLUXCAST_WFD_ENCODE_BIAS", "").strip().lower()
    if override in ("full", "efficient"):
        return override
    if not _on_mains_power():
        return "efficient"
    if _power_profile() == "power-saver":
        return "efficient"
    return "full"


def _level_to_idc(level: str) -> str:
    """Convert FluxCast levels like '3.1' / '4.0' to VAAPI level_idc '31' / '40'."""
    text = (level or "3.1").strip()
    if text.isdigit():
        return text
    try:
        major, minor = text.split(".", 1)
        return f"{int(major)}{int(minor)}"
    except Exception:
        return "31"


def _map_vaapi_profile(h264_profile: str) -> str:
    value = (h264_profile or "baseline").strip().lower()
    if value in ("baseline", "constrained_baseline", "cbp"):
        return "constrained_baseline"
    if value == "main":
        return "main"
    if value == "high":
        return "high"
    return "constrained_baseline"


def _requested_encoder() -> str:
    return os.environ.get("FLUXCAST_WFD_ENCODER", "auto").strip().lower() or "auto"


def apply_bitrate_bias(bitrate_text: str, bias: str) -> str:
    """On efficient bias, trim bitrate ~25% to save radio/GPU energy."""
    if bias != "efficient":
        return bitrate_text
    text = bitrate_text.strip().lower()
    try:
        if text.endswith("m"):
            return f"{max(1.0, float(text[:-1]) * 0.75):g}M"
        if text.endswith("k"):
            return f"{max(500, int(float(text[:-1]) * 0.75))}k"
    except ValueError:
        return bitrate_text
    return bitrate_text


def probe_encoder(prefer: str = "auto") -> str:
    prefer = (prefer or "auto").strip().lower()
    if prefer in ("libx264", "x264", "software", "sw"):
        return "libx264"
    if prefer == "vaapi":
        return "vaapi" if _ffmpeg_has_encoder("h264_vaapi") else "libx264"
    if prefer == "qsv":
        return "qsv" if _ffmpeg_has_encoder("h264_qsv") else "libx264"
    # auto: VAAPI first on this stack (Iris Xe), then QSV, then software.
    if _ffmpeg_has_encoder("h264_vaapi") and os.path.exists(_vaapi_device()):
        return "vaapi"
    if _ffmpeg_has_encoder("h264_qsv"):
        return "qsv"
    return "libx264"


def build_encode_plan(
    *,
    h264_profile: str,
    level: str,
    fps: int,
    gop: int,
    bitrate: str,
    bufsize: str,
    vf_scale: Optional[str],
) -> EncodePlan:
    """Build ffmpeg argv fragments for the chosen encoder.

    vf_scale is either None (source matches output; format only) or a full
    software letterbox/scale filter string from encoding._letterbox_vf.
    """
    bias = power_bias()
    choice = probe_encoder(_requested_encoder())
    level_idc = _level_to_idc(level)

    if choice == "vaapi":
        device = _vaapi_device()
        # Upload after any CPU scale/pad so the encoder sees NV12 on the GPU.
        if vf_scale:
            vf = f"{vf_scale},format=nv12,hwupload"
        else:
            vf = "format=nv12,hwupload"
        quality = "7" if bias == "efficient" else "4"  # higher = faster/less GPU on Intel
        async_depth = "1" if bias == "efficient" else "2"
        return EncodePlan(
            name="vaapi",
            pre_input=["-vaapi_device", device],
            vf=["-vf", vf],
            video_args=[
                "-c:v", "h264_vaapi",
                "-profile:v", _map_vaapi_profile(h264_profile),
                "-level", level_idc,
                "-bf", "0",
                "-g", str(gop),
                "-keyint_min", str(gop),
                "-r", str(fps),
                "-b:v", bitrate,
                "-maxrate", bitrate,
                "-bufsize", bufsize,
                "-quality", quality,
                "-async_depth", async_depth,
            ],
            note=f"h264_vaapi on {device} ({bias} power bias)",
        )

    if choice == "qsv":
        if vf_scale:
            vf = f"{vf_scale},format=nv12,hwupload=extra_hw_frames=64"
        else:
            vf = "format=nv12,hwupload=extra_hw_frames=64"
        preset = "faster" if bias == "efficient" else "balanced"
        return EncodePlan(
            name="qsv",
            pre_input=["-init_hw_device", "qsv=hw", "-filter_hw_device", "hw"],
            vf=["-vf", vf],
            video_args=[
                "-c:v", "h264_qsv",
                "-profile:v", "baseline" if "baseline" in (h264_profile or "").lower() else h264_profile,
                "-bf", "0",
                "-g", str(gop),
                "-r", str(fps),
                "-b:v", bitrate,
                "-maxrate", bitrate,
                "-bufsize", bufsize,
                "-preset", preset,
            ],
            note=f"h264_qsv ({bias} power bias)",
        )

    # Software fallback — same shape as the historical FluxCast path.
    preset = "ultrafast" if bias == "efficient" else "veryfast"
    return EncodePlan(
        name="libx264",
        pre_input=[],
        vf=["-vf", vf_scale] if vf_scale else ["-vf", "format=yuv420p"],
        video_args=[
            "-c:v", "libx264",
            "-preset", preset,
            "-tune", "zerolatency",
            "-profile:v", h264_profile,
            "-level:v", level,
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-g", str(gop),
            "-keyint_min", str(gop),
            "-sc_threshold", "0",
            "-bf", "0",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bufsize,
            "-x264-params", "repeat-headers=1:aud=1",
        ],
        note=f"libx264 {preset} ({bias} power bias; no usable GPU encoder)",
    )

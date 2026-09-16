#!/usr/bin/env python3
"""Detect the GPU encode device and its hardware-reported name.

Used by miracast-ctl gpu-info / status for RENDER ENGINE pill subtitles:
  GPU + DMA-BUF
  (TigerLake-LP GT2 [UHD Graphics])
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

# lspci class match for display GPUs
_GPU_CLASS_RE = re.compile(
    r"vga compatible controller|3d controller|display controller", re.I
)
# Trailing PCI id / rev from lspci -nn (keep product bracket like [UHD Graphics])
_PCI_ID_TAIL_RE = re.compile(r"\s*\[[0-9a-f]{4}:[0-9a-f]{4}\](?:\s*\(rev\s+[^)]+\))?\s*$", re.I)


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") if r.returncode == 0 else ""
    except Exception:
        return ""


def _vendor_from_driver(driver: str) -> str:
    d = (driver or "").strip().lower()
    if d in ("i915", "xe", "i965"):
        return "intel"
    if d in ("amdgpu", "radeon"):
        return "amd"
    if d in ("nvidia", "nvidia_drm", "nouveau"):
        return "nvidia"
    return "unknown"


def _vendor_from_pci_id(pci_id: str) -> str:
    # PCI_ID is VENDOR:DEVICE hex, e.g. 8086:9A49
    vid = (pci_id or "").split(":", 1)[0].lower()
    if vid == "8086":
        return "intel"
    if vid in ("1002", "1022"):
        return "amd"
    if vid == "10de":
        return "nvidia"
    return "unknown"


def _lspci_device_name(slot: str) -> Optional[str]:
    """Card-reported product string for a PCI slot (from lspci)."""
    if not slot:
        return None
    raw = _run(["lspci", "-nn", "-s", slot])
    line = (raw.splitlines() or [""])[0].strip()
    if not line:
        return None
    # "00:02.0 VGA …: Intel Corporation TigerLake-LP GT2 [UHD Graphics] [8086:9a49] (rev 01)"
    if ": " in line:
        # Split on first ": " after the slot/class portion — lspci uses ": " before vendor
        # Find the last "]: " pattern before vendor… simpler: after first "]: "
        m = re.search(r"\]:\s*(.+)$", line)
        if m:
            name = m.group(1).strip()
            name = _PCI_ID_TAIL_RE.sub("", name).strip()
            return name or None
    return line


def _drm_gpus() -> list[dict[str, Any]]:
    """Enumerate DRM cards with render nodes and card-reported names."""
    out: list[dict[str, Any]] = []
    drm = Path("/sys/class/drm")
    if not drm.is_dir():
        return out
    for card in sorted(drm.glob("card[0-9]*")):
        if "-" in card.name:  # skip card0-eDP-1 connectors
            continue
        uevent = card / "device" / "uevent"
        if not uevent.is_file():
            continue
        kv: dict[str, str] = {}
        try:
            for line in uevent.read_text(errors="replace").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    kv[k.strip()] = v.strip()
        except OSError:
            continue
        driver = kv.get("DRIVER", "")
        pci_id = kv.get("PCI_ID", "")
        pci_slot = kv.get("PCI_SLOT_NAME", "")  # 0000:00:02.0
        slot_short = ""
        if pci_slot:
            # lspci wants 00:02.0 (drop domain) usually
            parts = pci_slot.split(":")
            if len(parts) >= 3:
                slot_short = f"{parts[1]}:{parts[2]}"
            else:
                slot_short = pci_slot
        vendor = _vendor_from_driver(driver)
        if vendor == "unknown":
            vendor = _vendor_from_pci_id(pci_id)
        device_name = _lspci_device_name(slot_short) if slot_short else None
        if not device_name:
            # Fallback: any matching lspci GPU line
            for ln in _run(["lspci", "-nn"]).splitlines():
                if not _GPU_CLASS_RE.search(ln):
                    continue
                if slot_short and not ln.startswith(slot_short):
                    continue
                m = re.search(r"\]:\s*(.+)$", ln.strip())
                if m:
                    device_name = _PCI_ID_TAIL_RE.sub("", m.group(1)).strip()
                break
        render = None
        # card0 → ../card0/device/drm/renderD128 or sibling renderD*
        for rd in sorted((card / "device" / "drm").glob("renderD*")) if (card / "device" / "drm").is_dir() else []:
            render = f"/dev/dri/{rd.name}"
            break
        if render is None:
            # Alternate layout: /sys/class/drm/renderD128
            for rd in sorted(Path("/sys/class/drm").glob("renderD*")):
                try:
                    if rd.resolve().parent == (card / "device" / "drm").resolve():
                        render = f"/dev/dri/{rd.name}"
                        break
                except OSError:
                    continue
        out.append(
            {
                "vendor": vendor,
                "driver": driver or None,
                "pciId": pci_id or None,
                "pciSlot": pci_slot or slot_short or None,
                "deviceName": device_name or "Unknown GPU",
                "driCard": f"/dev/dri/{card.name}",
                "driRender": render,
                "dmabufLikely": vendor in ("intel", "amd"),
            }
        )
    return out


def _ffmpeg_has_encoder(name: str) -> bool:
    out = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=8.0)
    return bool(re.search(rf"\b{re.escape(name)}\b", out))


def _vaapi_usable(render: Optional[str] = None) -> bool:
    if not _ffmpeg_has_encoder("h264_vaapi"):
        return False
    if render and Path(render).exists():
        return True
    return any(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").is_dir() else False


def _pick_primary(gpus: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Prefer env-selected render node, else first Intel/AMD/NVIDIA with a name."""
    env = (
        os.environ.get("FLUXCAST_WFD_VAAPI_DEVICE")
        or os.environ.get("FLUXCAST_VAAPI_DEVICE")
        or ""
    ).strip()
    if env:
        for g in gpus:
            if g.get("driRender") == env or g.get("driCard") == env:
                return g
    for pref in ("intel", "amd", "nvidia"):
        for g in gpus:
            if g.get("vendor") == pref:
                return g
    return gpus[0] if gpus else None


def collect() -> dict[str, Any]:
    gpus = _drm_gpus()
    primary = _pick_primary(gpus)
    vendor = (primary or {}).get("vendor") or "unknown"
    device_name = (primary or {}).get("deviceName")
    render = (primary or {}).get("driRender")
    dmabuf_likely = bool((primary or {}).get("dmabufLikely")) and _vaapi_usable(render)
    pipe: list[str] = []
    if _vaapi_usable(render):
        pipe.append("h264_vaapi")
    if vendor == "intel" and _ffmpeg_has_encoder("h264_qsv"):
        pipe.append("h264_qsv")
    # Only advertise HW encoders when the matching device/driver is present
    # (ffmpeg may list nvenc/amf from build flags without a usable GPU).
    if vendor == "nvidia" and _ffmpeg_has_encoder("h264_nvenc") and (
        Path("/dev/nvidia0").exists() or Path("/proc/driver/nvidia/version").is_file()
    ):
        pipe.append("h264_nvenc")
    if vendor == "amd" and _ffmpeg_has_encoder("h264_amf"):
        pipe.append("h264_amf")
    return {
        "ok": True,
        "vendor": vendor,
        "deviceName": device_name,
        "driRender": render,
        "pciSlot": (primary or {}).get("pciSlot"),
        "driver": (primary or {}).get("driver"),
        "dmabufLikely": dmabuf_likely,
        "pipeEncoders": pipe,
        "gpus": gpus,
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="print JSON (default)")
    args = p.parse_args(argv)
    info = collect()
    print(json.dumps(info, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

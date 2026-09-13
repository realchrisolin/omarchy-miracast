#!/usr/bin/env python3
"""Score Miracast benchmark TSVs and recommend FluxCast / plugin settings.

Reads docs/benchmarks/results.tsv (primary matrix) and optionally
lpcm_damage_ab.tsv. Picks the lowest-cost capture+encode combo that is
actually available on this machine, then prints shell exports and can
--apply them into omarchy-miracast settings.json.

Usage:
  scripts/recommend-cast-profile.py              # print recommendation
  scripts/recommend-cast-profile.py --apply      # write settings + recommended.env
  scripts/recommend-cast-profile.py --json       # machine-readable
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "docs" / "benchmarks" / "results.tsv"
DEFAULT_DAMAGE = ROOT / "docs" / "benchmarks" / "lpcm_damage_ab.tsv"
DEFAULT_ENV_OUT = ROOT / "docs" / "benchmarks" / "recommended.env"


@dataclass
class Recommendation:
    case: str
    capture: str  # icc | stock
    encode: str  # dmabuf | vaapi | cpu
    score: float
    hypr_cpu: float
    wf_cpu: float
    ffmpeg_cpu: float
    wf_recorder_bin: str
    wf_recorder_proto: str  # icc | auto
    video_encoder: str  # auto | vaapi | software
    damage: str  # "1" | "0"
    damage_rationale: str
    notes: str


def _f(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def _icc_capable(path: str) -> bool:
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return False
    try:
        import subprocess

        r = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        blob = (r.stdout or "") + (r.stderr or "")
        return "--toplevel" in blob
    except Exception:
        return False


def score_row(row: dict) -> float:
    """Lower is better. Weight compositor + encode CPU; light weight on wf-recorder."""
    return (
        _f(row, "hypr_cpu") * 1.0
        + _f(row, "wf_cpu") * 0.5
        + _f(row, "ffmpeg_cpu") * 1.0
    )


def load_tsv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def pick_damage(path: Path | None) -> tuple[str, str]:
    """Prefer damage-aware when GPU power/RCS is not worse by much."""
    if path is None or not path.is_file():
        return "1", "no lpcm_damage_ab.tsv; default DAMAGE=1 (Omarchy default)"
    rows = load_tsv(path)
    by_case = {r.get("case"): r for r in rows}
    cont = by_case.get("lpcm_continuous_D")
    dmg = by_case.get("lpcm_damage_aware")
    if not cont or not dmg:
        return "1", "incomplete damage A/B; default DAMAGE=1"
    # Prefer damage-aware if GPU power <= continuous * 1.05 (within 5%)
    # or RCS not significantly higher.
    p_c, p_d = _f(cont, "gpu_power_w", 99), _f(dmg, "gpu_power_w", 99)
    r_c, r_d = _f(cont, "rcs_pct", 99), _f(dmg, "rcs_pct", 99)
    if p_d <= p_c * 1.05 and r_d <= r_c * 1.10:
        return (
            "1",
            f"damage A/B: power {p_d:.2f}W≤{p_c:.2f}W·1.05, RCS {r_d:.1f}≤{r_c:.1f}·1.10",
        )
    return (
        "0",
        f"damage A/B favors continuous -D (power {p_d:.2f} vs {p_c:.2f}, RCS {r_d:.1f} vs {r_c:.1f})",
    )


def recommend(
    results: Path,
    damage_tsv: Path | None,
    icc_bin_hint: str,
) -> Recommendation:
    rows = load_tsv(results)
    if not rows:
        raise SystemExit(f"no rows in {results}")

    # Resolve which ICC binary to consider (settings hint or PATH).
    settings_bin = ""
    settings_path = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ) / "omarchy-miracast" / "settings.json"
    if settings_path.is_file():
        try:
            settings_bin = str(
                json.loads(settings_path.read_text()).get("wfRecorderBin") or ""
            ).strip()
        except Exception:
            pass

    candidates = []
    for p in (
        icc_bin_hint,
        settings_bin,
        str(Path.home() / "src" / "wf-recorder" / "build" / "wf-recorder"),
        shutil.which("wf-recorder") or "",
    ):
        p = os.path.expanduser(p or "")
        if p and p not in candidates:
            candidates.append(p)

    icc_bin = next((p for p in candidates if _icc_capable(p)), "")
    stock_bin = shutil.which("wf-recorder") or "/usr/bin/wf-recorder"
    have_icc = bool(icc_bin)

    scored: list[tuple[float, dict]] = []
    for row in rows:
        cap = (row.get("capture") or "").strip().lower()
        enc = (row.get("encode") or "").strip().lower()
        if cap not in ("icc", "stock") or enc not in ("dmabuf", "vaapi", "cpu"):
            continue
        if cap == "icc" and not have_icc:
            continue
        # Prefer dmabuf slightly when scores nearly tie (encode offload).
        tie_break = {"dmabuf": 0.0, "vaapi": 0.15, "cpu": 0.4}.get(enc, 0.5)
        scored.append((score_row(row) + tie_break, row))

    if not scored:
        raise SystemExit("no eligible benchmark rows (need ICC binary or stock rows)")

    scored.sort(key=lambda x: x[0])
    best_score, best = scored[0]
    capture = best["capture"].strip().lower()
    encode = best["encode"].strip().lower()

    if capture == "icc":
        wf_bin = icc_bin
        wf_proto = "icc"
    else:
        wf_bin = stock_bin
        wf_proto = "auto"

    video_encoder = {
        "dmabuf": "auto",
        "vaapi": "vaapi",
        "cpu": "software",
    }.get(encode, "auto")

    damage, damage_why = pick_damage(damage_tsv)

    notes = (
        f"lowest score={best_score:.2f} among {len(scored)} eligible rows; "
        f"icc_available={have_icc}"
    )
    return Recommendation(
        case=str(best.get("case") or ""),
        capture=capture,
        encode=encode,
        score=round(best_score, 3),
        hypr_cpu=_f(best, "hypr_cpu"),
        wf_cpu=_f(best, "wf_cpu"),
        ffmpeg_cpu=_f(best, "ffmpeg_cpu"),
        wf_recorder_bin=wf_bin,
        wf_recorder_proto=wf_proto,
        video_encoder=video_encoder,
        damage=damage,
        damage_rationale=damage_why,
        notes=notes,
    )


def format_env(rec: Recommendation) -> str:
    lines = [
        "# Generated by scripts/recommend-cast-profile.py — source before FluxCast",
        f"# winner={rec.case} score={rec.score} ({rec.notes})",
        f"# damage: {rec.damage_rationale}",
        f"export FLUXCAST_WFD_CAPTURE_ENCODE_PREF={rec.encode}",
        f"export FLUXCAST_WFD_ENCODER={rec.video_encoder}",
        f"export FLUXCAST_WFD_WF_RECORDER_DAMAGE={rec.damage}",
    ]
    if rec.wf_recorder_bin:
        lines.append(f"export FLUXCAST_WFD_WF_RECORDER_BIN={rec.wf_recorder_bin}")
    lines.append(f"export FLUXCAST_WFD_WF_RECORDER_PROTO={rec.wf_recorder_proto}")
    # Legacy alias used by older FluxCast
    legacy = {"dmabuf": "vaapi", "vaapi": "pipe", "cpu": "pipe"}.get(rec.encode, "pipe")
    lines.append(f"export FLUXCAST_WFD_CAPTURE_ENCODE={legacy}")
    lines.append("")
    return "\n".join(lines)


def apply_settings(rec: Recommendation) -> Path:
    cfg_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = cfg_dir / "omarchy-miracast" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}
    data["captureEncode"] = rec.encode
    data["videoEncoder"] = rec.video_encoder
    data["wfRecorderBin"] = rec.wf_recorder_bin
    data["wfRecorderProto"] = rec.wf_recorder_proto
    data["wfRecorderDamage"] = rec.damage
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def apply_state_env(rec: Recommendation) -> Path:
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = state / "omarchy-miracast" / "recommended-cast.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_env(rec))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--results",
        type=Path,
        default=DEFAULT_RESULTS,
        help="primary results.tsv",
    )
    ap.add_argument(
        "--damage-tsv",
        type=Path,
        default=DEFAULT_DAMAGE,
        help="optional LPCM cadence A/B tsv",
    )
    ap.add_argument(
        "--icc-bin",
        default="",
        help="preferred ICC wf-recorder path (else settings / ~/src/... / PATH)",
    )
    ap.add_argument(
        "--env-out",
        type=Path,
        default=DEFAULT_ENV_OUT,
        help="write recommended.env under docs/benchmarks",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="update ~/.config/omarchy-miracast/settings.json and state recommended-cast.env",
    )
    ap.add_argument("--json", action="store_true", help="print recommendation as JSON")
    args = ap.parse_args()

    damage_path = args.damage_tsv if args.damage_tsv.is_file() else None
    rec = recommend(args.results, damage_path, args.icc_bin)

    env_text = format_env(rec)
    args.env_out.parent.mkdir(parents=True, exist_ok=True)
    args.env_out.write_text(env_text)

    if args.apply:
        settings_path = apply_settings(rec)
        state_env = apply_state_env(rec)
        applied = {"settings": str(settings_path), "state_env": str(state_env)}
    else:
        applied = None

    if args.json:
        payload = asdict(rec)
        payload["env_file"] = str(args.env_out)
        payload["applied"] = applied
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"Winner: {rec.case}  (score {rec.score})")
        print(
            f"  capture={rec.capture}  encode={rec.encode}  "
            f"hypr={rec.hypr_cpu}% wf={rec.wf_cpu}% ffmpeg={rec.ffmpeg_cpu}%"
        )
        print(f"  wf-recorder: {rec.wf_recorder_bin}  proto={rec.wf_recorder_proto}")
        print(f"  videoEncoder={rec.video_encoder}  DAMAGE={rec.damage}")
        print(f"  damage note: {rec.damage_rationale}")
        print(f"  notes: {rec.notes}")
        print()
        print(f"Wrote {args.env_out}")
        if applied:
            print(f"Applied settings → {applied['settings']}")
            print(f"Applied state env → {applied['state_env']}")
        else:
            print("Dry-run only. Re-run with --apply to update settings.json.")
        print()
        print(env_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

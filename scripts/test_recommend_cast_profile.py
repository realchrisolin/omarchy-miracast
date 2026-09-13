#!/usr/bin/env python3
"""Unit tests for recommend-cast-profile scoring (no PipeWire required)."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "recommend-cast-profile.py"


def _load():
    import sys

    spec = importlib.util.spec_from_file_location("recommend_cast_profile", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses need the module registered before @dataclass runs
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class RecommendCastProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def _write_results(self, tmp: Path, rows: list[str]) -> Path:
        path = tmp / "results.tsv"
        path.write_text(
            "case\tcapture\tencode\thypr_cpu\twf_cpu\tffmpeg_cpu\ttx_kbps\n"
            + "\n".join(rows)
            + "\n"
        )
        return path

    def test_prefers_icc_dmabuf_when_icc_available(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            results = self._write_results(
                tmp,
                [
                    "icc_dmabuf\ticc\tdmabuf\t15.0\t1.0\t0.1\t400",
                    "icc_cpu\ticc\tcpu\t17.0\t2.0\t40.0\t500",
                    "stock_dmabuf\tstock\tdmabuf\t80.0\t2.0\t0.5\t400",
                ],
            )
            with mock.patch.object(self.mod, "_icc_capable", return_value=True):
                rec = self.mod.recommend(results, None, "/fake/icc-wf-recorder")
            self.assertEqual(rec.capture, "icc")
            self.assertEqual(rec.encode, "dmabuf")
            self.assertEqual(rec.wf_recorder_proto, "icc")

    def test_falls_back_to_stock_when_no_icc_binary(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            results = self._write_results(
                tmp,
                [
                    "icc_dmabuf\ticc\tdmabuf\t15.0\t1.0\t0.1\t400",
                    "stock_dmabuf\tstock\tdmabuf\t80.0\t2.0\t0.5\t400",
                    "stock_cpu\tstock\tcpu\t86.0\t14.0\t37.0\t500",
                ],
            )
            with mock.patch.object(self.mod, "_icc_capable", return_value=False):
                with mock.patch.object(self.mod.shutil, "which", return_value="/usr/bin/wf-recorder"):
                    rec = self.mod.recommend(results, None, "")
            self.assertEqual(rec.capture, "stock")
            self.assertEqual(rec.encode, "dmabuf")
            self.assertEqual(rec.wf_recorder_proto, "auto")

    def test_damage_prefers_aware_when_gpu_not_worse(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            dmg = tmp / "damage.tsv"
            dmg.write_text(
                "case\tcadence\thypr_cpu\twf_cpu\tflux_cpu\trcs_pct\tvcs_pct\tgpu_power_w\thas_D\tstream\tnotes\n"
                "lpcm_continuous_D\tcontinuous_-D\t5\t3\t2\t17\t2\t1.20\ttrue\t1080p30\tx\n"
                "lpcm_damage_aware\tdamage_aware\t7\t3\t3\t16\t2\t1.00\tfalse\t1080p30\tx\n"
            )
            damage, why = self.mod.pick_damage(dmg)
            self.assertEqual(damage, "1")
            self.assertIn("damage A/B", why)

    def test_format_env_exports(self):
        rec = self.mod.Recommendation(
            case="icc_dmabuf",
            capture="icc",
            encode="dmabuf",
            score=16.0,
            hypr_cpu=15.0,
            wf_cpu=1.0,
            ffmpeg_cpu=0.1,
            wf_recorder_bin="/opt/wf",
            wf_recorder_proto="icc",
            video_encoder="auto",
            damage="1",
            damage_rationale="test",
            notes="n",
        )
        env = self.mod.format_env(rec)
        self.assertIn("FLUXCAST_WFD_CAPTURE_ENCODE_PREF=dmabuf", env)
        self.assertIn("FLUXCAST_WFD_WF_RECORDER_PROTO=icc", env)
        self.assertIn("FLUXCAST_WFD_WF_RECORDER_DAMAGE=1", env)


if __name__ == "__main__":
    unittest.main()

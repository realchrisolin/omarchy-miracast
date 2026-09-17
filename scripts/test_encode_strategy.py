#!/usr/bin/env python3
"""Unit tests for encode_strategy.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_path = Path(__file__).resolve().parent / "encode_strategy.py"
_spec = importlib.util.spec_from_file_location("encode_strategy", _path)
assert _spec and _spec.loader
es = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(es)


class EncodeStrategyTest(unittest.TestCase):
    def test_smartview_defaults_dmabuf_qvbr(self):
        s: dict = {"encodeProfile": "best"}
        out = es.apply_to_settings(s, "smartview")
        self.assertEqual(out["encodeStrategy"], "smartview")
        self.assertEqual(s["captureEncode"], "dmabuf")
        self.assertEqual(s["vaapiRcMode"], "QVBR")
        self.assertEqual(s["encodeStrategyAbr"], "sv")
        self.assertTrue(es.uses_sv_abr(s))

    def test_performance_forces_cqp_dmabuf(self):
        s = {
            "encodeProfile": "best",
            "vaapiRcMode": "QVBR",
            "encodeWfdBitrateMbps": 45,
            "encodeCongestionBitrateMbps": 45,
        }
        es.apply_to_settings(s, "performance")
        self.assertEqual(s["vaapiRcMode"], "CQP")
        self.assertEqual(s["captureEncode"], "dmabuf")
        self.assertEqual(s["encodeStrategyAbr"], "cqp_ladder")
        self.assertNotIn("encodeWfdBitrateMbps", s)
        self.assertFalse(es.uses_sv_abr(s))

    def test_retarget_engine_false_keeps_engine(self):
        s = {
            "captureEncode": "vaapi",
            "encodeProfile": "best",
        }
        es.apply_to_settings(s, "smartview", retarget_engine=False)
        self.assertEqual(s["captureEncode"], "vaapi")
        self.assertFalse(s["encodeStrategyPinnedEngine"])

    def test_starve_detector_triggers_after_streak(self):
        streak = 0
        for _ in range(es.STARVE_TICKS - 1):
            hit, streak, reason = es.dmabuf_qvbr_starving(
                capture_path="dmabuf",
                rc_mode="QVBR",
                strategy="smartview",
                air_tx_mbps=2.0,
                target_mbps=45.0,
                video_fps=60.0,
                streak=streak,
            )
            self.assertFalse(hit)
            self.assertIn("streak", reason)
        hit, streak, reason = es.dmabuf_qvbr_starving(
            capture_path="dmabuf",
            rc_mode="QVBR",
            strategy="smartview",
            air_tx_mbps=2.0,
            target_mbps=45.0,
            video_fps=60.0,
            streak=streak,
        )
        self.assertTrue(hit)
        self.assertIn("starve", reason)

    def test_starve_inactive_on_performance_or_pipe(self):
        hit, streak, _ = es.dmabuf_qvbr_starving(
            capture_path="pipe",
            rc_mode="QVBR",
            strategy="smartview",
            air_tx_mbps=1.0,
            target_mbps=45.0,
            video_fps=60.0,
            streak=2,
        )
        self.assertFalse(hit)
        self.assertEqual(streak, 0)
        hit, streak, _ = es.dmabuf_qvbr_starving(
            capture_path="dmabuf",
            rc_mode="CQP",
            strategy="performance",
            air_tx_mbps=1.0,
            target_mbps=45.0,
            video_fps=60.0,
            streak=2,
        )
        self.assertFalse(hit)


if __name__ == "__main__":
    unittest.main()

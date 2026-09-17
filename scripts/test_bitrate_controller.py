#!/usr/bin/env python3
"""Unit tests for Samsung Smart View–style BitrateController."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_path = Path(__file__).resolve().parent / "bitrate_controller.py"
_spec = importlib.util.spec_from_file_location("bitrate_controller", _path)
assert _spec and _spec.loader
bc = importlib.util.module_from_spec(_spec)
sys.modules["bitrate_controller"] = bc
_spec.loader.exec_module(bc)


class BitrateControllerTest(unittest.TestCase):
    def test_stall_decreases(self):
        cfg = bc.BitrateConfig(init_kbps=10000, min_kbps=1500, max_kbps=15000)
        st = bc.BitrateState(kbps=10000)
        d = bc.decide(
            st,
            bc.LinkSignals(video_fps=22.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertLess(d.kbps, 10000)
        self.assertIn("net_stall", d.reason)

    def test_loss_decreases(self):
        cfg = bc.BitrateConfig()
        st = bc.BitrateState(kbps=10000)
        d = bc.decide(
            st,
            bc.LinkSignals(tx_failed_delta=5, video_fps=30.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertLess(d.kbps, 10000)
        self.assertIn("loss", d.reason)

    def test_no_loss_increases_after_streak(self):
        cfg = bc.BitrateConfig()
        st = bc.BitrateState(kbps=8000, good_streak=0)
        for i in range(3):
            d = bc.decide(
                st,
                bc.LinkSignals(retry_percent=0.1, video_fps=30.0),
                cfg,
                cooldown_ok=True,
            )
            st = bc.BitrateState(
                kbps=d.kbps, good_streak=d.good_streak, bad_streak=d.bad_streak
            )
        self.assertTrue(d.changed)
        self.assertGreater(d.kbps, 8000)
        self.assertEqual(d.reason, "no_loss_increase")

    def test_respects_min_max(self):
        cfg = bc.BitrateConfig(init_kbps=2000, min_kbps=2000, max_kbps=3000)
        st = bc.BitrateState(kbps=2000)
        d = bc.decide(
            st,
            bc.LinkSignals(video_fps=20.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertEqual(d.kbps, 2000)

    def test_capacity_clamps_max(self):
        cfg = bc.BitrateConfig(init_kbps=10000, min_kbps=1500, max_kbps=20000)
        st = bc.BitrateState(kbps=10000, good_streak=3)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0, video_fps=30.0, link_capacity_mbps=20.0
            ),
            cfg,
            cooldown_ok=True,
        )
        # max ≤ 45% of 20 Mbps = 9 Mbps
        self.assertLessEqual(d.kbps, 9000)

    def test_band_5ghz_higher_ceiling(self):
        c24 = bc.config_for_band(band_5ghz=False)
        c5 = bc.config_for_band(band_5ghz=True)
        self.assertGreater(c5.max_kbps, c24.max_kbps)

    def test_kbps_roundtrip(self):
        self.assertEqual(bc.ffmpeg_to_kbps("10M"), 10000)
        self.assertEqual(bc.kbps_to_ffmpeg(10000), "10M")
        self.assertEqual(bc.kbps_to_ffmpeg(8500), "8.5M")


if __name__ == "__main__":
    unittest.main()

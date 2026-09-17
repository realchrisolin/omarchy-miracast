#!/usr/bin/env python3
"""Unit tests for QVBR BitrateController (Smart View–aligned)."""

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
        st = bc.BitrateState(kbps=10000, qp=22)
        d = bc.decide(
            st,
            bc.LinkSignals(video_fps=22.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertLess(d.kbps, 10000)
        self.assertIn("net_stall", d.reason)

    def test_retry_percent_does_not_demote(self):
        """iw retry% is not Smart View RR — must not invent LOSS."""
        cfg = bc.config_for_band(band_5ghz=True)
        st = bc.BitrateState(kbps=14000, qp=15)
        d = bc.decide(
            st,
            bc.LinkSignals(retry_percent=25.0, video_fps=60.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertFalse(d.changed)
        self.assertEqual(d.kbps, 14000)
        self.assertEqual(d.qp, 15)

    def test_rtcp_loss_decreases(self):
        cfg = bc.BitrateConfig()
        st = bc.BitrateState(kbps=10000, qp=22)
        d = bc.decide(
            st,
            bc.LinkSignals(loss_fraction=0.05, video_fps=30.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertLess(d.kbps, 10000)
        self.assertIn("loss_rtcp", d.reason)

    def test_tx_failed_treated_as_stall(self):
        cfg = bc.BitrateConfig()
        st = bc.BitrateState(kbps=10000, qp=22)
        d = bc.decide(
            st,
            bc.LinkSignals(tx_failed_delta=3, video_fps=60.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertLess(d.kbps, 10000)
        self.assertIn("tx_failed", d.reason)

    def test_no_loss_increases_after_streak(self):
        cfg = bc.BitrateConfig(max_kbps=20000)
        st = bc.BitrateState(kbps=8000, qp=22, good_streak=0)
        for _ in range(3):
            d = bc.decide(
                st,
                bc.LinkSignals(video_fps=30.0, link_capacity_mbps=72.0),
                cfg,
                cooldown_ok=True,
            )
            st = bc.BitrateState(
                kbps=d.kbps,
                qp=d.qp,
                good_streak=d.good_streak,
                bad_streak=d.bad_streak,
                underfill_streak=d.underfill_streak,
            )
        self.assertTrue(d.changed)
        self.assertGreater(d.kbps, 8000)
        self.assertEqual(d.reason, "no_loss_increase")

    def test_qp_fill_when_under_55pct_of_setpoint(self):
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        ceiling = bc._clamp_kbps(10**9, cfg, 72.2)
        self.assertEqual(ceiling, bc.DEFAULT_MAX_KBPS)
        st = bc.BitrateState(kbps=ceiling, qp=22, good_streak=2, underfill_streak=1)
        d = bc.decide(
            st,
            bc.LinkSignals(
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=5.0,
            ),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.kbps, ceiling)
        self.assertEqual(d.qp, 21)
        self.assertIn("qp_fill", d.reason)

    def test_samsung_1080p_ceiling(self):
        c5 = bc.config_for_band(band_5ghz=True, width_mhz=20)
        self.assertEqual(c5.min_kbps, 2_048)
        self.assertEqual(c5.init_kbps, 8_192)
        self.assertEqual(c5.max_kbps, 14_336)
        self.assertEqual(c5.qp_min, 15)
        self.assertEqual(c5.qp_max, 44)

    def test_kbps_roundtrip(self):
        self.assertEqual(bc.ffmpeg_to_kbps("10M"), 10000)
        self.assertEqual(bc.kbps_to_ffmpeg(10000), "10M")

    def test_coalesce_holds_qp_only(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=14000,
            applied_qp=22,
            desired_kbps=14000,
            desired_qp=21,
            reason="qp_fill:x",
            now=100.0,
            last_apply_ts=90.0,
        )
        self.assertFalse(ok)

    def test_coalesce_applies_after_interval(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=8000,
            applied_qp=22,
            desired_kbps=14000,
            desired_qp=15,
            reason="no_loss_increase",
            now=150.0,
            last_apply_ts=90.0,
        )
        self.assertTrue(ok)
        self.assertIn("ready", reason)


if __name__ == "__main__":
    unittest.main()

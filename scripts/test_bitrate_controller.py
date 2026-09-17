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
        st = bc.BitrateState(kbps=10000, qp=22)
        d = bc.decide(
            st,
            bc.LinkSignals(video_fps=22.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertLess(d.kbps, 10000)
        self.assertGreaterEqual(d.qp, 22)
        self.assertIn("net_stall", d.reason)

    def test_loss_decreases(self):
        cfg = bc.BitrateConfig()
        st = bc.BitrateState(kbps=10000, qp=22)
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
        cfg = bc.BitrateConfig(max_kbps=20000)
        st = bc.BitrateState(kbps=8000, qp=22, good_streak=0)
        for _ in range(3):
            d = bc.decide(
                st,
                bc.LinkSignals(retry_percent=0.1, video_fps=30.0, link_capacity_mbps=72.0),
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
        # Target at ceiling (~54M on 72 MCS) but air only ~7 Mbps → sharpen QP.
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        ceiling = bc._clamp_kbps(10**9, cfg, 72.2)
        st = bc.BitrateState(kbps=ceiling, qp=22, good_streak=2, underfill_streak=1)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=7.0,
            ),
            cfg,
            cooldown_ok=True,
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.kbps, ceiling)
        self.assertEqual(d.qp, 21)
        self.assertIn("qp_fill", d.reason)

    def test_quiet_fill_hold_needs_streak(self):
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        ceiling = bc._clamp_kbps(10**9, cfg, 72.2)
        st = bc.BitrateState(kbps=ceiling, qp=22, good_streak=2, underfill_streak=0)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=7.0,
            ),
            cfg,
            cooldown_ok=True,
        )
        self.assertFalse(d.changed)
        self.assertIn("underfill", d.reason)

    def test_capacity_clamps_max(self):
        cfg = bc.BitrateConfig(init_kbps=10000, min_kbps=1500, max_kbps=20000)
        st = bc.BitrateState(kbps=10000, qp=22, good_streak=3)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0, video_fps=30.0, link_capacity_mbps=20.0
            ),
            cfg,
            cooldown_ok=True,
        )
        self.assertLessEqual(d.kbps, int(20.0 * 1000 * bc.CAPACITY_FRAC))

    def test_band_5ghz_higher_ceiling(self):
        c24 = bc.config_for_band(band_5ghz=False)
        c5 = bc.config_for_band(band_5ghz=True, width_mhz=20)
        c5w = bc.config_for_band(band_5ghz=True, width_mhz=80)
        self.assertGreater(c5.max_kbps, c24.max_kbps)
        self.assertGreaterEqual(c5.max_kbps, 50_000)
        self.assertGreater(c5w.max_kbps, c5.max_kbps)
        self.assertAlmostEqual(bc.CAPACITY_FRAC, 0.75)
        self.assertEqual(
            bc._clamp_kbps(80_000, c5, 72.2),
            min(c5.max_kbps, int(72.2 * 1000 * bc.CAPACITY_FRAC)),
        )

    def test_kbps_roundtrip(self):
        self.assertEqual(bc.ffmpeg_to_kbps("10M"), 10000)
        self.assertEqual(bc.kbps_to_ffmpeg(10000), "10M")
        self.assertEqual(bc.kbps_to_ffmpeg(8500), "8.5M")

    def test_desired_fill(self):
        self.assertAlmostEqual(bc.desired_fill_mbps(45000, 72.2), 45.0)
        self.assertAlmostEqual(bc.desired_fill_mbps(60000, 72.2), 72.2 * 0.75)

    def test_fill_high_uses_mcs_not_demoted_target(self):
        # air 46 Mbps is fine vs 72 MCS; must NOT soften just because target is 22M.
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        st = bc.BitrateState(kbps=22000, qp=22, good_streak=3)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=46.7,
            ),
            cfg,
            cooldown_ok=True,
        )
        self.assertNotIn("fill_high", d.reason)

    def test_coalesce_holds_small_qp_step(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=54000,
            applied_qp=22,
            desired_kbps=54000,
            desired_qp=21,
            reason="qp_fill:air=20<55%×54",
            now=100.0,
            last_apply_ts=90.0,
        )
        self.assertFalse(ok)
        self.assertIn("coalesce", reason)

    def test_coalesce_applies_qp_delta_2_after_interval(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=54000,
            applied_qp=22,
            desired_kbps=54000,
            desired_qp=20,
            reason="qp_fill:air=20<55%×54",
            now=130.0,
            last_apply_ts=90.0,
        )
        self.assertTrue(ok)
        self.assertIn("ready", reason)

    def test_coalesce_urgent_loss_bypasses(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=54000,
            applied_qp=22,
            desired_kbps=40000,
            desired_qp=23,
            reason="loss:0.050",
            now=91.0,
            last_apply_ts=90.0,
        )
        self.assertTrue(ok)
        self.assertIn("urgent", reason)

    def test_air_clamped_to_mcs_before_fill_high(self):
        # Impossible 200 Mbps glitch must behave like air==MCS after clamp.
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        st = bc.BitrateState(kbps=54000, qp=21, good_streak=3)
        d_hi = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=200.0,
            ),
            cfg,
            cooldown_ok=True,
        )
        d_cap = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=72.2,
            ),
            cfg,
            cooldown_ok=True,
        )
        self.assertEqual(d_hi.reason, d_cap.reason)
        self.assertEqual(d_hi.qp, d_cap.qp)


if __name__ == "__main__":
    unittest.main()

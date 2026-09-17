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
        # Modest retry% must not cut on first tick.
        d0 = bc.decide(
            st,
            bc.LinkSignals(retry_percent=3.0, video_fps=30.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertFalse(d0.changed)
        # Sustained high loss (≥8%, 2 ticks) cuts bitrate.
        d1 = bc.decide(
            st,
            bc.LinkSignals(retry_percent=10.0, video_fps=30.0),
            cfg,
            cooldown_ok=True,
        )
        self.assertFalse(d1.changed)
        self.assertIn("loss_streak", d1.reason)
        st = bc.BitrateState(kbps=d1.kbps, qp=d1.qp, bad_streak=d1.bad_streak)
        d = bc.decide(
            st,
            bc.LinkSignals(retry_percent=10.0, video_fps=30.0),
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
        # Samsung max ~14 Mbps; air 5 Mbps (<55%) → sharpen QP.
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        ceiling = bc._clamp_kbps(10**9, cfg, 72.2)
        self.assertEqual(ceiling, bc.DEFAULT_MAX_KBPS)
        st = bc.BitrateState(kbps=ceiling, qp=22, good_streak=2, underfill_streak=1)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
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
                air_tx_mbps=5.0,
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

    def test_samsung_1080p_ceiling(self):
        c24 = bc.config_for_band(band_5ghz=False)
        c5 = bc.config_for_band(band_5ghz=True, width_mhz=20)
        self.assertEqual(c5.min_kbps, 2_048)
        self.assertEqual(c5.init_kbps, 8_192)
        self.assertEqual(c5.max_kbps, 14_336)  # 14 * 1024 from dig
        self.assertEqual(c5.qp_min, 15)
        self.assertEqual(c5.qp_max, 44)
        self.assertGreater(c5.max_kbps, c24.max_kbps)
        # MCS clamp does not raise Samsung max.
        self.assertEqual(bc._clamp_kbps(80_000, c5, 72.2), c5.max_kbps)

    def test_kbps_roundtrip(self):
        self.assertEqual(bc.ffmpeg_to_kbps("10M"), 10000)
        self.assertEqual(bc.kbps_to_ffmpeg(10000), "10M")
        self.assertEqual(bc.kbps_to_ffmpeg(8500), "8.5M")

    def test_desired_fill(self):
        self.assertAlmostEqual(bc.desired_fill_mbps(8_192, 72.2), 8.192)
        self.assertAlmostEqual(bc.desired_fill_mbps(14_336, 72.2), 14.336)

    def test_fill_high_uses_mcs_not_demoted_target(self):
        # air 12 Mbps is under 75%×92% of 72 MCS; must NOT soften.
        cfg = bc.config_for_band(band_5ghz=True, width_mhz=20)
        st = bc.BitrateState(kbps=cfg.max_kbps, qp=22, good_streak=3)
        d = bc.decide(
            st,
            bc.LinkSignals(
                retry_percent=0.0,
                video_fps=60.0,
                link_capacity_mbps=72.2,
                air_tx_mbps=12.0,
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
        self.assertTrue("qp_only" in reason or "coalesce" in reason)

    def test_coalesce_never_flushes_qp_plus_minus_1(self):
        # Even after a long idle, Δqp=1 must not restart capture.
        ok, reason = bc.should_apply_encode(
            applied_kbps=54000,
            applied_qp=24,
            desired_kbps=54000,
            desired_qp=23,
            reason="qp_fill:air=20<55%×54",
            now=200.0,
            last_apply_ts=100.0,
        )
        self.assertFalse(ok)

    def test_coalesce_applies_qp_delta_2_after_interval(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=54000,
            applied_qp=22,
            desired_kbps=54000,
            desired_qp=20,
            reason="qp_fill:air=20<55%×54",
            now=150.0,
            last_apply_ts=90.0,  # 60s > APPLY_MIN_INTERVAL_S (45)
        )
        self.assertTrue(ok)
        self.assertIn("ready", reason)

    def test_loss_does_not_bypass_coalesce(self):
        # Sticky retry% must not force a restart every ~17s.
        ok, reason = bc.should_apply_encode(
            applied_kbps=8000,
            applied_qp=22,
            desired_kbps=6400,
            desired_qp=23,
            reason="loss:0.036",
            now=91.0,
            last_apply_ts=90.0,
        )
        self.assertFalse(ok)
        self.assertIn("min_interval", reason)

    def test_loss_qp_only_at_floor_never_restarts(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=2048,
            applied_qp=28,
            desired_kbps=2048,
            desired_qp=29,
            reason="loss:0.030",
            now=200.0,
            last_apply_ts=100.0,
        )
        self.assertFalse(ok)
        self.assertTrue("qp_only" in reason or "noop" in reason or "coalesce" in reason)

    def test_loss_bitrate_cut_applies_after_interval(self):
        ok, reason = bc.should_apply_encode(
            applied_kbps=8000,
            applied_qp=22,
            desired_kbps=6400,
            desired_qp=23,
            reason="loss:0.050",
            now=150.0,
            last_apply_ts=90.0,
        )
        self.assertTrue(ok)
        self.assertIn("ready", reason)

    def test_loss_at_floor_needs_sustained_bad_streak_for_qp(self):
        cfg = bc.config_for_band(band_5ghz=True)
        st = bc.BitrateState(kbps=cfg.min_kbps, qp=28, bad_streak=0)
        # Need LOSS_STREAK_BEFORE_CUT loss ticks, then +2 more to soften QP at floor.
        for i in range(bc.LOSS_STREAK_BEFORE_CUT + 1):
            d = bc.decide(
                st,
                bc.LinkSignals(retry_percent=10.0, video_fps=60.0),
                cfg,
                cooldown_ok=True,
            )
            st = bc.BitrateState(
                kbps=d.kbps, qp=d.qp, bad_streak=d.bad_streak
            )
            if i < bc.LOSS_STREAK_BEFORE_CUT:
                self.assertFalse(d.changed)
        # At floor, bitrate unchanged; keep going until QP softens.
        softened = False
        for _ in range(5):
            d = bc.decide(
                st,
                bc.LinkSignals(retry_percent=10.0, video_fps=60.0),
                cfg,
                cooldown_ok=True,
            )
            st = bc.BitrateState(
                kbps=d.kbps, qp=d.qp, bad_streak=d.bad_streak
            )
            if d.changed and d.qp > 28:
                softened = True
                self.assertEqual(d.kbps, cfg.min_kbps)
                break
        self.assertTrue(softened)

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

#!/usr/bin/env python3
"""Unit tests for Best (Dynamic) fine-step ladder decisions."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "dynamic_encode.py"
PRE_PATH = ROOT / "scripts" / "encode_quality_presets.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class DynamicEncodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load("dynamic_encode", MOD_PATH)
        cls.p = _load("encode_quality_presets", PRE_PATH)
        cls.qp18 = cls.p.best_step_for_qp(18)
        cls.max_dma = cls.p.best_step_count("dmabuf") - 1

    def test_dmabuf_covers_all_qps(self):
        self.assertEqual(self.p.best_step_count("dmabuf"), 51)
        self.assertEqual(self.p.resolve_best_step("dmabuf", 0)["vaapiQp"], 1)
        self.assertEqual(self.p.resolve_best_step("dmabuf", 50)["vaapiQp"], 51)
        self.assertEqual(self.p.step_display_label("dmabuf", self.qp18), "qp18")

    def test_down_on_tx_failed(self):
        # Delivery / tx-failed demotes on the first bad tick (fast path).
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(
            throughput_mbps=12.0, retry_percent=0.1, delivery_fail=True, tx_failed_delta=1
        )
        d1 = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertTrue(d1.changed)
        self.assertEqual(d1.step, self.qp18 + 1)
        self.assertIn("tx_failed", d1.reason)

    def test_mild_retries_do_not_demote(self):
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=1.65)
        for _ in range(5):
            d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
            self.assertFalse(d.changed, d.reason)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm)

    def test_high_tx_without_capacity_does_not_demote(self):
        """Without iw capacity, high air alone is not actionable."""
        st = self.m.LadderState(step=self.qp18 + 1)
        sample = self.m.LinkSample(throughput_mbps=35.0, retry_percent=0.1, video_fps=30.0)
        for _ in range(5):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma,
                band_5ghz=False, width_mhz=20.0,
            )
            self.assertFalse(d.changed, d.reason)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm)
        d = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma,
            band_5ghz=False, width_mhz=20.0,
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18)
        self.assertIn("up:", d.reason)

    def test_headroom_over_demotes_even_with_healthy_fps(self):
        """Air > 15% of iw tx bitrate → one mild headroom jump."""
        sample = self.m.LinkSample(
            throughput_mbps=12.5,  # just over 0.15*72.2≈10.8
            link_capacity_mbps=72.2,
            retry_percent=0.1,
            video_fps=30.0,
        )
        st = self.m.LadderState(step=self.qp18)
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18 + self.m.HEADROOM_JUMP_MILD)
        self.assertIn("headroom", d.reason)

    def test_headroom_extreme_big_single_jump(self):
        """Air ≥ 3× headroom target → one large CQP jump (not tiny +5)."""
        sample = self.m.LinkSample(
            throughput_mbps=60.0,
            link_capacity_mbps=72.2,
            retry_percent=0.1,
            video_fps=30.0,
        )
        st = self.m.LadderState(step=self.qp18)
        d = self.m.decide(st, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18 + self.m.HEADROOM_JUMP_EXTREME)
        self.assertIn("extreme_harbor", d.reason)
        self.assertIn("headroom", d.reason)

    def test_headroom_under_allows_climb(self):
        sample = self.m.LinkSample(
            throughput_mbps=8.0,
            link_capacity_mbps=72.2,
            retry_percent=0.1,
            video_fps=30.0,
        )
        st = self.m.LadderState(step=self.qp18 + 2, good_streak=self.m.GOOD_STREAK_UP)
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18 + 1)
        self.assertIn("up:", d.reason)

    def test_headroom_over_blocks_climb(self):
        sample = self.m.LinkSample(
            throughput_mbps=20.0,
            link_capacity_mbps=72.2,
            retry_percent=0.1,
            video_fps=30.0,
        )
        st = self.m.LadderState(step=self.qp18 + 2, good_streak=self.m.GOOD_STREAK_UP)
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertGreater(d.step, self.qp18 + 2)
        self.assertIn("headroom", d.reason)

    def test_quiet_air_not_headroom_violation(self):
        sample = self.m.LinkSample(
            throughput_mbps=2.5,
            link_capacity_mbps=72.2,
            retry_percent=0.1,
            video_fps=30.0,
        )
        st = self.m.LadderState(step=self.qp18)
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertFalse(d.changed, d.reason)

    def test_fps_drop_demotes(self):
        """Mild fps sag demotes on the first tick when cooldown allows."""
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(
            throughput_mbps=55.0, retry_percent=0.05, video_fps=26.0
        )
        d0 = self.m.decide(st, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertFalse(d0.changed)
        self.assertIn("down_cooldown", d0.reason)
        d1 = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d1.changed)
        self.assertEqual(d1.step, self.qp18 + 1)
        self.assertIn("fps", d1.reason)

    def test_critical_fps_multi_step_demote(self):
        """fps < 24 (but above extreme) is severe — multi-step, skip cooldown."""
        st = self.m.LadderState(step=self.qp18)
        # 23.5 is critical (<24) but not extreme (<23).
        sample = self.m.LinkSample(
            throughput_mbps=55.0, retry_percent=0.05, video_fps=23.5
        )
        d1 = self.m.decide(st, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d1.changed)
        self.assertEqual(d1.step, self.qp18 + self.m.SEVERE_DOWN_STEPS)
        self.assertIn("fps", d1.reason)
        self.assertIn("+2", d1.reason)
        self.assertNotIn("extreme_harbor", d1.reason)

    def test_extreme_fps_jumps_to_harbor(self):
        """JScreenFix-class fps sag jumps to safe harbor and settles."""
        cliff = 6  # ~qp7 — where prior sessions died
        st = self.m.LadderState(step=cliff)
        sample = self.m.LinkSample(
            throughput_mbps=70.0, retry_percent=0.2, video_fps=22.4
        )
        d = self.m.decide(st, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, cliff + self.m.EXTREME_DOWN_STEPS)
        self.assertIn("extreme_harbor", d.reason)
        self.assertGreaterEqual(d.demote_count, self.m.MAX_DEMOTES_BEFORE_NO_CLIMB)
        self.assertEqual(d.climb_floor_step, cliff + self.m.EXTREME_DOWN_STEPS)

    def test_extreme_retry_pct_multi_step(self):
        st = self.m.LadderState(step=8)
        sample = self.m.LinkSample(
            throughput_mbps=40.0, retry_percent=9.5, video_fps=30.0
        )
        d = self.m.decide(st, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, 8 + self.m.EXTREME_DOWN_STEPS)
        self.assertIn("extreme_harbor", d.reason)

    def test_content_hot_blocks_climb(self):
        """Marginal fps must not climb back into oversat."""
        st = self.m.LadderState(
            step=self.qp18 + 2,
            good_streak=self.m.GOOD_STREAK_UP,
        )
        sample = self.m.LinkSample(
            throughput_mbps=55.0, retry_percent=0.1, video_fps=29.0
        )
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertFalse(d.changed)
        self.assertIn("content_hot", d.reason)

    def test_severe_from_cliff_exits_cliff(self):
        """Severe demote from deep cliff must leave the cliff zone."""
        st = self.m.LadderState(step=5)
        sample = self.m.LinkSample(
            throughput_mbps=50.0, retry_percent=0.1, video_fps=23.5
        )
        d = self.m.decide(st, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertGreater(d.step, self.m.CLIFF_STEP_MAX)
        self.assertIn("fps", d.reason)

    def test_near_zero_fps_ignored_as_counter_reset(self):
        """Low fps after capture rebind must not severe-demote."""
        st = self.m.LadderState(step=self.qp18)
        for fps in (0.2, 6.4, 10.6, 14.9):
            sample = self.m.LinkSample(
                throughput_mbps=20.0, retry_percent=0.05, video_fps=fps
            )
            d1 = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
            self.assertFalse(d1.changed, f"fps={fps}: {d1.reason}")

    def test_cqp_soft_tx_does_not_demote(self):
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(throughput_mbps=4.6, retry_percent=0.1)
        for _ in range(5):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma,
                band_5ghz=False, width_mhz=20.0,
            )
            self.assertFalse(d.changed, d.reason)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm)

    def test_up_when_clean(self):
        st = self.m.LadderState(step=self.qp18 + 2)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1)
        for _ in range(self.m.GOOD_STREAK_UP - 1):
            d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
            self.assertFalse(d.changed)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm)
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18 + 1)

    def test_three_demotes_block_climb(self):
        st = self.m.LadderState(step=self.qp18, demote_count=3)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1)
        for _ in range(self.m.GOOD_STREAK_UP):
            d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
            self.assertFalse(d.changed)
            self.assertIn("no_climb", d.reason)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm)

    def test_signal_recover_allows_climb_again(self):
        # Need ≥10 dB above floor (was 6) — -60 → -50.
        st = self.m.LadderState(step=self.qp18, demote_count=3, signal_floor_dbm=-60.0)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1, signal_dbm=-50.0)
        d0 = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertEqual(d0.demote_count, 0)
        self.assertEqual(d0.climb_good_need, self.m.GOOD_STREAK_UP_AFTER_RECOVER)
        st = self.m.LadderState(
            d0.step,
            d0.bad_streak,
            d0.good_streak,
            d0.demote_count,
            d0.signal_floor_dbm,
            d0.climb_good_need,
        )
        # Normal GOOD_STREAK_UP must not climb yet (post-recover hold).
        for _ in range(self.m.GOOD_STREAK_UP):
            d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
            self.assertFalse(d.changed, d.reason)
            st = self.m.LadderState(
                d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm, d.climb_good_need
            )
        up = None
        for _ in range(self.m.GOOD_STREAK_UP_AFTER_RECOVER + 2):
            d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
            st = self.m.LadderState(
                d.step, d.bad_streak, d.good_streak, d.demote_count, d.signal_floor_dbm, d.climb_good_need
            )
            if d.changed and "up:" in d.reason:
                up = d
                break
        self.assertIsNotNone(up)
        self.assertEqual(up.step, self.qp18 - 1)
        self.assertEqual(up.climb_good_need, self.m.GOOD_STREAK_UP)

    def test_mild_signal_bounce_does_not_recover(self):
        """+6 dB used to wipe settle; require +10 dB now (+8 bounce must hold)."""
        st = self.m.LadderState(step=self.qp18, demote_count=3, signal_floor_dbm=-51.0)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1, signal_dbm=-43.0)
        d0 = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertEqual(d0.demote_count, 3)
        self.assertIn("no_climb", d0.reason)

    def test_one_demote_still_allows_climb(self):
        """Demote limit is 3 — a single demote must not freeze the ladder."""
        st = self.m.LadderState(
            step=self.qp18,
            demote_count=1,
            signal_floor_dbm=-40.0,
            good_streak=self.m.GOOD_STREAK_UP - 1,
            climb_good_need=self.m.GOOD_STREAK_UP,
        )
        sample = self.m.LinkSample(
            throughput_mbps=20.0, retry_percent=0.1, signal_dbm=-35.0, video_fps=30.0
        )
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18 - 1)
        self.assertIn("up:", d.reason)

    def test_soft_ceiling_blocks_reclimb_past_demote_target(self):
        """After demoting to step X, cannot climb sharper than X until demotes clear."""
        floor = self.qp18 + 3  # demote target
        st = self.m.LadderState(
            step=floor,
            demote_count=1,
            climb_floor_step=floor,
            good_streak=self.m.GOOD_STREAK_UP - 1,
            climb_good_need=self.m.GOOD_STREAK_UP,
        )
        sample = self.m.LinkSample(
            throughput_mbps=20.0, retry_percent=0.1, signal_dbm=-35.0, video_fps=30.0
        )
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertFalse(d.changed)
        self.assertIn("climb_floor", d.reason)

    def test_demote_sets_soft_ceiling(self):
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(
            throughput_mbps=55.0, retry_percent=0.05, video_fps=26.0
        )
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.climb_floor_step, d.step)

    def test_demote_decay_after_long_clean(self):
        st = self.m.LadderState(
            step=self.qp18,
            demote_count=3,
            signal_floor_dbm=-40.0,
            good_streak=self.m.DEMOTE_DECAY_STREAK - 1,
        )
        sample = self.m.LinkSample(
            throughput_mbps=20.0, retry_percent=0.1, signal_dbm=-35.0, video_fps=30.0
        )
        d = self.m.decide(st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertFalse(d.changed)
        self.assertEqual(d.demote_count, 2)
        self.assertIn("demote_decay", d.reason)

    def test_cliff_tx_failed_is_severe(self):
        """qp≤10: any tx-failed multi-steps immediately."""
        cliff_step = self.m.CLIFF_STEP_MAX
        st = self.m.LadderState(step=cliff_step)
        sample = self.m.LinkSample(
            throughput_mbps=50.0, retry_percent=0.1, tx_failed_delta=1, video_fps=30.0
        )
        d = self.m.decide(st, sample, cooldown_ok=False, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, cliff_step + self.m.SEVERE_DOWN_STEPS)
        self.assertIn("tx_failed", d.reason)

    def test_cliff_soft_fps_is_severe(self):
        cliff_step = self.m.CLIFF_STEP_MAX
        st = self.m.LadderState(step=cliff_step)
        sample = self.m.LinkSample(
            throughput_mbps=50.0, retry_percent=0.1, video_fps=28.5
        )
        d = self.m.decide(st, sample, cooldown_ok=False, rc_mode="cqp", min_step=0, max_step=self.max_dma)
        self.assertTrue(d.changed)
        self.assertEqual(d.step, cliff_step + self.m.SEVERE_DOWN_STEPS)
        self.assertIn("fps", d.reason)

    def test_best_maps_to_high_knobs(self):
        data: dict = {"captureEncode": "dmabuf"}
        out = self.p.apply_to_settings(data, tier="best")
        self.assertEqual(out["encodeProfile"], "best")
        self.assertEqual(out["encodeProfileEffectiveStep"], self.qp18)
        self.assertEqual(data["vaapiQp"], 18)


if __name__ == "__main__":
    unittest.main()

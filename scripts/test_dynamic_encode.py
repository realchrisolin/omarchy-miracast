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

    def test_down_on_high_retries(self):
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=1.2)
        d1 = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertFalse(d1.changed)
        st2 = self.m.LadderState(d1.step, d1.bad_streak, d1.good_streak)
        d2 = self.m.decide(
            st2, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertTrue(d2.changed)
        self.assertEqual(d2.step, self.qp18 + 1)
        self.assertIn("down:", d2.reason)

    def test_cqp_soft_tx_does_not_demote(self):
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(throughput_mbps=4.6, retry_percent=0.1)
        for _ in range(5):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma,
                band_5ghz=False, width_mhz=20.0,
            )
            self.assertFalse(d.changed, d.reason)
            self.assertEqual(d.step, self.qp18)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak)

    def test_cqp_high_tx_alone_does_not_demote(self):
        """No hard Mbps ceiling — high CQP TX with clean retries must not demote."""
        st = self.m.LadderState(step=self.p.best_step_for_qp(8))
        sample = self.m.LinkSample(throughput_mbps=36.0, retry_percent=0.05)
        for _ in range(5):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma,
                band_5ghz=False, width_mhz=20.0,
            )
            self.assertFalse(d.changed, d.reason)
            self.assertEqual(d.step, self.p.best_step_for_qp(8))
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak)

    def test_delivery_fail_demotes(self):
        st = self.m.LadderState(step=self.qp18)
        sample = self.m.LinkSample(throughput_mbps=25.0, retry_percent=0.1, delivery_fail=True)
        d1 = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="CQP", min_step=0, max_step=self.max_dma
        )
        self.assertFalse(d1.changed)
        st2 = self.m.LadderState(d1.step, d1.bad_streak, d1.good_streak)
        d2 = self.m.decide(
            st2, sample, cooldown_ok=False, rc_mode="CQP", min_step=0, max_step=self.max_dma
        )
        self.assertTrue(d2.changed)
        self.assertGreater(d2.step, st.step)
        self.assertIn("delivery_fail", d2.reason)

    def test_cbr_soft_tx_does_demote(self):
        step = self.p.best_step_for_bitrate_mbps("vaapi", 15)
        st = self.m.LadderState(step=step)
        sample = self.m.LinkSample(throughput_mbps=4.6, retry_percent=0.1)
        max_s = self.p.best_step_count("vaapi") - 1
        d1 = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="CBR", min_step=0, max_step=max_s, target_mbps=15.0
        )
        self.assertFalse(d1.changed)
        st2 = self.m.LadderState(d1.step, d1.bad_streak, d1.good_streak)
        d2 = self.m.decide(
            st2, sample, cooldown_ok=True, rc_mode="CBR", min_step=0, max_step=max_s, target_mbps=15.0
        )
        self.assertTrue(d2.changed)
        self.assertEqual(d2.step, step + 1)
        self.assertIn("throughput", d2.reason)

    def test_up_toward_sharper_qp(self):
        st = self.m.LadderState(step=self.qp18 + 2)  # qp20
        sample = self.m.LinkSample(throughput_mbps=11.0, retry_percent=0.1)
        for _ in range(self.m.GOOD_STREAK_UP - 1):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma
            )
            self.assertFalse(d.changed)
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak)
        d = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.qp18 + 1)
        self.assertEqual(d.reason, "up:sustained_clean")

    def test_can_climb_below_qp16(self):
        """On 5 GHz with headroom under the ceiling, Best can still sharpen past qp16."""
        st = self.m.LadderState(step=self.p.best_step_for_qp(16))
        sample = self.m.LinkSample(throughput_mbps=20.0, retry_percent=0.1)
        for _ in range(self.m.GOOD_STREAK_UP - 1):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma,
                band_5ghz=True, width_mhz=40.0,
            )
            st = self.m.LadderState(d.step, d.bad_streak, d.good_streak)
        d = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma,
            band_5ghz=True, width_mhz=40.0,
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.p.best_step_for_qp(15))
        self.assertEqual(
            self.p.resolve_best_step("dmabuf", d.step)["vaapiQp"], 15
        )

    def test_cooldown_blocks_up_not_down(self):
        # Ups still cooldown-gated.
        st = self.m.LadderState(step=self.qp18 + 2, bad_streak=0, good_streak=20)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1)
        d = self.m.decide(
            st, sample, cooldown_ok=False, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertFalse(d.changed)
        self.assertEqual(d.reason, "cooldown")

        # Downs are never cooldown-blocked.
        st2 = self.m.LadderState(step=self.qp18, bad_streak=5)
        sample2 = self.m.LinkSample(throughput_mbps=12.0, retry_percent=2.0)
        d2 = self.m.decide(
            st2, sample2, cooldown_ok=False, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertTrue(d2.changed)
        self.assertEqual(d2.step, self.qp18 + 1)
        self.assertIn("down:", d2.reason)

    def test_severe_retry_multi_step_down(self):
        st = self.m.LadderState(step=self.p.best_step_for_qp(12), bad_streak=1)
        sample = self.m.LinkSample(throughput_mbps=1.0, retry_percent=50.0, stalled=True)
        d = self.m.decide(
            st, sample, cooldown_ok=False, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.step, self.p.best_step_for_qp(12) + self.m.SEVERE_DOWN_STEPS)
        self.assertIn("+", d.reason)

    def test_three_demotes_block_climb(self):
        st = self.m.LadderState(step=self.qp18, demote_count=3)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1)
        for _ in range(self.m.GOOD_STREAK_UP):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma,
                band_5ghz=False, width_mhz=20.0,
            )
            self.assertFalse(d.changed)
            self.assertIn("no_climb", d.reason)
            st = self.m.LadderState(
                d.step, d.bad_streak, d.good_streak, d.demote_count,
                d.signal_floor_dbm, d.climb_floor_step,
            )

    def test_demote_raises_climb_floor(self):
        qp13 = self.p.best_step_for_qp(13)
        st = self.m.LadderState(step=qp13, bad_streak=1)
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=1.2)
        d = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma
        )
        self.assertTrue(d.changed)
        self.assertEqual(d.step, qp13 + 1)
        self.assertEqual(d.climb_floor_step, qp13 + 1)

    def test_signal_recover_probes_one_not_to_qp1(self):
        """Recover clears demote settle but only relaxes floor by one step."""
        qp14 = self.p.best_step_for_qp(14)
        st = self.m.LadderState(
            step=qp14,
            demote_count=3,
            signal_floor_dbm=-60.0,
            climb_floor_step=qp14,  # was blocked from returning to qp13
            good_streak=0,
        )
        sample = self.m.LinkSample(throughput_mbps=12.0, retry_percent=0.1, signal_dbm=-52.0)
        d0 = self.m.decide(
            st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma,
            band_5ghz=False, width_mhz=20.0,
        )
        self.assertEqual(d0.demote_count, 0)
        # Floor relaxed by one → may probe qp13, not free-run to qp1.
        self.assertEqual(d0.climb_floor_step, qp14 - 1)
        st = self.m.LadderState(
            d0.step, d0.bad_streak, d0.good_streak, d0.demote_count,
            d0.signal_floor_dbm, d0.climb_floor_step,
        )
        up = None
        for _ in range(self.m.GOOD_STREAK_UP + 2):
            d = self.m.decide(
                st, sample, cooldown_ok=True, rc_mode="cqp", min_step=0, max_step=self.max_dma,
                band_5ghz=False, width_mhz=20.0,
            )
            st = self.m.LadderState(
                d.step, d.bad_streak, d.good_streak, d.demote_count,
                d.signal_floor_dbm, d.climb_floor_step,
            )
            if d.changed and "up:" in d.reason:
                up = d
                break
        self.assertIsNotNone(up)
        self.assertEqual(up.step, qp14 - 1)
        # Must not have climbed past the relaxed floor toward qp1.
        self.assertGreaterEqual(up.climb_floor_step, qp14 - 1)
        self.assertGreater(up.step, self.p.best_step_for_qp(1))

    def test_best_maps_to_high_knobs(self):
        data: dict = {"captureEncode": "dmabuf"}
        out = self.p.apply_to_settings(data, tier="best")
        self.assertEqual(out["encodeProfile"], "best")
        self.assertEqual(out["encodeProfileEffectiveStep"], self.qp18)
        self.assertEqual(out["encodeProfileEffectiveLabel"], "qp18")
        self.assertEqual(data["vaapiQp"], 18)

    def test_5ghz_or_24_can_select_qp1(self):
        data: dict = {"captureEncode": "dmabuf"}
        out = self.p.apply_to_settings(data, tier="best", effective_step=0)
        self.assertEqual(out["encodeProfileEffectiveStep"], 0)
        self.assertEqual(data["vaapiQp"], 1)
        self.assertEqual(out["encodeProfileEffectiveLabel"], "qp1")


if __name__ == "__main__":
    unittest.main()

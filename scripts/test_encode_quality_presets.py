#!/usr/bin/env python3
"""Unit tests for per-engine encode quality presets."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "encode_quality_presets.py"


def _load():
    spec = importlib.util.spec_from_file_location("encode_quality_presets", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _mbps(text: str) -> float:
    t = str(text).strip().lower()
    if t.endswith("m"):
        return float(t[:-1])
    if t.endswith("k"):
        return float(t[:-1]) / 1000.0
    return float(t)


class EncodeQualityPresetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_20mhz_budget_knobs_cap_at_16m(self):
        for engine in self.mod.ENGINES:
            for tier in self.mod.TIERS_20MHZ:
                knobs = self.mod.resolve_preset(engine, tier)
                self.assertLessEqual(_mbps(knobs["vaapiBitrate"]), 16.0 + 1e-6)
                self.assertLessEqual(_mbps(knobs["bitrate"]), 16.0 + 1e-6)

    def test_veryhigh_exceeds_20mhz_budget(self):
        for engine in self.mod.ENGINES:
            knobs = self.mod.resolve_preset(engine, "veryhigh")
            self.assertGreater(_mbps(knobs["bitrate"]), 16.0)
            self.assertTrue(self.mod.tier_requires_5ghz("veryhigh"))

    def test_freq_allows_veryhigh(self):
        self.assertFalse(self.mod.freq_allows_veryhigh(2437))
        self.assertFalse(self.mod.freq_allows_veryhigh(None))
        self.assertTrue(self.mod.freq_allows_veryhigh(5180))
        self.assertTrue(self.mod.freq_allows_veryhigh(5955))

    def test_dmabuf_high_uses_cqp_not_starved_cbr(self):
        dma = self.mod.resolve_preset("dmabuf", "high")
        pipe = self.mod.resolve_preset("vaapi", "high")
        self.assertEqual(dma["vaapiRcMode"], "CQP")
        self.assertEqual(dma["vaapiQp"], 18)
        self.assertEqual(dma["vaapiQuality"], "2")
        self.assertEqual(dma["vaapiIQfactor"], "1.3")
        self.assertEqual(pipe["vaapiRcMode"], "CBR")
        self.assertEqual(pipe["bitrate"], "15M")
        self.assertEqual(pipe["vaapiQuality"], "3")
        self.assertEqual(pipe["vaapiAsyncDepth"], 1)

    def test_dmabuf_veryhigh_is_sharper_cqp(self):
        vh = self.mod.resolve_preset("dmabuf", "veryhigh")
        self.assertEqual(vh["vaapiRcMode"], "CQP")
        self.assertEqual(vh["vaapiQp"], 16)
        self.assertEqual(vh["bitrate"], "28M")

    def test_apply_retargets_on_engine_change(self):
        data = {"captureEncode": "vaapi", "encodeProfile": "high"}
        self.mod.apply_to_settings(data, tier="high", engine="vaapi")
        self.assertEqual(data["vaapiRcMode"], "CBR")
        self.assertEqual(data["bitrate"], "15M")
        self.mod.apply_to_settings(data, tier="high", engine="dmabuf")
        self.assertEqual(data["captureEncode"], "dmabuf")
        self.assertEqual(data["vaapiRcMode"], "CQP")
        self.assertEqual(data["vaapiQp"], 18)
        self.assertEqual(data["vaapiQuality"], "2")

    def test_best_profile_starts_high_step(self):
        data = {"captureEncode": "vaapi"}
        out = self.mod.apply_to_settings(data, tier="best")
        self.assertEqual(out["encodeProfile"], "best")
        self.assertEqual(out["encodeProfileEffective"], "high")
        self.assertEqual(out["encodeProfileEffectiveStep"], self.mod.best_start_step("vaapi"))
        self.assertEqual(
            data["bitrate"],
            self.mod.resolve_best_step("vaapi", out["encodeProfileEffectiveStep"])["bitrate"],
        )

    def test_vaapi_best_ladder_is_aosp_cbr(self):
        """AOSP WifiDisplay uses Constant bitrate on the encode path."""
        self.assertGreater(self.mod.best_step_count("vaapi"), 10)
        step = self.mod.best_start_step("vaapi")
        knobs = self.mod.resolve_best_step("vaapi", step)
        self.assertEqual(knobs["vaapiRcMode"], "CBR")
        self.assertEqual(knobs["vaapiAsyncDepth"], 1)
        named = self.mod.resolve_preset("vaapi", "high")
        self.assertEqual(named["vaapiRcMode"], "CBR")

    def test_min_best_step_no_band_cap(self):
        self.assertEqual(self.mod.min_best_step("dmabuf", band_5ghz=False), 0)
        self.assertEqual(self.mod.min_best_step("dmabuf", band_5ghz=True), 0)
        self.assertEqual(self.mod.best_step_count("dmabuf"), 51)


if __name__ == "__main__":
    unittest.main()

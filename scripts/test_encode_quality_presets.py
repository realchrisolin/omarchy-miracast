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

    def test_budget_knobs_cap_at_16m(self):
        for engine in self.mod.ENGINES:
            for tier in self.mod.TIERS:
                knobs = self.mod.resolve_preset(engine, tier)
                self.assertLessEqual(_mbps(knobs["vaapiBitrate"]), 16.0 + 1e-6)
                self.assertLessEqual(_mbps(knobs["bitrate"]), 16.0 + 1e-6)

    def test_dmabuf_high_uses_cqp_not_starved_cbr(self):
        dma = self.mod.resolve_preset("dmabuf", "high")
        pipe = self.mod.resolve_preset("vaapi", "high")
        self.assertEqual(dma["vaapiRcMode"], "CQP")
        self.assertEqual(dma["vaapiQp"], 19)
        self.assertEqual(dma["vaapiQuality"], "2")
        self.assertEqual(dma["vaapiIQfactor"], "1.3")
        self.assertEqual(pipe["vaapiRcMode"], "CBR")
        self.assertEqual(pipe["bitrate"], "12M")
        self.assertEqual(pipe["vaapiQuality"], "4")
        self.assertEqual(pipe["vaapiAsyncDepth"], 1)

    def test_apply_retargets_on_engine_change(self):
        data = {"captureEncode": "vaapi", "encodeProfile": "high"}
        self.mod.apply_to_settings(data, tier="high", engine="vaapi")
        self.assertEqual(data["vaapiRcMode"], "CBR")
        self.assertEqual(data["bitrate"], "12M")
        self.mod.apply_to_settings(data, tier="high", engine="dmabuf")
        self.assertEqual(data["captureEncode"], "dmabuf")
        self.assertEqual(data["vaapiRcMode"], "CQP")
        self.assertEqual(data["vaapiQp"], 19)
        self.assertEqual(data["vaapiQuality"], "2")


if __name__ == "__main__":
    unittest.main()

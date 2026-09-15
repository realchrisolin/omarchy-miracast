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


class EncodeQualityPresetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_dmabuf_high_sharper_than_pipe_high(self):
        dma = self.mod.resolve_preset("dmabuf", "high")
        pipe = self.mod.resolve_preset("vaapi", "high")
        self.assertLess(int(dma["vaapiQp"]), int(pipe["vaapiQp"]))
        self.assertLessEqual(int(dma["vaapiQuality"]), int(pipe["vaapiQuality"]))
        self.assertEqual(dma["vaapiRcMode"], "QVBR")
        self.assertEqual(pipe["vaapiRcMode"], "QVBR")

    def test_apply_retargets_on_engine_change(self):
        data = {"captureEncode": "vaapi", "encodeProfile": "high"}
        self.mod.apply_to_settings(data, tier="high", engine="vaapi")
        self.assertEqual(data["vaapiQuality"], "4")
        self.mod.apply_to_settings(data, tier="high", engine="dmabuf")
        self.assertEqual(data["captureEncode"], "dmabuf")
        self.assertEqual(data["encodeProfile"], "high")
        self.assertEqual(data["vaapiQp"], 16)
        self.assertEqual(data["vaapiQuality"], "3")


if __name__ == "__main__":
    unittest.main()

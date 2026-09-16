#!/usr/bin/env python3
"""Unit tests for GPU encode info / card-reported names."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "gpu_encode_info.py"


def _load():
    spec = importlib.util.spec_from_file_location("gpu_encode_info", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class GpuEncodeInfoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def test_vendor_from_pci_id(self):
        self.assertEqual(self.m._vendor_from_pci_id("8086:9A49"), "intel")
        self.assertEqual(self.m._vendor_from_pci_id("1002:73FF"), "amd")
        self.assertEqual(self.m._vendor_from_pci_id("10DE:25A0"), "nvidia")
        self.assertEqual(self.m._vendor_from_pci_id("1234:5678"), "unknown")

    def test_vendor_from_driver(self):
        self.assertEqual(self.m._vendor_from_driver("i915"), "intel")
        self.assertEqual(self.m._vendor_from_driver("amdgpu"), "amd")
        self.assertEqual(self.m._vendor_from_driver("nvidia"), "nvidia")

    def test_pci_id_tail_strip(self):
        raw = "Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics] [8086:9a49] (rev 01)"
        cleaned = self.m._PCI_ID_TAIL_RE.sub("", raw).strip()
        self.assertEqual(
            cleaned, "Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics]"
        )
        self.assertIn("[Iris Xe Graphics]", cleaned)
        self.assertNotIn("8086:9a49", cleaned)

    def test_lspci_device_name_parse(self):
        line = (
            "00:02.0 VGA compatible controller [0300]: "
            "Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics] "
            "[8086:9a49] (rev 01)\n"
        )
        with mock.patch.object(self.m, "_run", return_value=line):
            name = self.m._lspci_device_name("00:02.0")
        self.assertEqual(
            name, "Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics]"
        )

    def test_collect_smoke(self):
        info = self.m.collect()
        self.assertTrue(info.get("ok"))
        self.assertIn(info.get("vendor"), ("intel", "amd", "nvidia", "unknown"))
        self.assertIsInstance(info.get("pipeEncoders"), list)
        self.assertIn("dmabufLikely", info)
        # On a real DRM host we expect a device name string.
        if info.get("gpus"):
            self.assertTrue(str(info.get("deviceName") or "").strip())


if __name__ == "__main__":
    unittest.main()

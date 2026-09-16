#!/usr/bin/env python3
"""Unit tests for link-watch soft-TX / fps stall gating."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "miracast_link_watch.py"


def _load():
    spec = importlib.util.spec_from_file_location("miracast_link_watch", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class LinkWatchStallGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load()

    def test_video_fps_healthy(self):
        self.assertTrue(self.m._video_fps_healthy(30.0))
        self.assertTrue(self.m._video_fps_healthy(20.0))
        self.assertFalse(self.m._video_fps_healthy(19.9))
        self.assertFalse(self.m._video_fps_healthy(None))

    def test_waydroid_soft_tx_not_stall(self):
        # ~2 Mbps at 30 fps must not count as stall (CQP quiet UI).
        self.assertFalse(
            self.m._soft_tx_counts_as_stall(2.0, 30.0, stall_mbps=3.0)
        )
        self.assertFalse(
            self.m._soft_tx_counts_as_stall(1.9, 30.0, stall_mbps=3.0)
        )

    def test_soft_tx_with_dead_fps_is_stall(self):
        self.assertTrue(
            self.m._soft_tx_counts_as_stall(2.0, 5.0, stall_mbps=3.0)
        )
        self.assertTrue(
            self.m._soft_tx_counts_as_stall(2.0, None, stall_mbps=3.0)
        )

    def test_near_zero_tx_is_stall(self):
        self.assertTrue(
            self.m._soft_tx_counts_as_stall(0.05, 30.0, stall_mbps=3.0)
        )

    def test_recent_video_fps_from_log(self):
        text = "\n".join(
            [
                "2026-09-16T03:46:01-0400 [FluxCast WFD Media] Sender health: "
                "tx+100 KiB; video_frames=100 audio_frames=10",
                "2026-09-16T03:46:06-0400 [FluxCast WFD Media] Sender health: "
                "tx+200 KiB; video_frames=250 audio_frames=20",
            ]
        )
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "cast.log"
            log.write_text(text)
            fps = self.m._recent_video_fps(log)
        self.assertIsNotNone(fps)
        # 150 frames / 5 s = 30 fps
        self.assertAlmostEqual(fps, 30.0, places=1)


if __name__ == "__main__":
    unittest.main()

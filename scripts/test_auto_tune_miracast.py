#!/usr/bin/env python3
"""Unit tests for auto_tune_miracast (offline picks + live refine heuristics)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "auto_tune_miracast.py"


def _load():
    spec = importlib.util.spec_from_file_location("auto_tune_miracast", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class AutoTuneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_pick_encode_prefers_dmabuf_when_available(self):
        eng = self.mod.EngineProbe(
            dmabuf=True, vaapi_pipe=True, cpu=True,
            ffmpeg="/usr/bin/ffmpeg", wf_recorder="/usr/bin/wf-recorder",
            vaapi_device=True,
        )
        enc, why = self.mod.pick_encode(eng)
        self.assertEqual(enc, "dmabuf")
        self.assertIn("DMA-BUF", why)

    def test_pick_encode_falls_back_vaapi(self):
        eng = self.mod.EngineProbe(
            dmabuf=False, vaapi_pipe=True, cpu=True,
            ffmpeg="/usr/bin/ffmpeg", wf_recorder="",
            vaapi_device=True,
        )
        enc, _ = self.mod.pick_encode(eng)
        self.assertEqual(enc, "vaapi")

    def test_pick_encode_honors_supported_bench_winner(self):
        eng = self.mod.EngineProbe(
            dmabuf=True, vaapi_pipe=True, cpu=True,
            ffmpeg="/usr/bin/ffmpeg", wf_recorder="/usr/bin/wf-recorder",
            vaapi_device=True,
        )
        enc, why = self.mod.pick_encode(eng, bench_encode="vaapi")
        self.assertEqual(enc, "vaapi")
        self.assertIn("benchmark winner", why)

    def test_pick_encode_skips_unsupported_bench_winner(self):
        eng = self.mod.EngineProbe(
            dmabuf=False, vaapi_pipe=False, cpu=True,
            ffmpeg="", wf_recorder="",
            vaapi_device=False,
        )
        enc, why = self.mod.pick_encode(eng, bench_encode="dmabuf")
        self.assertEqual(enc, "cpu")
        self.assertIn("unsupported", why)

    def test_tune_applies_settings(self):
        fake_engines = self.mod.EngineProbe(
            dmabuf=True, vaapi_pipe=True, cpu=True,
            ffmpeg="/usr/bin/ffmpeg", wf_recorder="/usr/bin/wf-recorder",
            vaapi_device=True,
        )
        radios = [
            {
                "iface": "wlan1",
                "p2pGo": True,
                "inUse": False,
                "adapterName": "USB Dongle",
            },
            {
                "iface": "wlp0s20f3",
                "p2pGo": True,
                "inUse": True,
                "adapterName": "Intel Corporation Wi-Fi 6 AX201",
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg = tmp / "config"
            state = tmp / "state"
            cfg.mkdir()
            state.mkdir()
            with mock.patch.object(self.mod, "probe_engines", return_value=fake_engines):
                with mock.patch.object(self.mod, "try_benchmark_profile", return_value=None):
                    with mock.patch.object(
                        self.mod,
                        "pick_radio",
                        return_value=("auto", "wlan1", radios, "auto → wlan1"),
                    ):
                        with mock.patch.dict(
                            "os.environ",
                            {
                                "XDG_CONFIG_HOME": str(cfg),
                                "XDG_STATE_HOME": str(state),
                            },
                            clear=False,
                        ):
                            result = self.mod.tune(offline_only=True)
                            applied = self.mod.apply_tune(result)
            settings = json.loads(Path(applied["settings"]).read_text())
            self.assertEqual(settings["captureEncode"], "dmabuf")
            self.assertEqual(settings["p2pWifiInterface"], "auto")
            self.assertEqual(settings["vaapiQuality"], "5")
            self.assertEqual(settings["vbvMultiplier"], "0.5")
            self.assertIs(settings["p2pQuietCsa"], False)
            env = Path(applied["state_env"]).read_text()
            self.assertIn("FLUXCAST_WFD_CAPTURE_ENCODE_PREF=dmabuf", env)
            self.assertIn("FLUXCAST_WFD_VAAPI_QUALITY=5", env)
            self.assertIn("FLUXCAST_WFD_INTERFACE=wlan1", env)
            self.assertEqual(
                Path(applied["capture_encode_file"]).read_text().strip(),
                "dmabuf",
            )

    def test_refine_from_live_prefers_dmabuf_on_high_pipe_cpu(self):
        eng = self.mod.EngineProbe(
            dmabuf=True, vaapi_pipe=True, cpu=True,
            ffmpeg="/usr/bin/ffmpeg", wf_recorder="/usr/bin/wf-recorder",
            vaapi_device=True,
        )
        live = self.mod.LiveSample(
            ok=True,
            skipped=False,
            reason="ok",
            seconds=25,
            p2p_iface="p2p-wlp0s20-0",
            tx_mbps_mean=12.0,
            tx_cv=0.05,
            retry_per_s=0.2,
            wf_cpu_mean=35.0,
            ffmpeg_cpu_mean=8.0,
            current_capture_path="pipe",
            current_capture_encode="vaapi",
        )
        enc, csa, notes = self.mod.refine_from_live("vaapi", eng, live, False)
        self.assertEqual(enc, "dmabuf")
        self.assertFalse(csa)
        self.assertTrue(any("prefer dmabuf" in n for n in notes))

    def test_refine_from_live_keeps_scc_on_high_retries(self):
        eng = self.mod.EngineProbe(
            dmabuf=True, vaapi_pipe=True, cpu=True,
            ffmpeg="/usr/bin/ffmpeg", wf_recorder="/usr/bin/wf-recorder",
            vaapi_device=True,
        )
        live = self.mod.LiveSample(
            ok=True,
            skipped=False,
            reason="ok",
            seconds=25,
            p2p_iface="p2p-wlp0s20-0",
            tx_mbps_mean=11.0,
            tx_cv=0.2,
            retry_per_s=5.0,
            wf_cpu_mean=5.0,
            current_capture_path="dmabuf",
            current_capture_encode="dmabuf",
        )
        enc, csa, notes = self.mod.refine_from_live("dmabuf", eng, live, True)
        self.assertEqual(enc, "dmabuf")
        self.assertFalse(csa)
        self.assertTrue(any("SCC" in n for n in notes))


if __name__ == "__main__":
    unittest.main()

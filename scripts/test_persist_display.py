#!/usr/bin/env python3
"""Unit tests for Persist display / persistent-miracast naming helpers."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTL = ROOT / "bin" / "miracast-ctl"


def _run_ctl(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        ["bash", str(CTL), *args],
        capture_output=True,
        text=True,
        env=e,
        timeout=30,
        check=False,
    )


class PersistDisplayCtlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        xdg_config = self.home / ".config"
        xdg_state = self.home / ".local" / "state"
        xdg_config.mkdir(parents=True)
        xdg_state.mkdir(parents=True)
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_STATE_HOME": str(xdg_state),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _settings_path(self) -> Path:
        return self.home / ".config" / "omarchy-miracast" / "settings.json"

    def test_set_persist_display_writes_bool(self):
        r = _run_ctl("set-persist-display", "false", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(data.get("ok"))
        self.assertIs(data.get("preserveDisplayAcrossMonitors"), False)
        on_disk = json.loads(self._settings_path().read_text())
        self.assertIs(on_disk.get("preserveDisplayAcrossMonitors"), False)

        r = _run_ctl("set-preserve-display", "true", env=self.env)  # alias
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        data = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIs(data.get("preserveDisplayAcrossMonitors"), True)

    def test_help_lists_persist_alias(self):
        r = _run_ctl("help", env=self.env)
        text = r.stdout + r.stderr
        self.assertIn("set-persist-display", text)
        self.assertIn("set-preserve-display", text)
        self.assertIn("persistent-miracast", text)

    def test_shared_extend_name_constant(self):
        text = CTL.read_text(encoding="utf-8")
        self.assertRegex(text, r'SHARED_EXTEND_NAME="persistent-miracast"')
        self.assertRegex(text, r'LEGACY_SHARED_EXTEND_NAME="Miracast"')


class PersistNameLogicTest(unittest.TestCase):
    """Pure helpers mirroring ensure_extend_monitor naming rules."""

    SHARED = "persistent-miracast"

    @staticmethod
    def sanitize(peer_name: str, peer_mac: str = "") -> str:
        raw = (peer_name or "").strip()
        mac = (peer_mac or "").replace(":", "").replace("-", "").upper()
        s = re.sub(r"\s+", "-", raw)
        s = re.sub(r"[^A-Za-z0-9._+-]", "", s)
        s = s.strip("._-")[:48]
        if not s:
            tail = mac[-6:] if len(mac) >= 6 else (mac or "sink")
            s = f"Miracast-{tail}"
        return s

    def desired(self, persist: bool, peer_name: str, peer_mac: str = "") -> str:
        if persist:
            return self.SHARED
        return self.sanitize(peer_name, peer_mac)

    def test_persist_uses_shared_name(self):
        self.assertEqual(self.desired(True, "[LG]"), "persistent-miracast")
        self.assertEqual(self.desired(True, "hotyeah-AABB12_P2P"), "persistent-miracast")

    def test_fresh_uses_peer_name(self):
        self.assertEqual(self.desired(False, "[LG]"), "LG")
        self.assertEqual(self.desired(False, "hotyeah TV"), "hotyeah-TV")


class MonitorStateVirtualTest(unittest.TestCase):
    def test_monitor_state_marks_persistent_miracast(self):
        script = ROOT / "bin" / "monitor-state"
        text = script.read_text(encoding="utf-8")
        self.assertIn('persistent-miracast', text)
        self.assertIn('n == "persistent-miracast"', text)


if __name__ == "__main__":
    unittest.main()

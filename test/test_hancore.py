#!/usr/bin/env python3
"""HANCORE 0.1.7 unit-ish checks. Run from the plugin root:

    python3 test/test_hancore.py
"""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import safe_io  # noqa: E402


class DescriptorWalkTests(unittest.TestCase):
    def test_symlink_ancestor_refused_for_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real)
            path = str(link / "omarchy" / "workspace-shift.json")
            with self.assertRaises(OSError) as ctx:
                safe_io.write_text(path, '{"labels":{}}\n', max_bytes=safe_io.CONFIG_MAX_BYTES)
            self.assertEqual(ctx.exception.errno, errno.ELOOP)

    def test_symlink_parent_refused_for_config_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            target = real / "workspace-shift.json"
            target.write_text("{}\n")
            os.chmod(target, 0o600)
            link = Path(tmp) / "link"
            link.symlink_to(real)
            path = str(link / "workspace-shift.json")
            with self.assertRaises(OSError) as ctx:
                safe_io.read_text(path, safe_io.CONFIG_MAX_BYTES)
            self.assertEqual(ctx.exception.errno, errno.ELOOP)


class IdentityRecheckTests(unittest.TestCase):
    def test_identity_mismatch_fails_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "omarchy" / "workspace-shift.json")
            original = safe_io.dump_config({"labels": {"1": "Personal", "2": "Company"}})
            safe_io.write_text(path, original, max_bytes=safe_io.CONFIG_MAX_BYTES)

            hijack = b'{"labels":{"9":"hijacked"}}\n'

            def transform(_text: str) -> str:
                fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
                try:
                    os.write(fd, hijack)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return safe_io.dump_config({"labels": {"1": "mutated"}})

            with self.assertRaises(OSError) as ctx:
                safe_io.rmw_text(path, transform, max_bytes=safe_io.CONFIG_MAX_BYTES)
            self.assertEqual(ctx.exception.errno, errno.ESTALE)
            on_disk = Path(path).read_bytes()
            self.assertEqual(on_disk, hijack)


class OversizeConfigTests(unittest.TestCase):
    def test_oversize_rejected_before_payload_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "omarchy" / "workspace-shift.json")
            Path(path).parent.mkdir(parents=True)
            payload = b"{" + (b"a" * (safe_io.CONFIG_MAX_BYTES + 8)) + b"}\n"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)

            with self.assertRaises(OSError) as ctx:
                safe_io.read_bytes(path, safe_io.CONFIG_MAX_BYTES)
            self.assertEqual(ctx.exception.errno, errno.EFBIG)

            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "safe_io.py"), "read", path, "--max-bytes", "65536"],
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 3)
            self.assertEqual(proc.stdout, b"")
            self.assertIn(b"exceeds", proc.stderr)


class ApplyBindsTests(unittest.TestCase):
    USER_LUA = """\
-- user comment mentioning omarchy-workspace-shift should survive
o.bind("SUPER + X", "mine", "echo omarchy-workspace-shift")
hl.bind("keep-me")

-- BEGIN other.plugin
o.bind("SUPER + Y", "other", "/opt/other/script")
-- END other.plugin
"""

    def test_apply_binds_does_not_delete_unrelated_lua(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindings = Path(tmp) / "hypr" / "bindings.lua"
            bindings.parent.mkdir()
            bindings.write_text(self.USER_LUA)
            os.chmod(bindings, 0o600)
            config = Path(tmp) / "omarchy" / "workspace-shift.json"
            env = os.environ.copy()
            env["WORKSPACE_SHIFT_BINDINGS"] = str(bindings)
            env["WORKSPACE_SHIFT_CONFIG"] = str(config)
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "apply-binds"), "--no-reload"],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            text = bindings.read_text()
            self.assertIn("-- user comment mentioning omarchy-workspace-shift should survive", text)
            self.assertIn('o.bind("SUPER + X", "mine", "echo omarchy-workspace-shift")', text)
            self.assertIn('hl.bind("keep-me")', text)
            self.assertIn("-- BEGIN other.plugin", text)
            self.assertIn('o.bind("SUPER + Y", "other", "/opt/other/script")', text)
            self.assertIn("-- BEGIN io.github.danihenrique.workspace-shift", text)
            self.assertIn("-- END io.github.danihenrique.workspace-shift", text)
            self.assertNotIn("Shift whole workspace left/right", text)


class WriteReplaceSmokeTests(unittest.TestCase):
    def test_roundtrip_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "omarchy" / "workspace-shift.json")
            data = {"labels": {"1": "Personal", "2": "Company"}}
            safe_io.save_config(data, path)
            loaded = safe_io.load_config(path)
            self.assertEqual(loaded["labels"]["1"], "Personal")
            self.assertEqual(loaded["labels"]["2"], "Company")
            st = os.stat(path)
            self.assertTrue(stat.S_ISREG(st.st_mode))
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()

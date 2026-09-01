#!/usr/bin/env python3
"""Secure read/replace for Workspace Shift owned files.

Used by apply-binds (bindings.lua, workspace-shift.json) and by
workspace-shift for label swaps. Refuses symlinks/FIFOs, checks owner,
locks, writes via an unpredictable same-directory temp, preserves mode.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from typing import Any

DEFAULT_MODE = 0o600
BINDINGS_MAX_BYTES = 1 * 1024 * 1024
CONFIG_MAX_BYTES = 64 * 1024
MAX_LABEL_KEYS = 32
MAX_LABEL_CHARS = 64
MAX_SHORTCUT_CHARS = 128
DEFAULT_LEFT = "SUPER + SHIFT + comma"
DEFAULT_RIGHT = "SUPER + SHIFT + period"
CONFIG_DEFAULT = os.path.expanduser("~/.config/omarchy/workspace-shift.json")


def _cloexec_nofollow(base: int) -> int:
    flags = base | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_parent_dir(path: str) -> tuple[int, str]:
    parent = os.path.dirname(os.path.abspath(path))
    flags = _cloexec_nofollow(os.O_RDONLY | os.O_DIRECTORY)
    try:
        dirfd = os.open(parent, flags)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise OSError(errno.ELOOP, f"parent directory is a symlink or not a dir: {parent}") from exc
        raise
    st = os.fstat(dirfd)
    if not stat.S_ISDIR(st.st_mode):
        os.close(dirfd)
        raise OSError(errno.ENOTDIR, f"parent is not a directory: {parent}")
    if st.st_uid != os.geteuid():
        os.close(dirfd)
        raise OSError(errno.EPERM, f"parent directory not owned by current user: {parent}")
    return dirfd, parent


def _check_regular_owned(fd: int, path: str) -> os.stat_result:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EPERM, f"not a regular file: {path}")
    if st.st_uid != os.geteuid():
        raise OSError(errno.EPERM, f"file not owned by current user: {path}")
    return st


def _open_lock(dirfd: int, name: str) -> int:
    lock_name = f".{name}.lock"
    flags = _cloexec_nofollow(os.O_RDWR | os.O_CREAT)
    try:
        lock_fd = os.open(lock_name, flags, DEFAULT_MODE, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO):
            raise OSError(errno.EPERM, f"refusing lock path that is not a regular file: {lock_name}") from exc
        raise
    try:
        st = os.fstat(lock_fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EPERM, f"lock path is not a regular file: {lock_name}")
        if st.st_uid != os.geteuid():
            raise OSError(errno.EPERM, f"lock file not owned by current user: {lock_name}")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except Exception:
        os.close(lock_fd)
        raise
    return lock_fd


def _open_target(dirfd: int, name: str, path: str, *, write: bool) -> int | None:
    acc = os.O_RDWR if write else os.O_RDONLY
    flags = _cloexec_nofollow(acc)
    try:
        fd = os.open(name, flags, dir_fd=dirfd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO, errno.EEXIST):
            raise OSError(errno.EPERM, f"refusing non-regular or symlink target: {path}") from exc
        raise
    try:
        _check_regular_owned(fd, path)
    except Exception:
        os.close(fd)
        raise
    return fd


def read_bytes(path: str, max_bytes: int) -> bytes:
    dirfd, _parent = _open_parent_dir(path)
    try:
        name = os.path.basename(path)
        lock_fd = _open_lock(dirfd, name)
        try:
            fd = _open_target(dirfd, name, path, write=False)
            if fd is None:
                return b""
            try:
                st = os.fstat(fd)
                if st.st_size > max_bytes:
                    raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes: {path}")
                data = os.read(fd, max_bytes + 1)
                if len(data) > max_bytes:
                    raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes: {path}")
                return data
            finally:
                os.close(fd)
        finally:
            os.close(lock_fd)
    finally:
        os.close(dirfd)


def read_text(path: str, max_bytes: int) -> str:
    return read_bytes(path, max_bytes).decode("utf-8")


def _write_replace_locked(
    dirfd: int,
    parent: str,
    name: str,
    path: str,
    data: bytes,
    prev_mode: int,
) -> None:
    fd, tmp_path = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=parent)
    tmp_name = os.path.basename(tmp_path)
    try:
        tmp_st = os.fstat(fd)
        if not stat.S_ISREG(tmp_st.st_mode):
            raise OSError(errno.EPERM, "temp file is not regular")
        parent_st = os.fstat(dirfd)
        tmp_dir_st = os.stat(os.path.dirname(os.path.abspath(tmp_path)), follow_symlinks=False)
        if (tmp_dir_st.st_dev, tmp_dir_st.st_ino) != (parent_st.st_dev, parent_st.st_ino):
            raise OSError(errno.EPERM, "temp file is not in the target directory")
        os.fchmod(fd, prev_mode)
        view = memoryview(data)
        written = 0
        while written < len(data):
            n = os.write(fd, view[written:])
            if n <= 0:
                raise OSError(errno.EIO, "short write")
            written += n
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_name, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        os.fsync(dirfd)
        tmp_path = ""
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


def write_bytes(path: str, data: bytes, max_bytes: int | None = None, default_mode: int = DEFAULT_MODE) -> None:
    if max_bytes is not None and len(data) > max_bytes:
        raise OSError(errno.EFBIG, f"payload exceeds {max_bytes} bytes")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    dirfd, parent = _open_parent_dir(path)
    try:
        name = os.path.basename(path)
        lock_fd = _open_lock(dirfd, name)
        try:
            exist_fd = _open_target(dirfd, name, path, write=True)
            try:
                if exist_fd is not None:
                    prev_mode = stat.S_IMODE(os.fstat(exist_fd).st_mode)
                else:
                    prev_mode = default_mode
                _write_replace_locked(dirfd, parent, name, path, data, prev_mode)
            finally:
                if exist_fd is not None:
                    os.close(exist_fd)
        finally:
            os.close(lock_fd)
    finally:
        os.close(dirfd)


def write_text(path: str, text: str, max_bytes: int | None = None, default_mode: int = DEFAULT_MODE) -> None:
    write_bytes(path, text.encode("utf-8"), max_bytes=max_bytes, default_mode=default_mode)


def rmw_text(
    path: str,
    transform: Callable[[str], str],
    max_bytes: int,
    default_mode: int = DEFAULT_MODE,
    *,
    missing_ok: bool = False,
    skip_if_missing: bool = False,
) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    dirfd, parent = _open_parent_dir(path)
    try:
        name = os.path.basename(path)
        lock_fd = _open_lock(dirfd, name)
        try:
            exist_fd = _open_target(dirfd, name, path, write=True)
            if exist_fd is None:
                if skip_if_missing:
                    return
                if not missing_ok:
                    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
                original = ""
                prev_mode = default_mode
            else:
                try:
                    st = os.fstat(exist_fd)
                    if st.st_size > max_bytes:
                        raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes: {path}")
                    raw = os.read(exist_fd, max_bytes + 1)
                    if len(raw) > max_bytes:
                        raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes: {path}")
                    original = raw.decode("utf-8")
                    prev_mode = stat.S_IMODE(st.st_mode)
                finally:
                    os.close(exist_fd)
                    exist_fd = None
            updated = transform(original)
            if not updated.endswith("\n"):
                updated += "\n"
            payload = updated.encode("utf-8")
            if len(payload) > max_bytes:
                raise OSError(errno.EFBIG, f"payload exceeds {max_bytes} bytes")
            _write_replace_locked(dirfd, parent, name, path, payload, prev_mode)
        finally:
            os.close(lock_fd)
    finally:
        os.close(dirfd)


def valid_workspace_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        n = value
    else:
        text = str(value).strip()
        if not text.isdigit():
            return None
        n = int(text)
    if n < 1 or n > 10:
        return None
    return str(n)


def sanitize_shortcut(value: Any, default: str) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    if not text or len(text) > MAX_SHORTCUT_CHARS:
        return default
    if any(ch in text for ch in "\n\r\0"):
        return default
    return text


def sanitize_label_text(value: Any) -> str:
    text = str(value) if value is not None else ""
    text = text.strip()
    if any(ch in text for ch in "\n\r\0"):
        text = "".join(ch for ch in text if ch not in "\n\r\0").strip()
    if len(text) > MAX_LABEL_CHARS:
        text = text[:MAX_LABEL_CHARS]
    return text


def sanitize_config(data: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "labels": {},
        "shortcutLeft": DEFAULT_LEFT,
        "shortcutRight": DEFAULT_RIGHT,
    }
    if not isinstance(data, dict):
        return out
    labels = data.get("labels")
    if isinstance(labels, dict):
        count = 0
        for key, value in labels.items():
            if count >= MAX_LABEL_KEYS:
                break
            ws = valid_workspace_id(key)
            if ws is None:
                continue
            label = sanitize_label_text(value)
            if not label:
                continue
            out["labels"][ws] = label
            count += 1
    out["shortcutLeft"] = sanitize_shortcut(data.get("shortcutLeft"), DEFAULT_LEFT)
    out["shortcutRight"] = sanitize_shortcut(data.get("shortcutRight"), DEFAULT_RIGHT)
    return out


def parse_config_text(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        return sanitize_config({})
    if len(text.encode("utf-8")) > CONFIG_MAX_BYTES:
        raise OSError(errno.EFBIG, "config exceeds size limit")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid config JSON") from exc
    return sanitize_config(parsed)


def dump_config(data: dict[str, Any]) -> str:
    clean = sanitize_config(data)
    return json.dumps(clean, indent=2) + "\n"


def load_config(path: str = CONFIG_DEFAULT) -> dict[str, Any]:
    try:
        text = read_text(path, CONFIG_MAX_BYTES)
    except FileNotFoundError:
        return sanitize_config({})
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return sanitize_config({})
        raise
    if not text:
        return sanitize_config({})
    try:
        return parse_config_text(text)
    except ValueError:
        return sanitize_config({})


def save_config(data: dict[str, Any], path: str = CONFIG_DEFAULT) -> None:
    write_text(path, dump_config(data), max_bytes=CONFIG_MAX_BYTES)


def swap_labels_in_config(src: str, dest: str, path: str = CONFIG_DEFAULT) -> None:
    a = valid_workspace_id(src)
    b = valid_workspace_id(dest)
    if a is None or b is None:
        raise ValueError("workspace ids must be 1..10")
    if a == b:
        return

    def transform(text: str) -> str:
        data = parse_config_text(text) if text.strip() else sanitize_config({})
        labels = data["labels"]
        la = labels.get(a)
        lb = labels.get(b)
        if lb:
            labels[a] = lb
        else:
            labels.pop(a, None)
        if la:
            labels[b] = la
        else:
            labels.pop(b, None)
        data["labels"] = labels
        return dump_config(data)

    rmw_text(path, transform, max_bytes=CONFIG_MAX_BYTES, missing_ok=False, skip_if_missing=True)


def set_label_in_config(ws: str, text: str, path: str = CONFIG_DEFAULT) -> dict[str, Any]:
    ident = valid_workspace_id(ws)
    if ident is None:
        raise ValueError("workspace ids must be 1..10")
    label = sanitize_label_text(text)

    def transform(raw: str) -> str:
        data = parse_config_text(raw) if raw.strip() else sanitize_config({})
        labels = data["labels"]
        if label:
            labels[ident] = label
        else:
            labels.pop(ident, None)
        # Re-apply cardinality after edit.
        data["labels"] = labels
        return dump_config(data)

    rmw_text(path, transform, max_bytes=CONFIG_MAX_BYTES, missing_ok=True)
    return load_config(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Secure file IO for Workspace Shift")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read", help="Read a regular file to stdout")
    p_read.add_argument("path")
    p_read.add_argument("--max-bytes", type=int, default=CONFIG_MAX_BYTES)

    p_write = sub.add_parser("write", help="Replace a regular file from stdin")
    p_write.add_argument("path")
    p_write.add_argument("--max-bytes", type=int, default=CONFIG_MAX_BYTES)

    p_swap = sub.add_parser("swap-labels", help="Exchange two workspace labels in config")
    p_swap.add_argument("src")
    p_swap.add_argument("dest")
    p_swap.add_argument("--config", default=CONFIG_DEFAULT)

    p_set = sub.add_parser("set-label", help="Set or clear one workspace label")
    p_set.add_argument("ws")
    p_set.add_argument("text", nargs="?", default="")
    p_set.add_argument("--config", default=CONFIG_DEFAULT)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "read":
            sys.stdout.write(read_text(args.path, args.max_bytes))
            return 0
        if args.cmd == "write":
            payload = sys.stdin.buffer.read(args.max_bytes + 1)
            if len(payload) > args.max_bytes:
                raise OSError(errno.EFBIG, "stdin exceeds max bytes")
            write_bytes(args.path, payload, max_bytes=args.max_bytes)
            return 0
        if args.cmd == "swap-labels":
            swap_labels_in_config(args.src, args.dest, path=args.config)
            return 0
        if args.cmd == "set-label":
            set_label_in_config(args.ws, args.text, path=args.config)
            return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())

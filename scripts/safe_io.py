#!/usr/bin/env python3
"""Secure read/replace for Workspace Shift owned files.

Used by apply-binds (bindings.lua, workspace-shift.json) and by
workspace-shift for label swaps and swap.lock. Refuses symlinks/FIFOs, checks owner,
locks, writes via an unpredictable same-directory temp, preserves mode.

Every writable path is opened with a root-to-leaf openat/O_NOFOLLOW walk.
Missing dirs are created only via mkdirat on that walk — never os.makedirs.
Replace is committed only after a successful directory fsync; on post-replace
failure the previous bytes are restored. Identity (dev/ino/mtime/size/hash)
from the locked read is rechecked immediately before os.replace.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import time
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
LOCK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STATE_CREATE_TAIL = 2  # only create omarchy/workspace-shift under the XDG state base
PARENT_CREATE_TAIL = 1  # may create omarchy/ or hypr/ under ~/.config


def _config_default() -> str:
    override = os.environ.get("WORKSPACE_SHIFT_CONFIG")
    if override and os.path.isabs(override):
        return override
    return os.path.expanduser("~/.config/omarchy/workspace-shift.json")


CONFIG_DEFAULT = _config_default()


def _cloexec_nofollow(base: int) -> int:
    flags = base | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_or_mkdir_component(dirfd: int, name: str, flags: int, may_create: bool, mode: int) -> int:
    try:
        return os.open(name, flags, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise OSError(errno.ELOOP, f"refusing symlink or non-directory path component: {name}") from exc
        if exc.errno != errno.ENOENT or not may_create:
            raise
    try:
        os.mkdir(name, mode, dir_fd=dirfd)
    except FileExistsError:
        pass
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
    try:
        return os.open(name, flags, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise OSError(errno.ELOOP, f"refusing symlink or non-directory path component: {name}") from exc
        raise


def open_dir_walk(path: str, *, create_tail: int = 0, mode: int = 0o700) -> int:
    """Open a directory by walking from / with O_DIRECTORY|O_NOFOLLOW.

    A pre-positioned symlink in any component cannot be followed. Only the last
    `create_tail` components may be created, and only via mkdirat on the walk.
    The leaf directory must be owned by euid.
    """
    path = os.path.abspath(path)
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, f"path must be absolute: {path}")
    parts = [p for p in path.split(os.sep) if p]
    if not parts:
        raise OSError(errno.EINVAL, "refusing filesystem root")
    flags = _cloexec_nofollow(os.O_RDONLY | os.O_DIRECTORY)
    fd = os.open("/", flags)
    n = len(parts)
    try:
        for i, part in enumerate(parts):
            may_create = create_tail > 0 and (n - i) <= create_tail
            next_fd = _open_or_mkdir_component(fd, part, flags, may_create, mode)
            os.close(fd)
            fd = next_fd
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError(errno.ENOTDIR, f"not a directory: {path}")
        if st.st_uid != os.geteuid():
            raise OSError(errno.EPERM, f"directory not owned by current user: {path}")
        return fd
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def default_state_dir() -> str:
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and os.path.isabs(xdg):
        base = xdg
    else:
        base = os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "omarchy", "workspace-shift")


def ensure_state_dir(path: str | None = None, *, create_tail: int = STATE_CREATE_TAIL, mode: int = 0o700) -> int:
    """Open state dir with O_DIRECTORY|O_NOFOLLOW; refuse any symlink component."""
    return open_dir_walk(path or default_state_dir(), create_tail=create_tail, mode=mode)


def _parent_create_tail(parent: str) -> int:
    parent = os.path.abspath(parent)
    state = os.path.abspath(default_state_dir())
    if parent == state or parent.startswith(state + os.sep):
        return STATE_CREATE_TAIL
    return PARENT_CREATE_TAIL


def open_parent_dir(path: str, *, create: bool = False) -> tuple[int, str]:
    """Descriptor-walk the parent of `path`. Create at most the last parent component."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent in ("", os.sep):
        raise OSError(errno.EINVAL, f"refusing filesystem root as parent: {path}")
    create_tail = _parent_create_tail(parent) if create else 0
    dirfd = open_dir_walk(parent, create_tail=create_tail, mode=0o700)
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


def _valid_lock_name(name: str) -> bool:
    return bool(name) and LOCK_NAME_RE.fullmatch(name) is not None and name not in (".", "..")


def _flock_ex(fd: int, timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EINTR):
                raise
        if time.monotonic() >= deadline:
            raise TimeoutError("lock-timeout")
        time.sleep(0.05)


def open_state_lock(dirfd: int, name: str, timeout: float = 15.0) -> int:
    """Open lock with O_NOFOLLOW|O_CREAT (never O_TRUNC). Regular file, owner==euid, flock EX."""
    if not _valid_lock_name(name):
        raise OSError(errno.EINVAL, f"invalid lock name: {name}")
    flags = _cloexec_nofollow(os.O_RDWR | os.O_CREAT)
    try:
        lock_fd = os.open(name, flags, DEFAULT_MODE, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO, errno.ENOTDIR):
            raise OSError(errno.EPERM, f"refusing lock path that is not a regular file: {name}") from exc
        raise
    try:
        st = os.fstat(lock_fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EPERM, f"lock path is not a regular file: {name}")
        if st.st_uid != os.geteuid():
            raise OSError(errno.EPERM, f"lock file not owned by current user: {name}")
        _flock_ex(lock_fd, timeout)
    except Exception:
        os.close(lock_fd)
        raise
    return lock_fd


def hold_lock(state_dir: str | None = None, name: str = "swap.lock", timeout: float = 15.0) -> int:
    """Acquire the swap lock and hold it until stdin EOF. Prints 'ok' or 'error' on stdout."""
    dirfd = None
    lock_fd = None
    try:
        dirfd = ensure_state_dir(state_dir)
        lock_fd = open_state_lock(dirfd, name, timeout=timeout)
        sys.stdout.write("ok\n")
        sys.stdout.flush()
        try:
            sys.stdin.buffer.read()
        except (BrokenPipeError, OSError):
            pass
        return 0
    except TimeoutError as exc:
        sys.stdout.write("error\n")
        sys.stdout.flush()
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        sys.stdout.write("error\n")
        sys.stdout.flush()
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if dirfd is not None:
            try:
                os.close(dirfd)
            except OSError:
                pass


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


def _file_identity(st: os.stat_result, digest: bytes) -> tuple[int, int, int, int, bytes]:
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size, digest)


def _digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _open_temp(dirfd: int, name: str, mode: int) -> tuple[int, str]:
    flags = _cloexec_nofollow(os.O_RDWR | os.O_CREAT | os.O_EXCL)
    for _ in range(32):
        tmp_name = f".{name}.{os.urandom(8).hex()}.tmp"
        try:
            fd = os.open(tmp_name, flags, mode, dir_fd=dirfd)
            return fd, tmp_name
        except FileExistsError:
            continue
    raise OSError(errno.EEXIST, "could not create unique temp file")


def _write_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(data):
        n = os.write(fd, view[written:])
        if n <= 0:
            raise OSError(errno.EIO, "short write")
        written += n
    os.fsync(fd)


def _recheck_identity(
    dirfd: int,
    name: str,
    path: str,
    expected: tuple[int, int, int, int, bytes] | None,
) -> None:
    fd = _open_target(dirfd, name, path, write=False)
    try:
        if expected is None:
            if fd is not None:
                raise OSError(errno.EEXIST, f"target appeared during write: {path}")
            return
        if fd is None:
            raise OSError(errno.ENOENT, f"target disappeared during write: {path}")
        st = os.fstat(fd)
        raw = os.read(fd, expected[3] + 1)
        now = _file_identity(st, _digest(raw))
        if now != expected:
            raise OSError(errno.ESTALE, f"target changed during write: {path}")
    finally:
        if fd is not None:
            os.close(fd)


def _restore_after_replace(
    dirfd: int,
    name: str,
    prev_data: bytes | None,
    prev_mode: int,
    existed: bool,
) -> None:
    """Best-effort restore of previous target after a failed post-replace fsync."""
    if not existed:
        try:
            os.unlink(name, dir_fd=dirfd)
            os.fsync(dirfd)
        except OSError:
            pass
        return
    if prev_data is None:
        return
    fd = None
    tmp_name = ""
    try:
        fd, tmp_name = _open_temp(dirfd, name, prev_mode)
        os.fchmod(fd, prev_mode)
        _write_fd(fd, prev_data)
        os.close(fd)
        fd = -1
        os.replace(tmp_name, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        tmp_name = ""
        try:
            os.fsync(dirfd)
        except OSError:
            pass
    except OSError:
        pass
    finally:
        if fd is not None and fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                os.unlink(tmp_name, dir_fd=dirfd)
            except OSError:
                pass


def _write_replace_locked(
    dirfd: int,
    name: str,
    path: str,
    data: bytes,
    prev_mode: int,
    *,
    expected_id: tuple[int, int, int, int, bytes] | None,
    prev_data: bytes | None,
    existed: bool,
) -> None:
    fd, tmp_name = _open_temp(dirfd, name, prev_mode)
    try:
        tmp_st = os.fstat(fd)
        if not stat.S_ISREG(tmp_st.st_mode):
            raise OSError(errno.EPERM, "temp file is not regular")
        os.fchmod(fd, prev_mode)
        _write_fd(fd, data)
        os.close(fd)
        fd = -1
        _recheck_identity(dirfd, name, path, expected_id)
        os.replace(tmp_name, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
        tmp_name = ""
        try:
            os.fsync(dirfd)
        except OSError:
            _restore_after_replace(dirfd, name, prev_data, prev_mode, existed)
            raise
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                os.unlink(tmp_name, dir_fd=dirfd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _read_capped(fd: int, path: str, max_bytes: int) -> bytes:
    st = os.fstat(fd)
    if st.st_size > max_bytes:
        raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes: {path}")
    data = os.read(fd, max_bytes + 1)
    if len(data) > max_bytes:
        raise OSError(errno.EFBIG, f"file exceeds {max_bytes} bytes: {path}")
    return data


def read_bytes(path: str, max_bytes: int) -> bytes:
    dirfd, _parent = open_parent_dir(path, create=False)
    try:
        name = os.path.basename(path)
        lock_fd = _open_lock(dirfd, name)
        try:
            fd = _open_target(dirfd, name, path, write=False)
            if fd is None:
                return b""
            try:
                return _read_capped(fd, path, max_bytes)
            finally:
                os.close(fd)
        finally:
            os.close(lock_fd)
    finally:
        os.close(dirfd)


def read_text(path: str, max_bytes: int) -> str:
    return read_bytes(path, max_bytes).decode("utf-8")


def write_bytes(path: str, data: bytes, max_bytes: int | None = None, default_mode: int = DEFAULT_MODE) -> None:
    if max_bytes is not None and len(data) > max_bytes:
        raise OSError(errno.EFBIG, f"payload exceeds {max_bytes} bytes")
    dirfd, _parent = open_parent_dir(path, create=True)
    try:
        name = os.path.basename(path)
        lock_fd = _open_lock(dirfd, name)
        try:
            exist_fd = _open_target(dirfd, name, path, write=True)
            try:
                if exist_fd is not None:
                    cap = max_bytes if max_bytes is not None else os.fstat(exist_fd).st_size
                    prev_data = _read_capped(exist_fd, path, cap)
                    st = os.fstat(exist_fd)
                    prev_mode = stat.S_IMODE(st.st_mode)
                    expected_id = _file_identity(st, _digest(prev_data))
                    existed = True
                else:
                    prev_data = None
                    prev_mode = default_mode
                    expected_id = None
                    existed = False
            finally:
                if exist_fd is not None:
                    os.close(exist_fd)
            _write_replace_locked(
                dirfd,
                name,
                path,
                data,
                prev_mode,
                expected_id=expected_id,
                prev_data=prev_data,
                existed=existed,
            )
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
    dirfd, _parent = open_parent_dir(path, create=True)
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
                prev_data: bytes | None = None
                prev_mode = default_mode
                expected_id = None
                existed = False
            else:
                try:
                    prev_data = _read_capped(exist_fd, path, max_bytes)
                    st = os.fstat(exist_fd)
                    original = prev_data.decode("utf-8")
                    prev_mode = stat.S_IMODE(st.st_mode)
                    expected_id = _file_identity(st, _digest(prev_data))
                    existed = True
                finally:
                    os.close(exist_fd)
                    exist_fd = None
            updated = transform(original)
            if not updated.endswith("\n"):
                updated += "\n"
            payload = updated.encode("utf-8")
            if len(payload) > max_bytes:
                raise OSError(errno.EFBIG, f"payload exceeds {max_bytes} bytes")
            _write_replace_locked(
                dirfd,
                name,
                path,
                payload,
                prev_mode,
                expected_id=expected_id,
                prev_data=prev_data,
                existed=existed,
            )
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
        data["labels"] = labels
        return dump_config(data)

    rmw_text(path, transform, max_bytes=CONFIG_MAX_BYTES, missing_ok=True)
    return load_config(path)


def _exit_oserror(exc: OSError) -> int:
    print(str(exc), file=sys.stderr)
    if exc.errno == errno.EFBIG:
        return 3
    if exc.errno == errno.ESTALE:
        return 4
    return 1


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

    p_lock = sub.add_parser("hold-lock", help="Hold swap.lock until stdin closes (descriptor-safe)")
    p_lock.add_argument("--state-dir", default=None)
    p_lock.add_argument("--name", default="swap.lock")
    p_lock.add_argument("--timeout", type=float, default=15.0)

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
        if args.cmd == "hold-lock":
            return hold_lock(args.state_dir, args.name, args.timeout)
    except OSError as exc:
        return _exit_oserror(exc)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())

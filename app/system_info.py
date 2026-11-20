"""Best-effort CPU and memory readings without adding a dependency.

Windows uses ``GetSystemTimes`` and ``GlobalMemoryStatusEx`` through ctypes;
Linux reads ``/proc/stat`` and ``/proc/meminfo``. Anything else returns
``None`` so the UI can show "unknown" instead of an invented number.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Optional

_lock = threading.Lock()
_previous: Optional[tuple[int, int, int]] = None


def _filetime_value(low: int, high: int) -> int:
    return (high << 32) | low


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


def _windows_cpu() -> Optional[float]:
    global _previous
    idle = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None
    idle_v = _filetime_value(idle.dwLowDateTime, idle.dwHighDateTime)
    kernel_v = _filetime_value(kernel.dwLowDateTime, kernel.dwHighDateTime)
    user_v = _filetime_value(user.dwLowDateTime, user.dwHighDateTime)

    if _previous is None:
        _previous = (idle_v, kernel_v, user_v)
        return None

    prev_idle, prev_kernel, prev_user = _previous
    _previous = (idle_v, kernel_v, user_v)
    idle_delta = idle_v - prev_idle
    total_delta = (kernel_v - prev_kernel) + (user_v - prev_user)
    if total_delta <= 0:
        return None
    busy = max(0.0, min(1.0, 1.0 - idle_delta / total_delta))
    return round(busy * 100.0, 1)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def _windows_memory() -> Optional[float]:
    status = _MemoryStatus()
    status.dwLength = ctypes.sizeof(_MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
        ctypes.byref(status)
    ):
        return None
    return float(status.dwMemoryLoad)


def _linux_cpu() -> Optional[float]:
    global _previous
    try:
        with open("/proc/stat", "r", encoding="ascii") as handle:
            fields = handle.readline().split()
    except OSError:
        return None
    if not fields or fields[0] != "cpu":
        return None
    values = [int(value) for value in fields[1:8]]
    idle_v = values[3] + (values[4] if len(values) > 4 else 0)
    total_v = sum(values)
    if _previous is None:
        _previous = (idle_v, total_v, 0)
        return None
    prev_idle, prev_total, _unused = _previous
    _previous = (idle_v, total_v, 0)
    total_delta = total_v - prev_total
    if total_delta <= 0:
        return None
    busy = max(0.0, min(1.0, 1.0 - (idle_v - prev_idle) / total_delta))
    return round(busy * 100.0, 1)


def _linux_memory() -> Optional[float]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                if name in ("MemTotal", "MemAvailable"):
                    values[name] = int(rest.strip().split()[0])
    except (OSError, ValueError):
        return None
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return round(100.0 * (1.0 - available / total), 1)


def system_usage() -> tuple[Optional[float], Optional[float]]:
    """Return ``(cpu_percent, memory_percent)`` or ``(None, None)``."""
    with _lock:
        try:
            if sys.platform.startswith("win"):
                return _windows_cpu(), _windows_memory()
            if sys.platform.startswith("linux"):
                return _linux_cpu(), _linux_memory()
        except Exception:  # noqa: BLE001 - metrics must never break a snapshot
            return None, None
        return None, None

"""Conservative, bounded macOS resource checks for the dedicated photo worker.

This is a deferral policy, not a guarantee against an operating-system crash.
No process is killed here and no system setting is changed.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_SWAP_GIB = 4
MIN_FREE_PERCENT = 20
MIN_THERMAL_LIMIT = 80
SAMPLE_INTERVAL = 10


class ResourceDeferred(RuntimeError):
    """The Mac needs its resources; retry on the next scheduled run."""


def probe(*arguments: str) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True,
                            encoding="utf-8", timeout=2, check=True,
                            env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    return result.stdout


def parse_swap_gib(output: str) -> float:
    match = re.search(r"\bused\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT])\b", output)
    if not match:
        raise ValueError("Unable to read swap usage")
    return float(match[1]) * {"K": 2**-20, "M": 2**-10, "G": 1, "T": 1024}[match[2]]


def parse_free_percent(output: str) -> int:
    match = re.search(r"System-wide memory free percentage:\s*([0-9]+)%", output)
    if not match or not 0 <= int(match[1]) <= 100:
        raise ValueError("Unable to read memory availability")
    return int(match[1])


def parse_thermal(output: str) -> dict:
    limits = {key: int(value) for key, value in re.findall(
        r"\b(CPU_Speed_Limit|CPU_Scheduler_Limit|GPU_Speed_Limit|Thermal_Level)\s*=\s*([0-9]+)", output)}
    # Apple Silicon can report no recorded warning rather than numeric limits.
    if not limits and "No thermal warning level has been recorded" not in output:
        raise ValueError("Unable to read thermal status")
    return limits


def sample() -> dict:
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "allowed": False, "reason": "", "pressure_level": None,
              "free_percent": None, "swap_gib": None, "thermal": {},
              "policy": {"max_swap_gib": MAX_SWAP_GIB,
                         "min_free_percent": MIN_FREE_PERCENT,
                         "min_thermal_limit": MIN_THERMAL_LIMIT}}
    if platform.system() != "Darwin":
        result["reason"] = "Mac resource checks unavailable on this platform"
        return result
    reasons = []
    try:
        level = int(probe("/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip())
        if level not in (1, 2, 4):
            raise ValueError("Unknown macOS memory pressure level")
        result["pressure_level"] = level
        if level != 1:
            reasons.append("macOS memory pressure warning")
        result["swap_gib"] = parse_swap_gib(probe("/usr/sbin/sysctl", "vm.swapusage"))
        if result["swap_gib"] >= MAX_SWAP_GIB:
            reasons.append(f"Swap {result['swap_gib']:.2f} GiB is at/above {MAX_SWAP_GIB} GiB")
        result["free_percent"] = parse_free_percent(probe("/usr/bin/memory_pressure", "-Q"))
        if result["free_percent"] < MIN_FREE_PERCENT:
            reasons.append(f"Available memory below {MIN_FREE_PERCENT}%")
        thermal = parse_thermal(probe("/usr/bin/pmset", "-g", "therm"))
        result["thermal"] = thermal
        if any(value < MIN_THERMAL_LIMIT for key, value in thermal.items() if key.endswith("_Limit")):
            reasons.append("macOS is reducing processor performance")
        if thermal.get("Thermal_Level", 0) > 0:
            reasons.append("macOS thermal warning")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result["probe_error"] = type(exc).__name__ + ": " + str(exc)[:180]
        reasons.append("Unable to verify safe Mac resources")
    result["reason"] = "; ".join(reasons) if reasons else "Mac resource checks passed"
    result["allowed"] = not reasons
    return result


class Guard:
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        self.checked_at = None
        self.latest = None

    def check(self, *, force: bool = False) -> dict:
        now = time.monotonic()
        if force or self.checked_at is None or now - self.checked_at >= SAMPLE_INTERVAL:
            snapshot = sample()
            self._save(snapshot)
            self.latest = snapshot
            self.checked_at = time.monotonic()
        if not self.latest["allowed"]:
            raise ResourceDeferred(self.latest["reason"])
        return self.latest

    def _save(self, snapshot: dict) -> None:
        target = self.app_dir / "resources.json"
        if self.app_dir.is_symlink() or target.is_symlink():
            raise RuntimeError("Unsafe resource status path")
        self.app_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".resources-",
                                         dir=self.app_dir, delete=False) as stream:
            stage = Path(stream.name)
            try:
                json.dump(snapshot, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.replace(stage, target)
            finally:
                stage.unlink(missing_ok=True)

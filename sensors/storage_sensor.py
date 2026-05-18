"""Storage power estimation from psutil disk I/O."""

import os
import time
from typing import Dict, List

import psutil

from .base import BaseSensor, PowerReading

IDLE_POWER_W = 0.5
ACTIVE_PER_GB_S_W = 0.3


def _is_physical_disk(name: str) -> bool:
    """Check if a disk name corresponds to a physical disk (not partition)."""
    # Skip virtual devices
    if name.startswith(("loop", "ram", "dm-", "md", "zram")):
        return False

    # Check /sys/block/ for physical block devices
    blk = f"/sys/block/{name}"
    if not os.path.isdir(blk):
        return False

    # Physical disks have a 'device' symlink (to PCI device); partitions don't.
    return os.path.islink(f"{blk}/device")


class StorageSensor(BaseSensor):
    """Estimates storage power from disk I/O — one reading per physical disk."""

    def __init__(self):
        self._last_timestamp = 0.0
        self._last_read: Dict[str, int] = {}
        self._last_write: Dict[str, int] = {}
        self._cumulative_wh: Dict[str, float] = {}

    def available(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "Storage"

    def read(self) -> List[PowerReading]:
        now = time.time()
        dt = (now - self._last_timestamp) if self._last_timestamp > 0 else 0.0
        self._last_timestamp = now

        readings = []
        try:
            disk_ios = psutil.disk_io_counters(perdisk=True)
            if not disk_ios:
                return self._fallback(now, dt)

            for name, io in disk_ios.items():
                if not _is_physical_disk(name):
                    continue

                prev_r = self._last_read.get(name, 0)
                prev_w = self._last_write.get(name, 0)
                rd = io.read_bytes - prev_r
                wt = io.write_bytes - prev_w
                self._last_read[name] = io.read_bytes
                self._last_write[name] = io.write_bytes

                rate = (rd + wt) / (dt * (1024 ** 3)) if dt > 0 else 0.0
                watts = IDLE_POWER_W + rate * ACTIVE_PER_GB_S_W

                if name not in self._cumulative_wh:
                    self._cumulative_wh[name] = 0.0
                if dt > 0:
                    self._cumulative_wh[name] += watts * (dt / 3600.0)

                readings.append(PowerReading(
                    component=f"Storage ({name})",
                    power_watts=watts,
                    energy_wh=self._cumulative_wh[name],
                    timestamp=now,
                ))
        except Exception:
            pass

        if not readings:
            return self._fallback(now, dt)
        return readings

    def _fallback(self, now: float, dt: float) -> List[PowerReading]:
        if "total" not in self._cumulative_wh:
            self._cumulative_wh["total"] = 0.0
        if dt > 0:
            self._cumulative_wh["total"] += IDLE_POWER_W * (dt / 3600.0)
        return [PowerReading(
            component="Storage (est.)",
            power_watts=IDLE_POWER_W,
            energy_wh=self._cumulative_wh["total"],
            timestamp=now,
        )]

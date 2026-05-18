"""DRAM power estimation from psutil virtual_memory."""

import time
import psutil

from .base import BaseSensor, PowerReading

IDLE_PER_DIMM_W = 2.5
ACTIVE_PER_GB_W = 0.15


class DRAMSensor(BaseSensor):
    """Estimates DRAM power from virtual memory stats."""

    def __init__(self):
        self._cumulative_wh = 0.0
        self._last_timestamp = 0.0
        self._current_watts = 0.0
        self._total_gb = psutil.virtual_memory().total / (1024 ** 3)
        common_sizes = [32.0, 16.0, 8.0]
        self._num_dimms = 2
        for sz in common_sizes:
            if abs(self._total_gb % sz) < 0.1 and self._total_gb / sz <= 4:
                self._num_dimms = int(round(self._total_gb / sz))
                break

    def available(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"DRAM ({self._total_gb:.0f}GB)"

    def read(self) -> PowerReading:
        now = time.time()
        dt = (now - self._last_timestamp) if self._last_timestamp > 0 else 0.0
        self._last_timestamp = now

        used_gb = psutil.virtual_memory().used / (1024 ** 3)
        if self._num_dimms > 0:
            per_dimm = used_gb / self._num_dimms
            self._current_watts = self._num_dimms * (IDLE_PER_DIMM_W + per_dimm * ACTIVE_PER_GB_W)

        if dt > 0:
            self._cumulative_wh += self._current_watts * (dt / 3600.0)

        return PowerReading(
            component=self.name,
            power_watts=self._current_watts,
            energy_wh=self._cumulative_wh,
            timestamp=now,
        )

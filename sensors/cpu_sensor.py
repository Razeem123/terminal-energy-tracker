"""AMD CPU power sensor via RAPL powercap interface."""

import glob
import os
import time
from typing import Optional

from .base import BaseSensor, PowerReading


class AMDRAPLSensor(BaseSensor):
    """Reads CPU power from AMD RAPL powercap zone."""

    TDP_WATTS = 105.0
    IDLE_WATTS = 15.0

    def __init__(self):
        self._energy_path = None
        self._last_joules = None
        self._last_timestamp = None
        self._cumulative_wh = 0.0
        self._current_watts = 0.0
        self._temp_path = None
        self._has_rapl = self._find_rapl_path()
        self._has_temp = self._find_temp_path()

    def _find_rapl_path(self) -> bool:
        patterns = [
            "/sys/class/powercap/amd-rapl-*/amd-rapl-*/energy_uj",
            "/sys/class/powercap/amd-rapl-*/energy_uj",
            "/sys/class/powercap/intel-rapl-*/intel-rapl-*/energy_uj",
            "/sys/class/powercap/intel-rapl-*/energy_uj",
            "/sys/devices/virtual/powercap/intel-rapl/intel-rapl:0/energy_uj",
        ]
        for pattern in patterns:
            matches = glob.glob(pattern)
            if matches:
                best = None
                best_max = 0
                for m in matches:
                    base = os.path.dirname(m)
                    max_file = os.path.join(base, "max_energy_range_uj")
                    if not os.path.exists(max_file):
                        max_file = os.path.join(os.path.dirname(base), "max_energy_range_uj")
                    if os.path.exists(max_file):
                        try:
                            with open(max_file) as f:
                                max_val = int(f.read().strip())
                            if max_val > best_max:
                                best_max = max_val
                                best = m
                        except (ValueError, IOError):
                            continue
                if best and os.access(best, os.R_OK):
                    self._energy_path = best
                    return True
        return False

    def _find_temp_path(self) -> bool:
        matches = glob.glob("/sys/class/hwmon/hwmon*/name")
        for m in matches:
            try:
                with open(m) as f:
                    if f.read().strip() == "k10temp":
                        base = os.path.dirname(m)
                        temp_file = os.path.join(base, "temp1_input")
                        if os.path.exists(temp_file):
                            self._temp_path = temp_file
                            return True
            except (ValueError, IOError):
                continue
        return False

    def available(self) -> bool:
        """CPU sensor is always available — RAPL if present, temperature-estimation otherwise."""
        return True

    @property
    def name(self) -> str:
        if self._energy_path:
            return "CPU (RAPL)"
        return "CPU (Est.)"

    def read(self) -> PowerReading:
        now = time.time()

        if self._energy_path and os.path.exists(self._energy_path):
            try:
                with open(self._energy_path) as f:
                    uj = float(f.read().strip())
                    joules = uj / 1_000_000.0
            except (PermissionError, ValueError, IOError):
                self._energy_path = None
                return self._fallback_read(now)

            if self._last_joules is not None and self._last_timestamp is not None:
                dt_s = now - self._last_timestamp
                dt_h = dt_s / 3600.0
                delta_j = joules - self._last_joules
                if dt_s > 0 and delta_j >= 0:
                    self._current_watts = min(delta_j / dt_s, self.TDP_WATTS)
                    self._cumulative_wh += self._current_watts * dt_h
                else:
                    self._energy_path = None
                    return self._fallback_read(now)
                self._last_joules = joules
            else:
                self._last_joules = joules

            return PowerReading(
                component=self.name,
                power_watts=self._current_watts,
                energy_wh=self._cumulative_wh,
                timestamp=now,
            )
        else:
            return self._fallback_read(now)

    def _fallback_read(self, now: float) -> PowerReading:
        try:
            if self._temp_path and os.path.exists(self._temp_path):
                with open(self._temp_path) as f:
                    temp_c = float(f.read().strip()) / 1000.0
                load = max(0.0, min(1.0, (temp_c - 40.0) / 50.0))
                self._current_watts = self.IDLE_WATTS + (self.TDP_WATTS - self.IDLE_WATTS) * load
            else:
                self._current_watts = 45.0
        except Exception:
            self._current_watts = 45.0

        if self._last_timestamp is not None:
            dt = now - self._last_timestamp
            self._cumulative_wh += self._current_watts * (dt / 3600.0)
        self._last_timestamp = now

        return PowerReading(
            component=self.name,
            power_watts=self._current_watts,
            energy_wh=self._cumulative_wh,
            timestamp=now,
        )

    def get_temperature(self) -> Optional[float]:
        if not self._temp_path:
            return None
        try:
            if os.path.exists(self._temp_path):
                with open(self._temp_path) as f:
                    return float(f.read().strip()) / 1000.0
        except (ValueError, IOError):
            pass
        return None

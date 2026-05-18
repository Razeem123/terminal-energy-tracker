"""NVIDIA GPU power sensor via NVML."""

import atexit
import logging
import time
import warnings
from typing import List

warnings.filterwarnings("ignore", message=".*pynvml.*deprecated.*", category=FutureWarning)

try:
    import nvidia_ml_py as pynvml
except ImportError:
    try:
        import pynvml
    except ImportError:
        pynvml = None
        logging.warning("GPU library not installed — GPU sensors unavailable. pip install nvidia-ml-py")

from .base import BaseSensor, PowerReading


class NVGpuSensor(BaseSensor):
    """Reads power from all NVIDIA GPUs via NVML."""

    def __init__(self):
        self._initialized = False
        self._gpus = []
        self._gpu_energy = []
        self._last_timestamp = 0.0

        if pynvml is not None:
            self._init_nvml()

    def _init_nvml(self):
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            self._gpus = []
            self._gpu_energy = []
            for i in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode()
                total_mem = pynvml.nvmlDeviceGetMemoryInfo(handle).total // (1024 * 1024)
                self._gpus.append({"handle": handle, "name": name, "total_mem_mb": total_mem})
                self._gpu_energy.append(0.0)
            self._initialized = True
            print(f"[gpu] NVML: {count} GPU(s) found")
            for g in self._gpus:
                print(f"[gpu]   {g['name']}")
        except Exception as e:
            print(f"[gpu] NVML init failed: {e}")
            self._initialized = False

    def available(self) -> bool:
        return self._initialized and len(self._gpus) > 0

    @property
    def name(self) -> str:
        return f"GPU ({len(self._gpus)} found)"

    def read(self) -> List[PowerReading]:
        if not self._initialized:
            self._init_nvml()
        if not self._initialized:
            return []

        now = time.time()
        dt = (now - self._last_timestamp) if self._last_timestamp > 0 else 0.0
        self._last_timestamp = now

        readings = []
        for i, gpu in enumerate(self._gpus):
            try:
                power = pynvml.nvmlDeviceGetPowerUsage(gpu["handle"]) / 1000.0
                if dt > 0:
                    self._gpu_energy[i] += power * (dt / 3600.0)

                try:
                    clock = pynvml.nvmlDeviceGetClockInfo(gpu["handle"], pynvml.NVML_CLOCK_GRAPHICS)
                except Exception:
                    clock = 0

                gpu["power_watts"] = power
                gpu["clock"] = clock
            except Exception:
                gpu["power_watts"] = 0.0
                gpu["clock"] = 0

        for i, gpu in enumerate(self._gpus):
            readings.append(PowerReading(
                component=f"GPU {i} ({gpu['name']})",
                power_watts=gpu.get("power_watts", 0.0),
                energy_wh=self._gpu_energy[i],
                timestamp=now,
            ))
        return readings

    def shutdown(self):
        """Shut down NVML — safe to call multiple times."""
        NVGpuSensor.shutdown_all()

    @classmethod
    def shutdown_all(cls):
        """Shut down NVML — safe to call multiple times."""
        if pynvml:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass

_shutdown_registered = False

def _register_atexit():
    global _shutdown_registered
    if not _shutdown_registered:
        atexit.register(NVGpuSensor.shutdown_all)
        _shutdown_registered = True

# Register once at import time
_register_atexit()

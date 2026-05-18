"""Base sensor interface and PowerReading dataclass."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Union


@dataclass
class PowerReading:
    """Single power reading for a component."""
    component: str
    power_watts: float
    energy_wh: float
    timestamp: float


class BaseSensor(ABC):
    """Abstract base for power sensors."""

    @abstractmethod
    def read(self) -> Union[PowerReading, List[PowerReading]]:
        ...

    @abstractmethod
    def available(self) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

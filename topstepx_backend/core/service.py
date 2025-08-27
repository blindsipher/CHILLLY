import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict


class Service(ABC):
    """Base class for asynchronous services with metrics support."""

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self._running = False
        self._metrics: Dict[str, Any] = {}
        self._metrics_lock = asyncio.Lock()

    @abstractmethod
    async def start(self) -> None:
        """Start the service."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the service."""
        ...

    def get_metrics(self) -> Dict[str, Any]:
        """Return service metrics including running state."""
        base = {"running": self._running}
        base.update(self._metrics)
        return base

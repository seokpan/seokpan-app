"""Read-only clock boundary shared by development memory adapters."""

from typing import Protocol


class MillisecondClock(Protocol):
    @property
    def now_ms(self) -> int: ...

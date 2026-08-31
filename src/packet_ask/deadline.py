"""stdin과 Git preflight 호출이 공유하는 monotonic absolute deadline."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Deadline:
    """여러 단계가 같은 종료 시각을 재사용하도록 한다."""

    expires_at: float

    @classmethod
    def after(cls, seconds: int | float) -> Deadline:
        return cls(time.monotonic() + max(0.0, float(seconds)))

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def bounded_expires_at(self, max_seconds: int | float) -> float:
        return min(self.expires_at, time.monotonic() + max(0.0, float(max_seconds)))

from __future__ import annotations

from dataclasses import dataclass
import logging
from time import monotonic, sleep


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VendorRatePolicy:
    min_interval_seconds: float
    cooldown_403_seconds: float = 300.0
    cooldown_429_seconds: float = 120.0
    cooldown_error_seconds: float = 60.0


@dataclass(slots=True)
class VendorHealth:
    last_request_at: float = 0.0
    cooldown_until: float = 0.0
    last_success_at: float = 0.0
    last_failure_reason: str | None = None
    request_count: int = 0
    failure_count: int = 0


DEFAULT_VENDOR_RATE_POLICIES: dict[str, VendorRatePolicy] = {
    "eastmoney-push2his": VendorRatePolicy(min_interval_seconds=2.0, cooldown_403_seconds=300.0, cooldown_429_seconds=120.0, cooldown_error_seconds=120.0),
    "eastmoney-push2": VendorRatePolicy(min_interval_seconds=4.0, cooldown_403_seconds=300.0, cooldown_429_seconds=180.0, cooldown_error_seconds=120.0),
    "tencent-finance": VendorRatePolicy(min_interval_seconds=5.0, cooldown_403_seconds=300.0, cooldown_429_seconds=120.0, cooldown_error_seconds=60.0),
    "sina-finance": VendorRatePolicy(min_interval_seconds=4.0, cooldown_403_seconds=900.0, cooldown_429_seconds=600.0, cooldown_error_seconds=120.0),
    "mootdx": VendorRatePolicy(min_interval_seconds=0.5, cooldown_403_seconds=60.0, cooldown_429_seconds=60.0, cooldown_error_seconds=10.0),
}


class VendorScheduler:
    def __init__(self, policies: dict[str, VendorRatePolicy] | None = None) -> None:
        self._policies = policies or DEFAULT_VENDOR_RATE_POLICIES
        self._health: dict[str, VendorHealth] = {}
        self._symbol_source_cooldown_until: dict[tuple[str, str, str], float] = {}

    def wait_for_slot(self, source: str) -> None:
        policy = self._policy_for(source)
        health = self._health_for(source)
        now = monotonic()
        if now < health.cooldown_until:
            raise RuntimeError(f"{source} is in source cooldown")
        elapsed = now - health.last_request_at
        if health.last_request_at > 0 and elapsed < policy.min_interval_seconds:
            sleep(policy.min_interval_seconds - elapsed)
        health.last_request_at = monotonic()
        health.request_count += 1

    def raise_if_symbol_source_cooldown_active(self, source: str, symbol: str, scope: str) -> None:
        key = (source, symbol, scope)
        cooldown_until = self._symbol_source_cooldown_until.get(key, 0.0)
        now = monotonic()
        if now < cooldown_until:
            raise RuntimeError(f"{source} is in symbol cooldown for {symbol}/{scope}")

    def record_success(self, source: str) -> None:
        health = self._health_for(source)
        health.last_success_at = monotonic()
        health.last_failure_reason = None
        health.failure_count = 0
        health.cooldown_until = 0.0

    def record_failure(self, source: str, *, reason: str, status_code: int | None = None) -> None:
        policy = self._policy_for(source)
        health = self._health_for(source)
        health.failure_count += 1
        health.last_failure_reason = reason
        if status_code == 403:
            cooldown_seconds = policy.cooldown_403_seconds
        elif status_code == 429:
            cooldown_seconds = policy.cooldown_429_seconds
        else:
            cooldown_seconds = min(policy.cooldown_error_seconds * health.failure_count, policy.cooldown_403_seconds)
        health.cooldown_until = monotonic() + cooldown_seconds
        logger.warning(
            "market data source cooldown applied",
            extra={"source": source, "reason": reason, "status_code": status_code, "cooldown_seconds": cooldown_seconds, "failure_count": health.failure_count},
        )

    def record_symbol_source_failure(self, source: str, symbol: str, scope: str, *, cooldown_seconds: float, reason: str) -> None:
        self._symbol_source_cooldown_until[(source, symbol, scope)] = monotonic() + cooldown_seconds
        logger.warning(
            "market data symbol-source cooldown applied",
            extra={"source": source, "symbol": symbol, "scope": scope, "reason": reason, "cooldown_seconds": cooldown_seconds},
        )

    def health_snapshot(self) -> dict[str, dict[str, object]]:
        now = monotonic()
        return {
            source: {
                "requestCount": health.request_count,
                "failureCount": health.failure_count,
                "cooldownRemainingSeconds": max(round(health.cooldown_until - now, 2), 0.0),
                "lastFailureReason": health.last_failure_reason,
                "lastSuccessAgeSeconds": round(now - health.last_success_at, 2) if health.last_success_at else None,
            }
            for source, health in self._health.items()
        }

    def _policy_for(self, source: str) -> VendorRatePolicy:
        return self._policies.get(source, VendorRatePolicy(min_interval_seconds=5.0))

    def _health_for(self, source: str) -> VendorHealth:
        return self._health.setdefault(source, VendorHealth())

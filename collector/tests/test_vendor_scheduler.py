from __future__ import annotations

from unittest.mock import patch

from collector.services import vendor_scheduler
from collector.services.vendor_scheduler import VendorRatePolicy, VendorScheduler


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_wait_for_slot_serializes_same_source() -> None:
    clock = FakeClock()
    scheduler = VendorScheduler({"eastmoney-push2his": VendorRatePolicy(min_interval_seconds=2.0)})

    with patch.object(vendor_scheduler, "monotonic", clock.monotonic), patch.object(vendor_scheduler, "sleep", clock.sleep):
        scheduler.wait_for_slot("eastmoney-push2his")
        clock.now += 0.5
        scheduler.wait_for_slot("eastmoney-push2his")

    assert clock.slept == [1.5]
    assert scheduler.health_snapshot()["eastmoney-push2his"]["requestCount"] == 2


def test_cooldowns_use_status_specific_policy() -> None:
    clock = FakeClock()
    scheduler = VendorScheduler(
        {
            "eastmoney-push2his": VendorRatePolicy(
                min_interval_seconds=2.0,
                cooldown_403_seconds=300.0,
                cooldown_429_seconds=120.0,
                cooldown_error_seconds=60.0,
            )
        }
    )

    with patch.object(vendor_scheduler, "monotonic", clock.monotonic), patch.object(vendor_scheduler, "sleep", clock.sleep):
        scheduler.record_failure("eastmoney-push2his", reason="rate_limited", status_code=429)
        snapshot = scheduler.health_snapshot()["eastmoney-push2his"]
        assert snapshot["cooldownRemainingSeconds"] == 120.0
        assert snapshot["failureCount"] == 1

        try:
            scheduler.wait_for_slot("eastmoney-push2his")
        except RuntimeError as exc:
            assert "source cooldown" in str(exc)
        else:
            raise AssertionError("expected source cooldown")


def test_symbol_source_cooldown_is_scoped() -> None:
    clock = FakeClock()
    scheduler = VendorScheduler({"mootdx": VendorRatePolicy(min_interval_seconds=0.5)})

    with patch.object(vendor_scheduler, "monotonic", clock.monotonic):
        scheduler.record_symbol_source_failure("mootdx", "000001", "intraday", cooldown_seconds=30.0, reason="empty_payload")

        try:
            scheduler.raise_if_symbol_source_cooldown_active("mootdx", "000001", "intraday")
        except RuntimeError as exc:
            assert "symbol cooldown" in str(exc)
        else:
            raise AssertionError("expected symbol cooldown")

        scheduler.raise_if_symbol_source_cooldown_active("mootdx", "000002", "intraday")
        scheduler.raise_if_symbol_source_cooldown_active("mootdx", "000001", "daily")

        clock.now = 131.0
        scheduler.raise_if_symbol_source_cooldown_active("mootdx", "000001", "intraday")


def test_success_clears_source_cooldown_and_failures() -> None:
    clock = FakeClock()
    scheduler = VendorScheduler({"mootdx": VendorRatePolicy(min_interval_seconds=0.5, cooldown_error_seconds=10.0)})

    with patch.object(vendor_scheduler, "monotonic", clock.monotonic):
        scheduler.record_failure("mootdx", reason="transport_error")
        scheduler.record_success("mootdx")

        snapshot = scheduler.health_snapshot()["mootdx"]
    assert snapshot["failureCount"] == 0
    assert snapshot["cooldownRemainingSeconds"] == 0.0
    assert snapshot["lastFailureReason"] is None

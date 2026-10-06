from datetime import date

from modules.validity_r14 import (
    STATUS_ATTENTION,
    STATUS_CRITICAL,
    STATUS_EXPIRED,
    STATUS_REGULAR,
    validity_metrics,
)


def test_validity_thresholds():
    # Finestra di 90 giorni: soglia attenzione dal giorno 45, critica dal giorno 60.
    ref = "2026-01-01T09:00:00"
    expiry = "2026-04-01"

    assert validity_metrics(expiry, ref, 90, today=date(2026, 1, 30))["status"] == STATUS_REGULAR
    assert validity_metrics(expiry, ref, 90, today=date(2026, 2, 15))["status"] == STATUS_ATTENTION
    assert validity_metrics(expiry, ref, 90, today=date(2026, 3, 2))["status"] == STATUS_CRITICAL
    assert validity_metrics(expiry, ref, 90, today=date(2026, 4, 1))["status"] == STATUS_EXPIRED


def test_threshold_date_is_two_thirds():
    result = validity_metrics(
        "2026-04-01",
        "2026-01-01T09:00:00",
        90,
        today=date(2026, 1, 1),
    )
    assert result["threshold_date"] == date(2026, 3, 2)
    assert result["remaining_days"] == 90
    assert result["consumed_percent"] == 0.0


if __name__ == "__main__":
    test_validity_thresholds()
    test_threshold_date_is_two_thirds()
    print("R14 validity tests: OK")

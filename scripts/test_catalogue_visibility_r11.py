from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from modules.catalogue_visibility import (
    STATUS_ARCHIVED,
    STATUS_COLUMN,
    STATUS_HIDDEN,
    STATUS_VISIBLE,
    filter_catalogue_for_role,
    init_db,
)


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: atteso={expected!r}, ottenuto={actual!r}")


def main() -> None:
    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "r11.db"
        init_db(db_path)

        import sqlite3

        with sqlite3.connect(db_path) as conn:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(products)").fetchall()
            }

        required = {
            "catalogue_status",
            "status_reason",
            "status_updated_at",
            "status_updated_by",
        }
        missing = required - cols
        if missing:
            raise AssertionError(
                "Colonne R11 mancanti: " + ", ".join(sorted(missing))
            )

    sample = pd.DataFrame(
        [
            {"product_id": 1, STATUS_COLUMN: STATUS_VISIBLE},
            {"product_id": 2, STATUS_COLUMN: STATUS_HIDDEN},
            {"product_id": 3, STATUS_COLUMN: STATUS_ARCHIVED},
        ]
    )

    cliente = filter_catalogue_for_role(sample, "CLIENTE")
    assert_equal(cliente["product_id"].tolist(), [1], "Filtro CLIENTE")

    admin = filter_catalogue_for_role(sample, "ADMIN")
    assert_equal(admin["product_id"].tolist(), [1, 2], "Filtro ADMIN")

    buyer = filter_catalogue_for_role(sample, "BUYER")
    assert_equal(buyer["product_id"].tolist(), [1, 2], "Filtro BUYER")

    unknown = filter_catalogue_for_role(sample, "UNKNOWN")
    assert_equal(unknown["product_id"].tolist(), [1], "Filtro fail-closed")

    print("R11 catalogue visibility tests: OK")


if __name__ == "__main__":
    main()

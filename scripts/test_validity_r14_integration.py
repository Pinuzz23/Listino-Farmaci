from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import modules.catalog_db  # noqa: F401 - applica le patch R14/R14.1
import modules.order_management as order_management
import modules.validity_r14 as validity


TEST_USER = {
    "user_id": None,
    "role_id": "BUYER",
    "permissions": [
        "access_app",
        "view_catalogue",
        "publish_catalogue",
        "manage_catalogue",
        "edit_catalogue",
        "export_erp",
    ],
}


def _record(expiry: date, price: float = 10.0) -> dict:
    return {
        "Fornitore": "TEST PHARMA S.P.A.",
        "AIC": "012345678",
        "Codice Fornitore": "TEST-001",
        "Nome Commerciale": "Farmaco Test",
        "Principio Attivo": "Principio Test",
        "Forma Farmaceutica": "Compressa",
        "Materiale Pericoloso": "N",
        "Stupefacente": "No",
        "ATC7": "A01AA01",
        "ATC9": "",
        "Fala / Lasa": "N",
        "Gruppo di Stivaggio": "STD_Standard",
        "Temperatura di Stivaggio": "inferiore a + 25°C",
        "Prezzo Unitario": price,
        "Prezzo Confezione": price * 10,
        "UPC": 10,
        "Minimo Movimentabile": 1,
        "IVA": 0.10,
        "Note": "test R14.1",
        "X": 10,
        "Y": 5,
        "Z": 3,
        "Data Validità Farmaco": expiry.isoformat(),
    }


def _row(db_path: Path, query: str, params=()):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchone()


def test_publication_validity_lifecycle_and_recovery():
    order_management.current_user = lambda: TEST_USER
    validity.current_user = lambda: TEST_USER
    validity.audit = lambda *args, **kwargs: None

    with TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "listino.db"
        first_expiry = date.today() + timedelta(days=180)

        first = order_management.publish_records(
            [_record(first_expiry)],
            source_name="first.xlsx",
            db_path=db_path,
            app_version="1.14.1-test",
        )
        product = _row(
            db_path,
            """
            SELECT product_id, data_validita_farmaco, validita_riferimento_at,
                   validita_iniziale_giorni
            FROM products WHERE aic='012345678'
            """,
        )
        assert product is not None
        assert product["data_validita_farmaco"] == first_expiry.isoformat()
        first_reference = product["validita_riferimento_at"]
        assert int(product["validita_iniziale_giorni"]) > 0

        history = _row(
            db_path,
            "SELECT COUNT(*) AS n FROM product_validity_history WHERE product_id=?",
            (int(product["product_id"]),),
        )
        assert history["n"] == 1

        # Stessa validità + prezzo diverso: non deve resettare la finestra.
        order_management.publish_records(
            [_record(first_expiry, price=11.0)],
            source_name="same-validity.xlsx",
            db_path=db_path,
            app_version="1.14.1-test",
        )
        same = _row(
            db_path,
            """
            SELECT data_validita_farmaco, validita_riferimento_at
            FROM products WHERE product_id=?
            """,
            (int(product["product_id"]),),
        )
        assert same["data_validita_farmaco"] == first_expiry.isoformat()
        assert same["validita_riferimento_at"] == first_reference
        history = _row(
            db_path,
            "SELECT COUNT(*) AS n FROM product_validity_history WHERE product_id=?",
            (int(product["product_id"]),),
        )
        assert history["n"] == 1

        # Rinnovo progressivo da tracciato.
        trace_expiry = first_expiry + timedelta(days=90)
        trace = order_management.publish_records(
            [_record(trace_expiry, price=11.0)],
            source_name="trace-renewal.xlsx",
            db_path=db_path,
            app_version="1.14.1-test",
        )
        history = _row(
            db_path,
            """
            SELECT event_type, new_valid_until
            FROM product_validity_history
            WHERE product_id=?
            ORDER BY validity_history_id DESC LIMIT 1
            """,
            (int(product["product_id"]),),
        )
        assert history["event_type"] == "TRACE_RENEWAL"
        assert history["new_valid_until"] == trace_expiry.isoformat()

        # Simula il caso critico R14: core committato, persistenza validità fallisce.
        original_persist = validity._persist_publication_validity

        def fail_after_core(*args, **kwargs):
            raise RuntimeError("errore simulato persistenza validità")

        validity._persist_publication_validity = fail_after_core
        recovered_expiry = trace_expiry + timedelta(days=90)
        try:
            recovered = order_management.publish_records(
                [_record(recovered_expiry, price=12.0)],
                source_name="recovered.xlsx",
                db_path=db_path,
                app_version="1.14.1-test",
            )
        finally:
            validity._persist_publication_validity = original_persist

        assert recovered["batch_id"]
        repaired = _row(
            db_path,
            """
            SELECT data_validita_farmaco
            FROM products WHERE product_id=?
            """,
            (int(product["product_id"]),),
        )
        assert repaired["data_validita_farmaco"] == recovered_expiry.isoformat()
        repaired_history = _row(
            db_path,
            """
            SELECT COUNT(*) AS n
            FROM product_validity_history
            WHERE product_id=? AND source_batch_id=?
            """,
            (int(product["product_id"]), recovered["batch_id"]),
        )
        assert repaired_history["n"] == 1

        # Il recovery è idempotente.
        from modules.validity_reconciliation_guard import reconcile_validity_batch

        check = reconcile_validity_batch(recovered["batch_id"], db_path)
        assert check["missing_after"] == 0
        repaired_history = _row(
            db_path,
            """
            SELECT COUNT(*) AS n
            FROM product_validity_history
            WHERE product_id=? AND source_batch_id=?
            """,
            (int(product["product_id"]), recovered["batch_id"]),
        )
        assert repaired_history["n"] == 1

        # Rinnovo Buyer: nuova finestra + delta ERP UPDATE atomico.
        buyer_expiry = recovered_expiry + timedelta(days=60)
        validity.renew_product_validity(
            int(product["product_id"]),
            buyer_expiry,
            "Rinnovo test Buyer",
            db_path,
        )
        buyer = _row(
            db_path,
            """
            SELECT data_validita_farmaco
            FROM products WHERE product_id=?
            """,
            (int(product["product_id"]),),
        )
        assert buyer["data_validita_farmaco"] == buyer_expiry.isoformat()
        buyer_history = _row(
            db_path,
            """
            SELECT event_type
            FROM product_validity_history
            WHERE product_id=?
            ORDER BY validity_history_id DESC LIMIT 1
            """,
            (int(product["product_id"]),),
        )
        assert buyer_history["event_type"] == "BUYER_RENEWAL"
        delta = _row(
            db_path,
            """
            SELECT COUNT(*) AS n
            FROM erp_delta_events
            WHERE source_type='BUYER' OR source_type='ORDER_MANAGEMENT'
            """,
        )
        assert delta["n"] >= 4


if __name__ == "__main__":
    test_publication_validity_lifecycle_and_recovery()
    print("R14.1 integration tests: OK")

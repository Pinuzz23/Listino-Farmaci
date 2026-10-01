from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.db_backend import use_postgres
import modules.order_management as order_management


logger = logging.getLogger(__name__)

# R13 was deployed from this commit window. Reconciliation is deliberately
# limited to publications created from R13 onward, so historical pre-R12/R13
# catalogue loads are never backfilled into the ERP queue by accident.
R13_DELTA_GUARD_FROM = "2026-10-01T13:21:21"
_ACTIONS = {
    "NUOVO PRODOTTO": "INSERT",
    "NUOVA OFFERTA": "INSERT",
    "PREZZO MODIFICATO": "UPDATE",
    "DATI MODIFICATI": "UPDATE",
    "PREZZO + ANAGRAFICA": "UPDATE",
}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _fetch_id_maps(cur, product_keys: list[str], offer_keys: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    product_ids: dict[str, int] = {}
    offer_ids: dict[str, int] = {}

    product_keys = _dedupe(product_keys)
    offer_keys = _dedupe(offer_keys)

    if use_postgres():
        if product_keys:
            cur.execute(
                "SELECT product_key, product_id FROM products WHERE product_key = ANY(%s)",
                (product_keys,),
            )
            product_ids = {str(row[0]): int(row[1]) for row in cur.fetchall()}
        if offer_keys:
            cur.execute(
                "SELECT offer_key, offer_id FROM offers WHERE offer_key = ANY(%s)",
                (offer_keys,),
            )
            offer_ids = {str(row[0]): int(row[1]) for row in cur.fetchall()}
        return product_ids, offer_ids

    if product_keys:
        marks = ", ".join(["?"] * len(product_keys))
        cur.execute(
            f"SELECT product_key, product_id FROM products WHERE product_key IN ({marks})",
            tuple(product_keys),
        )
        product_ids = {str(row[0]): int(row[1]) for row in cur.fetchall()}
    if offer_keys:
        marks = ", ".join(["?"] * len(offer_keys))
        cur.execute(
            f"SELECT offer_key, offer_id FROM offers WHERE offer_key IN ({marks})",
            tuple(offer_keys),
        )
        offer_ids = {str(row[0]): int(row[1]) for row in cur.fetchall()}
    return product_ids, offer_ids


def reconcile_buyer_delta_batch(
    batch_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create only missing Buyer ERP events for one already-published batch.

    The event key is exactly the R12 key (BUYER|batch|offer_key), therefore the
    operation is idempotent and can safely be executed after the normal queue.
    """
    batch_id = str(batch_id or "").strip()
    if not batch_id:
        return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

    order_management.init_db(db_path)
    actor = (order_management.current_user() or {}).get("user_id")
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"

        cur.execute(
            f"SELECT status FROM publications WHERE batch_id = {ph}",
            (batch_id,),
        )
        publication = cur.fetchone()
        if not publication or str(publication[0]).upper() != "PUBBLICATO":
            return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

        cur.execute(
            f"""
            SELECT product_key, offer_key, action, row_json
            FROM publication_rows
            WHERE batch_id = {ph}
            ORDER BY publication_row_id
            """,
            (batch_id,),
        )
        source_rows = cur.fetchall()

        actionable_by_key: dict[str, tuple[str, str, str, str, str]] = {}
        for product_key, offer_key, source_action, row_json in source_rows:
            erp_action = _ACTIONS.get(str(source_action or "").strip().upper())
            if not erp_action:
                continue
            event_key = f"BUYER|{batch_id}|{offer_key}"
            actionable_by_key[event_key] = (
                event_key,
                str(product_key),
                str(offer_key),
                erp_action,
                str(row_json or "{}"),
            )

        expected = len(actionable_by_key)
        if not expected:
            return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

        cur.execute(
            f"""
            SELECT event_key
            FROM erp_delta_events
            WHERE source_type = 'BUYER'
              AND source_batch_id = {ph}
            """,
            (batch_id,),
        )
        existing_keys = {str(row[0]) for row in cur.fetchall()}
        existing_before = len(existing_keys & set(actionable_by_key))

        missing = [
            value
            for key, value in actionable_by_key.items()
            if key not in existing_keys
        ]
        if missing:
            product_ids, offer_ids = _fetch_id_maps(
                cur,
                [item[1] for item in missing],
                [item[2] for item in missing],
            )
            events = [
                (
                    event_key,
                    product_ids.get(product_key),
                    offer_ids.get(offer_key),
                    erp_action,
                    "BUYER",
                    batch_id,
                    row_json,
                    actor,
                )
                for event_key, product_key, offer_key, erp_action, row_json in missing
            ]

            if use_postgres():
                from psycopg2.extras import execute_values

                execute_values(
                    cur,
                    """
                    INSERT INTO erp_delta_events (
                        event_key, product_id, offer_id, action, source_type,
                        source_batch_id, payload_json, created_by
                    ) VALUES %s
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                    events,
                    page_size=1000,
                )
            else:
                now = datetime.now().isoformat(timespec="seconds")
                cur.executemany(
                    """
                    INSERT OR IGNORE INTO erp_delta_events (
                        event_key, product_id, offer_id, action, source_type,
                        source_batch_id, payload_json, created_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [(*event[:-1], now, event[-1]) for event in events],
                )

        conn.commit()

        cur.execute(
            f"""
            SELECT event_key
            FROM erp_delta_events
            WHERE source_type = 'BUYER'
              AND source_batch_id = {ph}
            """,
            (batch_id,),
        )
        after_keys = {str(row[0]) for row in cur.fetchall()}
        existing_after = len(after_keys & set(actionable_by_key))
        created = max(0, existing_after - existing_before)
        missing_after = max(0, expected - existing_after)

        logger.info(
            "[ERP-GUARD] batch=%s expected=%d existing_before=%d created=%d missing_after=%d",
            batch_id,
            expected,
            existing_before,
            created,
            missing_after,
        )
        return {
            "batch_id": batch_id,
            "expected": expected,
            "existing": existing_after,
            "created": created,
            "missing_after": missing_after,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _recent_missing_batches(
    db_path: str | Path | None = None,
    limit: int = 20,
) -> list[str]:
    """Return recent R13+ publications with actionable rows not fully queued."""
    order_management.init_db(db_path)
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"
        cur.execute(
            f"""
            WITH expected AS (
                SELECT batch_id, COUNT(DISTINCT offer_key) AS expected_count
                FROM publication_rows
                WHERE UPPER(TRIM(action)) IN (
                    'NUOVO PRODOTTO', 'NUOVA OFFERTA', 'PREZZO MODIFICATO',
                    'DATI MODIFICATI', 'PREZZO + ANAGRAFICA'
                )
                GROUP BY batch_id
            ), actual AS (
                SELECT source_batch_id AS batch_id, COUNT(DISTINCT event_key) AS actual_count
                FROM erp_delta_events
                WHERE source_type = 'BUYER'
                GROUP BY source_batch_id
            )
            SELECT p.batch_id
            FROM publications p
            JOIN expected e ON e.batch_id = p.batch_id
            LEFT JOIN actual a ON a.batch_id = p.batch_id
            WHERE p.status = 'PUBBLICATO'
              AND p.published_at >= {ph}
              AND e.expected_count > COALESCE(a.actual_count, 0)
            ORDER BY p.published_at DESC
            LIMIT {max(1, int(limit))}
            """,
            (R13_DELTA_GUARD_FROM,),
        )
        return [str(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()


# Capture the R13 bulk queue if R13 is active; otherwise capture the R12 queue.
_original_queue_buyer_delta = order_management._queue_buyer_delta
_original_delta_dataframe = order_management.delta_dataframe


def queue_buyer_delta_guarded(records: list[dict], result: dict, db_path=None) -> None:
    """Run the normal queue and verify/repair the just-published batch."""
    original_error: Exception | None = None
    try:
        _original_queue_buyer_delta(records, result, db_path)
    except Exception as exc:  # publication itself may already be committed
        original_error = exc
        logger.exception("[ERP-GUARD] normal Buyer delta queue failed; attempting reconciliation")

    batch_id = str(result.get("batch_id") or "")
    try:
        check = reconcile_buyer_delta_batch(batch_id, db_path)
    except Exception:
        if original_error is not None:
            raise original_error
        raise

    if check.get("missing_after", 0):
        if original_error is not None:
            raise original_error
        raise order_management.OrderManagementError(
            f"Il batch {batch_id} è stato pubblicato ma la coda ERP non è completa. "
            "Apri Export ERP e riprova dopo aver aggiornato la pagina."
        )

    if original_error is not None:
        logger.warning(
            "[ERP-GUARD] recovered Buyer delta queue for batch=%s after normal queue error",
            batch_id,
        )


def delta_dataframe_guarded(db_path: str | Path | None = None):
    """Self-heal missing R13+ Buyer deltas before rendering Export ERP."""
    if use_postgres():
        try:
            for batch_id in _recent_missing_batches(db_path):
                reconcile_buyer_delta_batch(batch_id, db_path)
        except Exception:
            # Existing ERP events must remain usable even if a repair attempt fails.
            logger.exception("[ERP-GUARD] automatic reconciliation on Export ERP failed")
    return _original_delta_dataframe(db_path)


order_management._queue_buyer_delta = queue_buyer_delta_guarded
order_management.delta_dataframe = delta_dataframe_guarded

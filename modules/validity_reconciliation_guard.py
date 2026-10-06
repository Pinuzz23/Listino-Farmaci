from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.db_backend import use_postgres
from modules.validator import parse_date
import modules.order_management as order_management
import modules.validity_r14 as validity


logger = logging.getLogger(__name__)


def _ph() -> str:
    return "%s" if use_postgres() else "?"


def _as_dict(row, columns: list[str]) -> dict[str, Any]:
    if hasattr(row, "keys"):
        return dict(row)
    return dict(zip(columns, row))


def _publication(batch_id: str, db_path=None) -> dict[str, Any] | None:
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT batch_id, published_at, status FROM publications WHERE batch_id = {_ph()}",
            (batch_id,),
        )
        row = cur.fetchone()
        return _as_dict(row, ["batch_id", "published_at", "status"]) if row else None
    finally:
        conn.close()


def _source_records(batch_id: str, db_path=None) -> dict[str, dict[str, Any]]:
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT product_key, row_json
            FROM publication_rows
            WHERE batch_id = {_ph()}
            ORDER BY publication_row_id
            """,
            (batch_id,),
        )
        latest: dict[str, dict[str, Any]] = {}
        for product_key, row_json in cur.fetchall():
            try:
                payload = json.loads(str(row_json or "{}"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if validity.VALIDITY_FIELD not in payload:
                continue
            latest[str(product_key)] = payload
        return latest
    finally:
        conn.close()


def _prior_trace_validity(cur, product_key: str, batch_id: str):
    """Ultima validità dichiarata in un batch precedente per lo stesso prodotto."""

    ph = _ph()
    cur.execute(
        f"""
        SELECT row_json
        FROM publication_rows
        WHERE product_key = {ph}
          AND publication_row_id < (
              SELECT MIN(publication_row_id)
              FROM publication_rows
              WHERE batch_id = {ph} AND product_key = {ph}
          )
        ORDER BY publication_row_id DESC
        """,
        (product_key, batch_id, product_key),
    )
    for row in cur.fetchall():
        raw = row[0] if not hasattr(row, "keys") else row["row_json"]
        try:
            payload = json.loads(str(raw or "{}"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        previous = parse_date(payload.get(validity.VALIDITY_FIELD))
        if previous is not None:
            return previous
    return None


def reconcile_validity_batch(
    batch_id: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Ricostruisce solo gli eventi validità mancanti di un batch pubblicato.

    publication_rows è la sorgente immutabile. La funzione non riduce mai la
    validità corrente di un prodotto e usa un indice univoco
    (product_id, source_batch_id), quindi può essere richiamata più volte.
    """

    batch_id = str(batch_id or "").strip()
    if not batch_id:
        return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

    validity.ensure_validity_schema(db_path)
    publication = _publication(batch_id, db_path)
    if not publication or str(publication.get("status") or "").upper() != "PUBBLICATO":
        return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

    published_at = validity._parse_publication_datetime(publication.get("published_at"))
    source_records = _source_records(batch_id, db_path)
    if not source_records:
        return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        keys = list(source_records)
        if use_postgres():
            cur.execute(
                """
                SELECT product_id, product_key, data_validita_farmaco,
                       validita_riferimento_at, validita_iniziale_giorni
                FROM products
                WHERE product_key = ANY(%s)
                """,
                (keys,),
            )
        else:
            marks = ", ".join(["?"] * len(keys))
            cur.execute(
                f"""
                SELECT product_id, product_key, data_validita_farmaco,
                       validita_riferimento_at, validita_iniziale_giorni
                FROM products
                WHERE product_key IN ({marks})
                """,
                tuple(keys),
            )

        products = {
            str(item["product_key"]): item
            for item in (
                _as_dict(
                    row,
                    [
                        "product_id", "product_key", "data_validita_farmaco",
                        "validita_riferimento_at", "validita_iniziale_giorni",
                    ],
                )
                for row in cur.fetchall()
            )
        }

        expected_ids: list[int] = []
        existing_before = 0
        actor = (order_management.current_user() or {}).get("user_id")
        ph = _ph()

        for product_key, payload in source_records.items():
            incoming = parse_date(payload.get(validity.VALIDITY_FIELD))
            product = products.get(product_key)
            if incoming is None or product is None or incoming <= published_at.date():
                continue

            product_id = int(product["product_id"])

            # Una ripubblicazione con la stessa validità NON apre una nuova
            # finestra e NON deve produrre un nuovo evento storico.
            prior_trace_validity = _prior_trace_validity(
                cur, product_key, batch_id
            )
            if prior_trace_validity == incoming:
                continue

            expected_ids.append(product_id)
            cur.execute(
                f"""
                SELECT validity_history_id
                FROM product_validity_history
                WHERE product_id = {ph} AND source_batch_id = {ph}
                LIMIT 1
                """,
                (product_id, batch_id),
            )
            if cur.fetchone():
                existing_before += 1
                continue

            current_validity = parse_date(product.get("data_validita_farmaco"))
            current_reference = validity._as_datetime(product.get("validita_riferimento_at"))
            try:
                current_initial = int(product.get("validita_iniziale_giorni"))
            except (TypeError, ValueError):
                current_initial = None

            old_for_event = prior_trace_validity
            event_type = (
                "TRACE_RENEWAL"
                if prior_trace_validity is not None
                else "FIRST_LOAD"
            )
            initial_days = (incoming - published_at.date()).days

            should_update = (
                current_validity is None
                or current_validity < incoming
                or (
                    current_validity == incoming
                    and (current_reference is None or not current_initial or current_initial <= 0)
                )
            )
            # Non riportare mai indietro una validità già rinnovata successivamente.
            if should_update and (current_validity is None or current_validity <= incoming):
                if use_postgres():
                    cur.execute(
                        """
                        UPDATE products
                        SET data_validita_farmaco = %s,
                            validita_riferimento_at = %s,
                            validita_iniziale_giorni = %s,
                            validita_updated_at = %s,
                            validita_updated_by = %s
                        WHERE product_id = %s
                        """,
                        (incoming, published_at, initial_days, published_at, actor, product_id),
                    )
                else:
                    stamp = published_at.isoformat(timespec="seconds")
                    cur.execute(
                        """
                        UPDATE products
                        SET data_validita_farmaco = ?,
                            validita_riferimento_at = ?,
                            validita_iniziale_giorni = ?,
                            validita_updated_at = ?,
                            validita_updated_by = ?
                        WHERE product_id = ?
                        """,
                        (incoming.isoformat(), stamp, initial_days, stamp, actor, product_id),
                    )

            reason = (
                "Prima immissione validità ricostruita dal batch pubblicato"
                if event_type == "FIRST_LOAD"
                else "Rinnovo validità ricostruito dal batch pubblicato"
            )
            if use_postgres():
                cur.execute(
                    """
                    INSERT INTO product_validity_history (
                        product_id, event_type, old_valid_until, new_valid_until,
                        reference_at, initial_days, reason, source_batch_id, created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        product_id, event_type, old_for_event, incoming, published_at,
                        initial_days, reason, batch_id, actor,
                    ),
                )
            else:
                stamp = published_at.isoformat(timespec="seconds")
                cur.execute(
                    """
                    INSERT OR IGNORE INTO product_validity_history (
                        product_id, event_type, old_valid_until, new_valid_until,
                        reference_at, initial_days, reason, source_batch_id,
                        created_at, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_id,
                        event_type,
                        old_for_event.isoformat() if old_for_event else None,
                        incoming.isoformat(),
                        stamp,
                        initial_days,
                        reason,
                        batch_id,
                        datetime.now().isoformat(timespec="seconds"),
                        actor,
                    ),
                )

        conn.commit()

        expected_ids = list(dict.fromkeys(expected_ids))
        if not expected_ids:
            return {"batch_id": batch_id, "expected": 0, "existing": 0, "created": 0, "missing_after": 0}

        if use_postgres():
            cur.execute(
                """
                SELECT COUNT(*)
                FROM product_validity_history
                WHERE source_batch_id = %s
                  AND product_id = ANY(%s)
                """,
                (batch_id, expected_ids),
            )
        else:
            marks = ", ".join(["?"] * len(expected_ids))
            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM product_validity_history
                WHERE source_batch_id = ?
                  AND product_id IN ({marks})
                """,
                (batch_id, *expected_ids),
            )
        existing_after = int(cur.fetchone()[0])
        result = {
            "batch_id": batch_id,
            "expected": len(expected_ids),
            "existing": existing_after,
            "created": max(0, existing_after - existing_before),
            "missing_after": max(0, len(expected_ids) - existing_after),
        }
        logger.info("[VALIDITY-GUARD] %s", result)
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def recent_missing_validity_batches(
    db_path: str | Path | None = None,
    limit: int = 20,
) -> list[str]:
    validity.ensure_validity_schema(db_path)
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        if use_postgres():
            cur.execute(
                f"""
                SELECT DISTINCT p.batch_id, p.published_at
                FROM publications p
                JOIN publication_rows pr ON pr.batch_id = p.batch_id
                JOIN products prod ON prod.product_key = pr.product_key
                LEFT JOIN product_validity_history h
                  ON h.product_id = prod.product_id
                 AND h.source_batch_id = p.batch_id
                WHERE p.status = 'PUBBLICATO'
                  AND pr.row_json LIKE '%"Data Validità Farmaco"%'
                  AND h.validity_history_id IS NULL
                ORDER BY p.published_at DESC
                LIMIT {max(1, int(limit))}
                """
            )
        else:
            cur.execute(
                f"""
                SELECT DISTINCT p.batch_id, p.published_at
                FROM publications p
                JOIN publication_rows pr ON pr.batch_id = p.batch_id
                JOIN products prod ON prod.product_key = pr.product_key
                LEFT JOIN product_validity_history h
                  ON h.product_id = prod.product_id
                 AND h.source_batch_id = p.batch_id
                WHERE p.status = 'PUBBLICATO'
                  AND pr.row_json LIKE '%"Data Validità Farmaco"%'
                  AND h.validity_history_id IS NULL
                ORDER BY p.published_at DESC
                LIMIT {max(1, int(limit))}
                """
            )
        return [str(row[0]) for row in cur.fetchall()]
    finally:
        conn.close()


def reconcile_recent_validity(
    db_path: str | Path | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    results = []
    for batch_id in recent_missing_validity_batches(db_path, limit=limit):
        results.append(reconcile_validity_batch(batch_id, db_path))
    return results


_original_publish_core = order_management._r10_publish_records
_original_catalogue_dataframe = order_management.catalogue_dataframe
_original_monitor_dataframe = validity.validity_monitor_dataframe


def publish_core_guarded(
    records: list[dict],
    source_name: str,
    db_path: str | Path | None,
    app_version: str = "demo",
    archive_path: str | None = None,
) -> dict:
    try:
        result = _original_publish_core(
            records=records,
            source_name=source_name,
            db_path=db_path,
            app_version=app_version,
            archive_path=archive_path,
        )
    except validity.ValidityPersistenceError as exc:
        result = exc.result
        check = reconcile_validity_batch(str(result.get("batch_id") or ""), db_path)
        if check.get("missing_after", 0):
            raise exc
        logger.warning(
            "[VALIDITY-GUARD] recovered batch=%s after persistence error: %s",
            result.get("batch_id"),
            exc.cause,
        )
        return result

    check = reconcile_validity_batch(str(result.get("batch_id") or ""), db_path)
    if check.get("missing_after", 0):
        raise validity.ValidityError(
            f"Il batch {result.get('batch_id')} è pubblicato ma la validità non è completa. "
            "Ricarica la pagina: il guard R14.1 tenterà una nuova riconciliazione."
        )
    return result


def catalogue_dataframe_guarded(db_path: str | Path | None = None):
    try:
        reconcile_recent_validity(db_path)
    except Exception:
        logger.exception("[VALIDITY-GUARD] riconciliazione automatica catalogo non riuscita")
    return _original_catalogue_dataframe(db_path)


def validity_monitor_dataframe_guarded(db_path: str | Path | None = None):
    try:
        reconcile_recent_validity(db_path)
    except Exception:
        logger.exception("[VALIDITY-GUARD] riconciliazione automatica monitor non riuscita")
    return _original_monitor_dataframe(db_path)


order_management._r10_publish_records = publish_core_guarded
order_management.catalogue_dataframe = catalogue_dataframe_guarded
validity.validity_monitor_dataframe = validity_monitor_dataframe_guarded

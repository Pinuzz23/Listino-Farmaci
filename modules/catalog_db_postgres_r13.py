from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from psycopg2.extras import RealDictCursor, execute_values

import modules.catalog_db_postgres as legacy
import modules.master22_catalog as master22


logger = logging.getLogger(__name__)

# R13 performance layer.
#
# The existing PostgreSQL backend remains the compatibility surface for the
# application. This module replaces only the expensive hot paths at import
# time: schema bootstrap, publication preview, publication writes and the
# Master22 wrapper. All other read/export functions continue to use the
# established backend implementation.

_legacy_init_db = legacy.init_db
_master22_init_db = master22.init_db

_SCHEMA_READY = False
_SCHEMA_LOCK = Lock()
_MASTER22_SCHEMA_READY = False
_MASTER22_SCHEMA_LOCK = Lock()


def _elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 4)


def init_db(db_path: str | Path | None = None) -> None:
    """Run the legacy DDL only once per Python process."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        started = time.perf_counter()
        _legacy_init_db(db_path)
        _SCHEMA_READY = True
        logger.info("[PERF] postgres_schema_bootstrap=%.4fs", _elapsed(started))


def master22_init_db(db_path: str | Path | None = None) -> None:
    """Run Master22 ALTER migrations only once per Python process."""
    global _MASTER22_SCHEMA_READY
    if _MASTER22_SCHEMA_READY:
        return
    with _MASTER22_SCHEMA_LOCK:
        if _MASTER22_SCHEMA_READY:
            return
        started = time.perf_counter()
        _master22_init_db(db_path)
        _MASTER22_SCHEMA_READY = True
        logger.info("[PERF] master22_schema_bootstrap=%.4fs", _elapsed(started))


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def preview_publication(
    records: list[dict],
    db_path: str | Path | None = None,
) -> dict:
    """Bulk publication preview: one product query and one offer query."""
    init_db(db_path)
    started = time.perf_counter()

    source_hash = legacy.canonical_records_hash(records)
    details: list[dict[str, Any]] = []
    counts = {
        "new_products": 0,
        "new_offers": 0,
        "price_changes": 0,
        "data_changes": 0,
        "updated_offers": 0,
        "unchanged": 0,
    }
    suppliers = sorted({
        legacy._txt(record.get("Fornitore"))
        for record in records
        if legacy._txt(record.get("Fornitore"))
    })

    prepared: list[tuple[dict, str, str, dict, dict]] = []
    product_keys: list[str] = []
    offer_keys: list[str] = []
    for record in records:
        pkey = legacy.product_key(record)
        okey = legacy.offer_key(record)
        pvals = legacy._product_values(record)
        ovals = legacy._offer_values(record)
        prepared.append((record, pkey, okey, pvals, ovals))
        product_keys.append(pkey)
        offer_keys.append(okey)

    conn = legacy._connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT batch_id, published_at, source_name
                FROM publications
                WHERE source_hash = %s
                  AND status = 'PUBBLICATO'
                """,
                (source_hash,),
            )
            previous = cur.fetchone()

            products_by_key: dict[str, dict] = {}
            unique_product_keys = _dedupe(product_keys)
            if unique_product_keys:
                cur.execute(
                    "SELECT * FROM products WHERE product_key = ANY(%s)",
                    (unique_product_keys,),
                )
                products_by_key = {
                    row["product_key"]: dict(row)
                    for row in cur.fetchall()
                }

            offers_by_key: dict[str, dict] = {}
            unique_offer_keys = _dedupe(offer_keys)
            if unique_offer_keys:
                cur.execute(
                    "SELECT * FROM offers WHERE offer_key = ANY(%s)",
                    (unique_offer_keys,),
                )
                offers_by_key = {
                    row["offer_key"]: dict(row)
                    for row in cur.fetchall()
                }
    finally:
        conn.close()

    price_fields = {
        "prezzo_unitario",
        "prezzo_confezione",
        "minimo_movimentabile",
        "iva",
    }

    for idx, (_, pkey, okey, pvals, ovals) in enumerate(prepared, start=1):
        prod = products_by_key.get(pkey)
        offer = offers_by_key.get(okey)
        pchanges = legacy._diff_fields(prod, pvals)
        ochanges = legacy._diff_fields(offer, ovals)
        price_changes = [field for field in ochanges if field in price_fields]
        commercial_meta = [field for field in ochanges if field not in price_fields]

        if prod is None:
            action = "NUOVO PRODOTTO"
            counts["new_products"] += 1
            if offer is None:
                counts["new_offers"] += 1
        elif offer is None:
            action = "NUOVA OFFERTA"
            counts["new_offers"] += 1
            if pchanges:
                counts["data_changes"] += 1
        elif price_changes and (pchanges or commercial_meta):
            action = "PREZZO + ANAGRAFICA"
            counts["price_changes"] += 1
            counts["data_changes"] += 1
            counts["updated_offers"] += 1
        elif price_changes:
            action = "PREZZO MODIFICATO"
            counts["price_changes"] += 1
            counts["updated_offers"] += 1
        elif pchanges or commercial_meta:
            action = "DATI MODIFICATI"
            counts["data_changes"] += 1
            counts["updated_offers"] += 1
        else:
            action = "INVARIATO"
            counts["unchanged"] += 1

        details.append({
            "Riga": idx,
            "AIC": pvals["aic"] or "",
            "Nome Commerciale": pvals["nome_commerciale"] or "",
            "Fornitore": ovals["fornitore"],
            "Prezzo Confezione": ovals["prezzo_confezione"],
            "Azione": action,
            "Campi prodotto variati": ", ".join(pchanges),
            "Campi offerta variati": ", ".join(ochanges),
        })

    logger.info(
        "[PERF] publication_preview rows=%d db_reads=3 elapsed=%.4fs",
        len(records),
        _elapsed(started),
    )
    return {
        "source_hash": source_hash,
        "already_published": dict(previous) if previous else None,
        "suppliers": suppliers,
        "row_count": len(records),
        "details": details,
        **counts,
    }


def _product_rows(records: list[dict], published_at: str, batch_id: str) -> list[tuple]:
    rows_by_key: dict[str, tuple] = {}
    for record in records:
        pkey = legacy.product_key(record)
        pvals = legacy._product_values(record)
        rows_by_key[pkey] = (
            pkey,
            pvals["aic"],
            pvals["nome_commerciale"],
            pvals["principio_attivo"],
            pvals["atc7"],
            pvals["atc9"],
            pvals["fala_lasa"],
            pvals["materiale_pericoloso"],
            pvals["stupefacente"],
            pvals["gruppo_stivaggio"],
            pvals["temperatura_stivaggio"],
            pvals["upc"],
            pvals["note"],
            published_at,
            published_at,
            batch_id,
        )
    return list(rows_by_key.values())


def _offer_rows(
    records: list[dict],
    product_ids: dict[str, int],
    published_at: str,
    batch_id: str,
) -> list[tuple]:
    rows_by_key: dict[str, tuple] = {}
    for record in records:
        pkey = legacy.product_key(record)
        okey = legacy.offer_key(record)
        ovals = legacy._offer_values(record)
        rows_by_key[okey] = (
            okey,
            product_ids[pkey],
            ovals["fornitore"],
            ovals["codice_fornitore"],
            ovals["prezzo_unitario"],
            ovals["prezzo_confezione"],
            ovals["minimo_movimentabile"],
            ovals["iva"],
            published_at,
            published_at,
            batch_id,
        )
    return list(rows_by_key.values())


def publish_records(
    records: list[dict],
    source_name: str,
    db_path: str | Path | None,
    app_version: str = "demo",
    archive_path: str | None = None,
    preview: dict | None = None,
) -> dict:
    """Bulk UPSERT products/offers and batch INSERT history/publication rows."""
    init_db(db_path)
    total_started = time.perf_counter()
    preview = preview or preview_publication(records, db_path)

    if preview["already_published"]:
        raise ValueError(
            "Questo dataset risulta già pubblicato nel batch "
            f"{preview['already_published']['batch_id']}."
        )

    batch_id = legacy._batch_id()
    published_at = datetime.now().isoformat(timespec="seconds")
    supplier_label = " | ".join(preview["suppliers"])

    stage_times: dict[str, float] = {}
    conn = legacy._connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            stage = time.perf_counter()
            cur.execute(
                """
                INSERT INTO publications (
                    batch_id, source_name, source_hash, supplier, published_at,
                    row_count, new_products, new_offers, updated_offers,
                    unchanged_offers, status, app_version, archive_path
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'PUBBLICATO', %s, %s
                )
                """,
                (
                    batch_id,
                    source_name,
                    preview["source_hash"],
                    supplier_label,
                    published_at,
                    preview["row_count"],
                    preview["new_products"],
                    preview["new_offers"],
                    preview["updated_offers"],
                    preview["unchanged"],
                    app_version,
                    archive_path,
                ),
            )
            stage_times["publication"] = _elapsed(stage)

            stage = time.perf_counter()
            product_rows = _product_rows(records, published_at, batch_id)
            product_returning = execute_values(
                cur,
                """
                INSERT INTO products (
                    product_key, aic, nome_commerciale, principio_attivo,
                    atc7, atc9, fala_lasa, materiale_pericoloso, stupefacente,
                    gruppo_stivaggio, temperatura_stivaggio, upc, note,
                    created_at, updated_at, last_batch_id
                ) VALUES %s
                ON CONFLICT (product_key) DO UPDATE SET
                    aic = EXCLUDED.aic,
                    nome_commerciale = EXCLUDED.nome_commerciale,
                    principio_attivo = EXCLUDED.principio_attivo,
                    atc7 = EXCLUDED.atc7,
                    atc9 = EXCLUDED.atc9,
                    fala_lasa = EXCLUDED.fala_lasa,
                    materiale_pericoloso = EXCLUDED.materiale_pericoloso,
                    stupefacente = EXCLUDED.stupefacente,
                    gruppo_stivaggio = EXCLUDED.gruppo_stivaggio,
                    temperatura_stivaggio = EXCLUDED.temperatura_stivaggio,
                    upc = EXCLUDED.upc,
                    note = EXCLUDED.note,
                    updated_at = EXCLUDED.updated_at,
                    last_batch_id = EXCLUDED.last_batch_id
                RETURNING product_key, product_id
                """,
                product_rows,
                page_size=1000,
                fetch=True,
            ) if product_rows else []
            product_ids = {
                row["product_key"]: int(row["product_id"])
                for row in product_returning
            }
            stage_times["products_upsert"] = _elapsed(stage)

            stage = time.perf_counter()
            offer_rows = _offer_rows(records, product_ids, published_at, batch_id)
            offer_returning = execute_values(
                cur,
                """
                INSERT INTO offers (
                    offer_key, product_id, fornitore, codice_fornitore,
                    prezzo_unitario, prezzo_confezione, minimo_movimentabile,
                    iva, active, first_published_at, last_published_at,
                    current_batch_id
                ) VALUES %s
                ON CONFLICT (offer_key) DO UPDATE SET
                    product_id = EXCLUDED.product_id,
                    fornitore = EXCLUDED.fornitore,
                    codice_fornitore = EXCLUDED.codice_fornitore,
                    prezzo_unitario = EXCLUDED.prezzo_unitario,
                    prezzo_confezione = EXCLUDED.prezzo_confezione,
                    minimo_movimentabile = EXCLUDED.minimo_movimentabile,
                    iva = EXCLUDED.iva,
                    active = 1,
                    last_published_at = EXCLUDED.last_published_at,
                    current_batch_id = EXCLUDED.current_batch_id
                RETURNING offer_key, offer_id
                """,
                offer_rows,
                template="(%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s)",
                page_size=1000,
                fetch=True,
            ) if offer_rows else []
            offer_ids = {
                row["offer_key"]: int(row["offer_id"])
                for row in offer_returning
            }
            stage_times["offers_upsert"] = _elapsed(stage)

            stage = time.perf_counter()
            history_rows: list[tuple] = []
            history_offer_keys: set[str] = set()
            history_actions = {
                "NUOVO PRODOTTO",
                "NUOVA OFFERTA",
                "PREZZO MODIFICATO",
                "PREZZO + ANAGRAFICA",
            }
            for record, detail in zip(records, preview["details"]):
                if detail.get("Azione") not in history_actions:
                    continue
                okey = legacy.offer_key(record)
                if okey in history_offer_keys:
                    continue
                history_offer_keys.add(okey)
                ovals = legacy._offer_values(record)
                history_rows.append((
                    offer_ids[okey],
                    batch_id,
                    ovals["prezzo_unitario"],
                    ovals["prezzo_confezione"],
                    ovals["minimo_movimentabile"],
                    ovals["iva"],
                    published_at,
                ))
            if history_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO price_history (
                        offer_id, batch_id, prezzo_unitario, prezzo_confezione,
                        minimo_movimentabile, iva, published_at
                    ) VALUES %s
                    """,
                    history_rows,
                    page_size=1000,
                )
            stage_times["price_history"] = _elapsed(stage)

            stage = time.perf_counter()
            publication_rows: list[tuple] = []
            for record, detail in zip(records, preview["details"]):
                row_payload = {
                    key: legacy._clean(value)
                    for key, value in record.items()
                }
                publication_rows.append((
                    batch_id,
                    legacy.product_key(record),
                    legacy.offer_key(record),
                    detail["Azione"],
                    json.dumps(row_payload, ensure_ascii=False, default=str),
                    published_at,
                ))
            if publication_rows:
                execute_values(
                    cur,
                    """
                    INSERT INTO publication_rows (
                        batch_id, product_key, offer_key, action, row_json,
                        published_at
                    ) VALUES %s
                    """,
                    publication_rows,
                    page_size=1000,
                )
            stage_times["publication_rows"] = _elapsed(stage)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "[PERF] publication_write rows=%d publication=%.4fs products=%.4fs "
        "offers=%.4fs history=%.4fs rows_insert=%.4fs total=%.4fs",
        len(records),
        stage_times.get("publication", 0.0),
        stage_times.get("products_upsert", 0.0),
        stage_times.get("offers_upsert", 0.0),
        stage_times.get("price_history", 0.0),
        stage_times.get("publication_rows", 0.0),
        _elapsed(total_started),
    )
    return {
        "batch_id": batch_id,
        "published_at": published_at,
        **preview,
    }


def _master22_extra_rows(product_keys: list[str], db_path=None) -> dict[str, dict]:
    unique_keys = _dedupe(product_keys)
    if not unique_keys:
        return {}
    conn = master22._connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT product_key, forma_farmaceutica, x, y, z
            FROM products
            WHERE product_key = ANY(%s)
            """,
            (unique_keys,),
        )
        rows = cur.fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            if hasattr(row, "keys"):
                item = dict(row)
            else:
                item = {
                    "product_key": row[0],
                    "forma_farmaceutica": row[1],
                    "x": row[2],
                    "y": row[3],
                    "z": row[4],
                }
            result[item["product_key"]] = item
        return result
    finally:
        conn.close()


def master22_preview_publication(
    records: list[dict],
    db_path: str | Path | None = None,
) -> dict:
    master22_init_db(db_path)
    preview = preview_publication(records, db_path)
    existing = _master22_extra_rows(
        [legacy.product_key(record) for record in records],
        db_path,
    )

    for record, detail in zip(records, preview.get("details", [])):
        pkey = legacy.product_key(record)
        current = existing.get(pkey)
        if not current:
            continue
        incoming = master22._extra_values(record)
        changed = [
            display
            for display, db_name in master22.EXTRA_FIELDS.items()
            if not master22._same(current.get(db_name), incoming.get(db_name))
        ]
        if not changed:
            continue

        old_changes = detail.get("Campi prodotto variati", "").strip()
        detail["Campi prodotto variati"] = ", ".join(
            [value for value in [old_changes, *changed] if value]
        )
        action = detail.get("Azione")
        if action == "INVARIATO":
            detail["Azione"] = "DATI MODIFICATI"
            preview["unchanged"] = max(0, preview.get("unchanged", 0) - 1)
            preview["data_changes"] = preview.get("data_changes", 0) + 1
            preview["updated_offers"] = preview.get("updated_offers", 0) + 1
        elif action == "PREZZO MODIFICATO":
            detail["Azione"] = "PREZZO + ANAGRAFICA"
            preview["data_changes"] = preview.get("data_changes", 0) + 1
    return preview


def _persist_master22_extra_fields(records: list[dict], db_path=None) -> None:
    rows_by_key: dict[str, tuple] = {}
    for record in records:
        pkey = legacy.product_key(record)
        values = master22._extra_values(record)
        rows_by_key[pkey] = (
            pkey,
            values["forma_farmaceutica"],
            values["x"],
            values["y"],
            values["z"],
        )
    rows = list(rows_by_key.values())
    if not rows:
        return

    started = time.perf_counter()
    conn = master22._connect(db_path)
    try:
        cur = conn.cursor()
        execute_values(
            cur,
            """
            UPDATE products AS p
            SET forma_farmaceutica = v.forma_farmaceutica,
                x = v.x,
                y = v.y,
                z = v.z
            FROM (VALUES %s) AS v(product_key, forma_farmaceutica, x, y, z)
            WHERE p.product_key = v.product_key
            """,
            rows,
            page_size=1000,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    logger.info(
        "[PERF] master22_extra_fields rows=%d elapsed=%.4fs",
        len(rows),
        _elapsed(started),
    )


def master22_publish_records(
    records: list[dict],
    source_name: str,
    db_path: str | Path | None,
    app_version: str = "demo",
    archive_path: str | None = None,
) -> dict:
    total_started = time.perf_counter()
    enhanced = master22_preview_publication(records, db_path)
    result = publish_records(
        records=records,
        source_name=source_name,
        db_path=db_path,
        app_version=app_version,
        archive_path=archive_path,
        preview=enhanced,
    )
    _persist_master22_extra_fields(records, db_path)
    logger.info(
        "[PERF] master22_publish rows=%d total=%.4fs",
        len(records),
        _elapsed(total_started),
    )
    return result


# Patch base backend first so legacy read helpers call the cached init function.
legacy.init_db = init_db
legacy.preview_publication = preview_publication
legacy.publish_records = publish_records

# Patch Master22 before R11/R12 import their function references.
master22.init_db = master22_init_db
master22.preview_publication = master22_preview_publication
master22.publish_records = master22_publish_records


# Import R12 only after Master22 has been patched, then replace the Buyer ERP
# delta N+1 lookup/insert loop with one pair of bulk lookups and one bulk insert.
import modules.order_management as order_management  # noqa: E402

_original_queue_buyer_delta = order_management._queue_buyer_delta


def queue_buyer_delta_bulk(records: list[dict], result: dict, db_path=None) -> None:
    batch_id = result.get("batch_id")
    if not batch_id:
        return

    actionable: list[tuple[dict, dict, str, str, str]] = []
    for record, detail in zip(records, result.get("details") or []):
        action = order_management._action_from_publication(detail.get("Azione"))
        if not action:
            continue
        pkey = order_management.product_key(record)
        okey = order_management.offer_key(record)
        actionable.append((record, detail, action, pkey, okey))

    if not actionable:
        return

    actor = (order_management.current_user() or {}).get("user_id")
    started = time.perf_counter()
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        product_keys = _dedupe([item[3] for item in actionable])
        offer_keys = _dedupe([item[4] for item in actionable])

        cur.execute(
            "SELECT product_key, product_id FROM products WHERE product_key = ANY(%s)",
            (product_keys,),
        )
        product_ids = {row[0]: int(row[1]) for row in cur.fetchall()}

        cur.execute(
            "SELECT offer_key, offer_id FROM offers WHERE offer_key = ANY(%s)",
            (offer_keys,),
        )
        offer_ids = {row[0]: int(row[1]) for row in cur.fetchall()}

        events: list[tuple] = []
        for record, _, action, pkey, okey in actionable:
            events.append((
                f"BUYER|{batch_id}|{okey}",
                product_ids.get(pkey),
                offer_ids.get(okey),
                action,
                "BUYER",
                batch_id,
                json.dumps(
                    order_management._normalize_payload(record),
                    ensure_ascii=False,
                    default=str,
                ),
                actor,
            ))

        execute_values(
            cur,
            """
            INSERT INTO public.erp_delta_events (
                event_key, product_id, offer_id, action, source_type,
                source_batch_id, payload_json, created_by
            ) VALUES %s
            ON CONFLICT (event_key) DO NOTHING
            """,
            events,
            page_size=1000,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        "[PERF] erp_delta_bulk rows=%d elapsed=%.4fs",
        len(actionable),
        _elapsed(started),
    )


order_management._queue_buyer_delta = queue_buyer_delta_bulk

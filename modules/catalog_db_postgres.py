from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from modules.db_backend import get_postgres_url
from modules.catalog_db_sqlite import (
    PRODUCT_FIELDS,
    OFFER_FIELDS,
    _batch_id,
    _clean,
    _offer_values,
    _product_values,
    _same,
    _txt,
    canonical_records_hash,
    offer_key,
    product_key,
)


def _connect():
    url = get_postgres_url()
    if not url:
        raise RuntimeError("DATABASE_URL PostgreSQL non configurata.")
    return psycopg2.connect(
        url,
        connect_timeout=10,
        sslmode="require",
        application_name="listino-farmaci",
    )


def init_db(db_path: str | Path | None = None) -> None:
    """
    In PostgreSQL il parametro db_path è ignorato e viene mantenuto
    solo per compatibilità con le pagine esistenti della R9.
    """
    statements = [
        """
        CREATE TABLE IF NOT EXISTS publications (
            batch_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_hash TEXT NOT NULL UNIQUE,
            supplier TEXT,
            published_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            new_products INTEGER NOT NULL DEFAULT 0,
            new_offers INTEGER NOT NULL DEFAULT 0,
            updated_offers INTEGER NOT NULL DEFAULT 0,
            unchanged_offers INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'PUBBLICATO',
            app_version TEXT,
            archive_path TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS products (
            product_id BIGSERIAL PRIMARY KEY,
            product_key TEXT NOT NULL UNIQUE,
            aic TEXT,
            nome_commerciale TEXT,
            principio_attivo TEXT,
            atc7 TEXT,
            atc9 TEXT,
            fala_lasa TEXT,
            materiale_pericoloso TEXT,
            stupefacente TEXT,
            gruppo_stivaggio TEXT,
            temperatura_stivaggio TEXT,
            upc INTEGER,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_batch_id TEXT,
            CONSTRAINT fk_products_last_batch
                FOREIGN KEY (last_batch_id)
                REFERENCES publications(batch_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS offers (
            offer_id BIGSERIAL PRIMARY KEY,
            offer_key TEXT NOT NULL UNIQUE,
            product_id BIGINT NOT NULL,
            fornitore TEXT NOT NULL,
            codice_fornitore TEXT,
            prezzo_unitario DOUBLE PRECISION,
            prezzo_confezione DOUBLE PRECISION,
            minimo_movimentabile INTEGER,
            iva DOUBLE PRECISION,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
            first_published_at TEXT NOT NULL,
            last_published_at TEXT NOT NULL,
            current_batch_id TEXT,
            CONSTRAINT fk_offers_product
                FOREIGN KEY (product_id)
                REFERENCES products(product_id),
            CONSTRAINT fk_offers_batch
                FOREIGN KEY (current_batch_id)
                REFERENCES publications(batch_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS price_history (
            history_id BIGSERIAL PRIMARY KEY,
            offer_id BIGINT NOT NULL,
            batch_id TEXT NOT NULL,
            prezzo_unitario DOUBLE PRECISION,
            prezzo_confezione DOUBLE PRECISION,
            minimo_movimentabile INTEGER,
            iva DOUBLE PRECISION,
            published_at TEXT NOT NULL,
            CONSTRAINT fk_history_offer
                FOREIGN KEY (offer_id)
                REFERENCES offers(offer_id),
            CONSTRAINT fk_history_batch
                FOREIGN KEY (batch_id)
                REFERENCES publications(batch_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS publication_rows (
            publication_row_id BIGSERIAL PRIMARY KEY,
            batch_id TEXT NOT NULL,
            product_key TEXT NOT NULL,
            offer_key TEXT NOT NULL,
            action TEXT NOT NULL,
            row_json TEXT NOT NULL,
            published_at TEXT NOT NULL,
            CONSTRAINT fk_publication_rows_batch
                FOREIGN KEY (batch_id)
                REFERENCES publications(batch_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_products_aic ON products(aic)",
        "CREATE INDEX IF NOT EXISTS idx_products_name ON products(nome_commerciale)",
        "CREATE INDEX IF NOT EXISTS idx_products_principio ON products(principio_attivo)",
        "CREATE INDEX IF NOT EXISTS idx_offers_supplier ON offers(fornitore)",
        "CREATE INDEX IF NOT EXISTS idx_history_offer ON price_history(offer_id, published_at)",
        "CREATE INDEX IF NOT EXISTS idx_pubrows_batch ON publication_rows(batch_id)",
    ]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _diff_fields(existing: dict | None, values: dict) -> list[str]:
    if existing is None:
        return list(values)
    return [
        key
        for key, value in values.items()
        if not _same(existing.get(key), value)
    ]


def preview_publication(records: list[dict], db_path: str | Path | None = None) -> dict:
    init_db(db_path)

    source_hash = canonical_records_hash(records)
    details = []
    counts = {
        "new_products": 0,
        "new_offers": 0,
        "price_changes": 0,
        "data_changes": 0,
        "updated_offers": 0,
        "unchanged": 0,
    }
    suppliers = sorted({
        _txt(r.get("Fornitore"))
        for r in records
        if _txt(r.get("Fornitore"))
    })

    conn = _connect()
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

            for idx, record in enumerate(records, start=1):
                pkey = product_key(record)
                okey = offer_key(record)
                pvals = _product_values(record)
                ovals = _offer_values(record)

                cur.execute(
                    "SELECT * FROM products WHERE product_key = %s",
                    (pkey,),
                )
                prod = cur.fetchone()

                cur.execute(
                    "SELECT * FROM offers WHERE offer_key = %s",
                    (okey,),
                )
                offer = cur.fetchone()

                pchanges = _diff_fields(prod, pvals)
                ochanges = _diff_fields(offer, ovals)

                price_fields = {
                    "prezzo_unitario",
                    "prezzo_confezione",
                    "minimo_movimentabile",
                    "iva",
                }
                price_changes = [
                    field for field in ochanges
                    if field in price_fields
                ]
                commercial_meta = [
                    field for field in ochanges
                    if field not in price_fields
                ]

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
    finally:
        conn.close()

    return {
        "source_hash": source_hash,
        "already_published": dict(previous) if previous else None,
        "suppliers": suppliers,
        "row_count": len(records),
        "details": details,
        **counts,
    }


def backup_database(
    db_path: str | Path | None,
    backup_dir: str | Path,
    keep: int = 30,
) -> str | None:
    """
    PostgreSQL è esterno a Streamlit: non si crea più una copia .db locale.
    Il backup gestito verrà configurato sul provider quando si passerà
    dal pilot alla produzione.
    """
    return None


def publish_records(
    records: list[dict],
    source_name: str,
    db_path: str | Path | None,
    app_version: str = "demo",
    archive_path: str | None = None,
) -> dict:
    init_db(db_path)
    preview = preview_publication(records, db_path)

    if preview["already_published"]:
        raise ValueError(
            "Questo dataset risulta già pubblicato nel batch "
            f"{preview['already_published']['batch_id']}."
        )

    batch_id = _batch_id()
    from datetime import datetime
    published_at = datetime.now().isoformat(timespec="seconds")
    supplier_label = " | ".join(preview["suppliers"])

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO publications (
                    batch_id,
                    source_name,
                    source_hash,
                    supplier,
                    published_at,
                    row_count,
                    new_products,
                    new_offers,
                    updated_offers,
                    unchanged_offers,
                    status,
                    app_version,
                    archive_path
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
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

            for record, detail in zip(records, preview["details"]):
                pkey = product_key(record)
                okey = offer_key(record)
                pvals = _product_values(record)
                ovals = _offer_values(record)

                cur.execute(
                    "SELECT * FROM products WHERE product_key = %s FOR UPDATE",
                    (pkey,),
                )
                prod = cur.fetchone()

                if prod is None:
                    cur.execute(
                        """
                        INSERT INTO products (
                            product_key,
                            aic,
                            nome_commerciale,
                            principio_attivo,
                            atc7,
                            atc9,
                            fala_lasa,
                            materiale_pericoloso,
                            stupefacente,
                            gruppo_stivaggio,
                            temperatura_stivaggio,
                            upc,
                            note,
                            created_at,
                            updated_at,
                            last_batch_id
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING product_id
                        """,
                        (
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
                        ),
                    )
                    product_id = cur.fetchone()["product_id"]

                else:
                    product_id = prod["product_id"]
                    cur.execute(
                        """
                        UPDATE products
                        SET
                            aic = %s,
                            nome_commerciale = %s,
                            principio_attivo = %s,
                            atc7 = %s,
                            atc9 = %s,
                            fala_lasa = %s,
                            materiale_pericoloso = %s,
                            stupefacente = %s,
                            gruppo_stivaggio = %s,
                            temperatura_stivaggio = %s,
                            upc = %s,
                            note = %s,
                            updated_at = %s,
                            last_batch_id = %s
                        WHERE product_id = %s
                        """,
                        (
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
                            batch_id,
                            product_id,
                        ),
                    )

                cur.execute(
                    "SELECT * FROM offers WHERE offer_key = %s FOR UPDATE",
                    (okey,),
                )
                offer = cur.fetchone()

                if offer is None:
                    cur.execute(
                        """
                        INSERT INTO offers (
                            offer_key,
                            product_id,
                            fornitore,
                            codice_fornitore,
                            prezzo_unitario,
                            prezzo_confezione,
                            minimo_movimentabile,
                            iva,
                            active,
                            first_published_at,
                            last_published_at,
                            current_batch_id
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, 1, %s, %s, %s
                        )
                        RETURNING offer_id
                        """,
                        (
                            okey,
                            product_id,
                            ovals["fornitore"],
                            ovals["codice_fornitore"],
                            ovals["prezzo_unitario"],
                            ovals["prezzo_confezione"],
                            ovals["minimo_movimentabile"],
                            ovals["iva"],
                            published_at,
                            published_at,
                            batch_id,
                        ),
                    )
                    offer_id = cur.fetchone()["offer_id"]
                    price_changed = True

                else:
                    offer_id = offer["offer_id"]
                    price_changed = any(
                        not _same(offer.get(field), ovals[field])
                        for field in (
                            "prezzo_unitario",
                            "prezzo_confezione",
                            "minimo_movimentabile",
                            "iva",
                        )
                    )

                    cur.execute(
                        """
                        UPDATE offers
                        SET
                            product_id = %s,
                            fornitore = %s,
                            codice_fornitore = %s,
                            prezzo_unitario = %s,
                            prezzo_confezione = %s,
                            minimo_movimentabile = %s,
                            iva = %s,
                            active = 1,
                            last_published_at = %s,
                            current_batch_id = %s
                        WHERE offer_id = %s
                        """,
                        (
                            product_id,
                            ovals["fornitore"],
                            ovals["codice_fornitore"],
                            ovals["prezzo_unitario"],
                            ovals["prezzo_confezione"],
                            ovals["minimo_movimentabile"],
                            ovals["iva"],
                            published_at,
                            batch_id,
                            offer_id,
                        ),
                    )

                if price_changed:
                    cur.execute(
                        """
                        INSERT INTO price_history (
                            offer_id,
                            batch_id,
                            prezzo_unitario,
                            prezzo_confezione,
                            minimo_movimentabile,
                            iva,
                            published_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            offer_id,
                            batch_id,
                            ovals["prezzo_unitario"],
                            ovals["prezzo_confezione"],
                            ovals["minimo_movimentabile"],
                            ovals["iva"],
                            published_at,
                        ),
                    )

                row_payload = {
                    key: _clean(value)
                    for key, value in record.items()
                }
                cur.execute(
                    """
                    INSERT INTO publication_rows (
                        batch_id,
                        product_key,
                        offer_key,
                        action,
                        row_json,
                        published_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        batch_id,
                        pkey,
                        okey,
                        detail["Azione"],
                        json.dumps(
                            row_payload,
                            ensure_ascii=False,
                            default=str,
                        ),
                        published_at,
                    ),
                )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    return {
        "batch_id": batch_id,
        "published_at": published_at,
        **preview,
    }


def _dataframe(query: str, params: tuple | None = None) -> pd.DataFrame:
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params or ())
            rows = cur.fetchall()
        return pd.DataFrame([dict(row) for row in rows])
    finally:
        conn.close()


def catalogue_dataframe(db_path: str | Path | None = None) -> pd.DataFrame:
    init_db(db_path)

    query = """
        SELECT
            p.product_id,
            o.offer_id,
            p.aic AS "AIC",
            p.nome_commerciale AS "Nome Commerciale",
            p.principio_attivo AS "Principio Attivo",
            p.atc7 AS "ATC7",
            p.atc9 AS "ATC9",
            p.fala_lasa AS "Fala / Lasa",
            p.materiale_pericoloso AS "Materiale Pericoloso",
            p.stupefacente AS "Stupefacente",
            p.gruppo_stivaggio AS "Gruppo di Stivaggio",
            p.temperatura_stivaggio AS "Temperatura di Stivaggio",
            p.upc AS "UPC",
            o.fornitore AS "Fornitore",
            o.codice_fornitore AS "Codice Fornitore",
            o.prezzo_unitario AS "Prezzo Unitario",
            o.prezzo_confezione AS "Prezzo Confezione",
            o.minimo_movimentabile AS "Minimo Movimentabile",
            o.iva AS "IVA",
            p.note AS "Note",
            o.last_published_at AS "Ultimo aggiornamento",
            o.current_batch_id AS "Batch corrente"
        FROM offers o
        JOIN products p
          ON p.product_id = o.product_id
        WHERE o.active = 1
        ORDER BY
            LOWER(COALESCE(p.nome_commerciale, '')),
            LOWER(COALESCE(o.fornitore, ''))
    """
    return _dataframe(query)


def publications_dataframe(db_path: str | Path | None = None) -> pd.DataFrame:
    init_db(db_path)

    return _dataframe(
        """
        SELECT
            batch_id AS "Batch",
            published_at AS "Data",
            supplier AS "Fornitore",
            source_name AS "File",
            row_count AS "Articoli",
            new_products AS "Nuovi prodotti",
            new_offers AS "Nuove offerte",
            updated_offers AS "Aggiornamenti",
            unchanged_offers AS "Invariati",
            status AS "Stato",
            app_version AS "Versione"
        FROM publications
        ORDER BY published_at DESC
        """
    )


def publication_rows_dataframe(
    db_path: str | Path | None,
    batch_id: str,
) -> pd.DataFrame:
    init_db(db_path)

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT action, row_json
                FROM publication_rows
                WHERE batch_id = %s
                ORDER BY publication_row_id
                """,
                (batch_id,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    data = []
    for row in rows:
        item = json.loads(row["row_json"])
        data.append({
            "Azione": row["action"],
            **item,
        })

    return pd.DataFrame(data)


def price_history_dataframe(
    db_path: str | Path | None,
    offer_id: int,
) -> pd.DataFrame:
    init_db(db_path)

    return _dataframe(
        """
        SELECT
            published_at AS "Data",
            prezzo_unitario AS "Prezzo Unitario",
            prezzo_confezione AS "Prezzo Confezione",
            minimo_movimentabile AS "Minimo Movimentabile",
            iva AS "IVA",
            batch_id AS "Batch"
        FROM price_history
        WHERE offer_id = %s
        ORDER BY published_at DESC, history_id DESC
        """,
        (offer_id,),
    )


def dashboard_stats(db_path: str | Path | None = None) -> dict:
    init_db(db_path)

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM products) AS products,
                    (SELECT COUNT(*) FROM offers WHERE active = 1) AS offers,
                    (
                        SELECT COUNT(DISTINCT fornitore)
                        FROM offers
                        WHERE active = 1
                    ) AS suppliers,
                    (
                        SELECT COUNT(*)
                        FROM publications
                        WHERE status = 'PUBBLICATO'
                    ) AS publications
                """
            )
            row = cur.fetchone()

            cur.execute(
                """
                SELECT
                    batch_id,
                    published_at,
                    supplier,
                    row_count
                FROM publications
                ORDER BY published_at DESC
                LIMIT 1
                """
            )
            latest = cur.fetchone()
    finally:
        conn.close()

    return {
        **dict(row),
        "latest": dict(latest) if latest else None,
    }


def export_catalogue_excel(
    db_path: str | Path | None,
    dataframe: pd.DataFrame | None = None,
) -> bytes:
    df = (
        dataframe.copy()
        if dataframe is not None
        else catalogue_dataframe(db_path)
    )

    out = BytesIO()

    export_df = df.drop(
        columns=[
            column
            for column in ("product_id", "offer_id")
            if column in df.columns
        ],
        errors="ignore",
    )

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        export_df.to_excel(
            writer,
            index=False,
            sheet_name="Listino",
        )

        ws = writer.book["Listino"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        for col in ws.columns:
            max_len = max(
                (
                    len(str(cell.value))
                    if cell.value is not None
                    else 0
                )
                for cell in col
            )
            ws.column_dimensions[
                col[0].column_letter
            ].width = min(
                max(max_len + 2, 11),
                42,
            )

    return out.getvalue()

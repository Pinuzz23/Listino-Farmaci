from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from modules.db_backend import get_postgres_url, use_postgres


EXTRA_FIELDS = {
    "Forma Farmaceutica": "forma_farmaceutica",
    "X": "x",
    "Y": "y",
    "Z": "z",
}


def _backend():
    if use_postgres():
        import modules.catalog_db_postgres as backend
    else:
        import modules.catalog_db_sqlite as backend
    return backend


def _same(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        if pd.isna(a) and pd.isna(b):
            return True
    except Exception:
        pass
    try:
        return abs(float(a) - float(b)) < 1e-9
    except Exception:
        return str(a).strip().casefold() == str(b).strip().casefold()


def _clean_number(value: Any):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.strip().replace(" ", "").replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extra_values(record: dict) -> dict:
    form = record.get("Forma Farmaceutica")
    form = None if form is None else str(form).strip() or None
    return {
        "forma_farmaceutica": form,
        "x": _clean_number(record.get("X")),
        "y": _clean_number(record.get("Y")),
        "z": _clean_number(record.get("Z")),
    }


def _connect(db_path: str | Path | None = None):
    if use_postgres():
        import psycopg2
        return psycopg2.connect(
            get_postgres_url(),
            connect_timeout=10,
            sslmode="require",
            application_name="listino-farmaci",
        )
    conn = sqlite3.connect(Path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    backend = _backend()
    backend.init_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        if use_postgres():
            cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS forma_farmaceutica TEXT")
            cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS x DOUBLE PRECISION")
            cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS y DOUBLE PRECISION")
            cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS z DOUBLE PRECISION")
        else:
            columns = {row[1] for row in cur.execute("PRAGMA table_info(products)").fetchall()}
            for name, sql_type in {
                "forma_farmaceutica": "TEXT",
                "x": "REAL",
                "y": "REAL",
                "z": "REAL",
            }.items():
                if name not in columns:
                    cur.execute(f"ALTER TABLE products ADD COLUMN {name} {sql_type}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _extra_rows(db_path=None) -> dict[str, dict]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT product_key, forma_farmaceutica, x, y, z FROM products")
        rows = cur.fetchall()
        result = {}
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


def preview_publication(records: list[dict], db_path: str | Path | None = None) -> dict:
    init_db(db_path)
    backend = _backend()
    preview = backend.preview_publication(records, db_path)
    existing = _extra_rows(db_path)

    for record, detail in zip(records, preview.get("details", [])):
        pkey = backend.product_key(record)
        current = existing.get(pkey)
        if not current:
            continue
        incoming = _extra_values(record)
        changed = [
            display for display, db_name in EXTRA_FIELDS.items()
            if not _same(current.get(db_name), incoming.get(db_name))
        ]
        if not changed:
            continue

        old_changes = detail.get("Campi prodotto variati", "").strip()
        detail["Campi prodotto variati"] = ", ".join(
            [x for x in [old_changes, *changed] if x]
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


def _persist_extra_fields(records: list[dict], batch_id: str, db_path=None) -> None:
    backend = _backend()
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        for record in records:
            pkey = backend.product_key(record)
            values = _extra_values(record)
            if use_postgres():
                cur.execute(
                    """
                    UPDATE products
                    SET forma_farmaceutica=%s, x=%s, y=%s, z=%s
                    WHERE product_key=%s
                    """,
                    (values["forma_farmaceutica"], values["x"], values["y"], values["z"], pkey),
                )
            else:
                cur.execute(
                    """
                    UPDATE products
                    SET forma_farmaceutica=?, x=?, y=?, z=?
                    WHERE product_key=?
                    """,
                    (values["forma_farmaceutica"], values["x"], values["y"], values["z"], pkey),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sync_publication(preview: dict, records: list[dict], batch_id: str, db_path=None) -> None:
    backend = _backend()
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        values = (
            preview.get("new_products", 0),
            preview.get("new_offers", 0),
            preview.get("updated_offers", 0),
            preview.get("unchanged", 0),
            batch_id,
        )
        if use_postgres():
            cur.execute(
                """UPDATE publications SET new_products=%s, new_offers=%s,
                   updated_offers=%s, unchanged_offers=%s WHERE batch_id=%s""",
                values,
            )
        else:
            cur.execute(
                """UPDATE publications SET new_products=?, new_offers=?,
                   updated_offers=?, unchanged_offers=? WHERE batch_id=?""",
                values,
            )

        for record, detail in zip(records, preview.get("details", [])):
            pkey = backend.product_key(record)
            okey = backend.offer_key(record)
            params = (detail.get("Azione", "INVARIATO"), batch_id, pkey, okey)
            if use_postgres():
                cur.execute(
                    "UPDATE publication_rows SET action=%s WHERE batch_id=%s AND product_key=%s AND offer_key=%s",
                    params,
                )
            else:
                cur.execute(
                    "UPDATE publication_rows SET action=? WHERE batch_id=? AND product_key=? AND offer_key=?",
                    params,
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def publish_records(
    records: list[dict],
    source_name: str,
    db_path: str | Path | None,
    app_version: str = "demo",
    archive_path: str | None = None,
) -> dict:
    enhanced = preview_publication(records, db_path)
    backend = _backend()
    result = backend.publish_records(
        records=records,
        source_name=source_name,
        db_path=db_path,
        app_version=app_version,
        archive_path=archive_path,
    )
    _persist_extra_fields(records, result["batch_id"], db_path)
    _sync_publication(enhanced, records, result["batch_id"], db_path)
    return {
        **result,
        **{
            key: enhanced[key]
            for key in (
                "source_hash", "already_published", "suppliers", "row_count", "details",
                "new_products", "new_offers", "price_changes", "data_changes",
                "updated_offers", "unchanged",
            )
            if key in enhanced
        },
    }


def catalogue_dataframe(db_path: str | Path | None = None) -> pd.DataFrame:
    init_db(db_path)
    backend = _backend()
    df = backend.catalogue_dataframe(db_path)
    if df.empty:
        for col in EXTRA_FIELDS:
            if col not in df.columns:
                df[col] = pd.Series(dtype="object")
        return df

    conn = _connect(db_path)
    try:
        query = "SELECT product_id, forma_farmaceutica AS \"Forma Farmaceutica\", x AS X, y AS Y, z AS Z FROM products"
        extra = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    return df.merge(extra, how="left", on="product_id")


def export_catalogue_excel(db_path: str | Path | None, dataframe: pd.DataFrame | None = None) -> bytes:
    backend = _backend()
    df = dataframe if dataframe is not None else catalogue_dataframe(db_path)
    return backend.export_catalogue_excel(db_path, df)

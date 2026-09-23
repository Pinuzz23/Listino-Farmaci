from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


PRODUCT_FIELDS = {
    "AIC": "aic",
    "Nome Commerciale": "nome_commerciale",
    "Principio Attivo": "principio_attivo",
    "ATC7": "atc7",
    "ATC9": "atc9",
    "Fala / Lasa": "fala_lasa",
    "Materiale Pericoloso": "materiale_pericoloso",
    "Stupefacente": "stupefacente",
    "Gruppo di Stivaggio": "gruppo_stivaggio",
    "Temperatura di Stivaggio": "temperatura_stivaggio",
    "UPC": "upc",
    "Note": "note",
}

OFFER_FIELDS = {
    "Fornitore": "fornitore",
    "Codice Fornitore": "codice_fornitore",
    "Prezzo Unitario": "prezzo_unitario",
    "Prezzo Confezione": "prezzo_confezione",
    "Minimo Movimentabile": "minimo_movimentabile",
    "IVA": "iva",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(value: Any):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def _txt(value: Any) -> str:
    value = _clean(value)
    return "" if value is None else str(value).strip()


def _norm_key(value: Any) -> str:
    text = _txt(value).upper()
    text = re.sub(r"\s+", " ", text)
    return text


def _float(value: Any):
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, str):
        text = value.replace("€", "").replace(" ", "").replace("%", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any):
    num = _float(value)
    if num is None:
        return None
    return int(round(num))


def product_key(record: dict) -> str:
    aic = re.sub(r"\D", "", _txt(record.get("AIC")))
    if aic:
        return f"AIC:{aic.zfill(9)}"
    supplier = _norm_key(record.get("Fornitore"))
    code = _norm_key(record.get("Codice Fornitore"))
    return f"SUP:{supplier}|CODE:{code}"


def offer_key(record: dict) -> str:
    return f"{product_key(record)}|FORN:{_norm_key(record.get('Fornitore'))}"


def canonical_records_hash(records: list[dict]) -> str:
    safe = []
    for record in records:
        row = {}
        for key in sorted(record):
            value = _clean(record.get(key))
            if isinstance(value, float):
                value = round(value, 8)
            row[key] = value
        safe.append(row)
    payload = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(db_path: str | Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
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
            );

            CREATE TABLE IF NOT EXISTS products (
                product_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                FOREIGN KEY(last_batch_id) REFERENCES publications(batch_id)
            );

            CREATE TABLE IF NOT EXISTS offers (
                offer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_key TEXT NOT NULL UNIQUE,
                product_id INTEGER NOT NULL,
                fornitore TEXT NOT NULL,
                codice_fornitore TEXT,
                prezzo_unitario REAL,
                prezzo_confezione REAL,
                minimo_movimentabile INTEGER,
                iva REAL,
                active INTEGER NOT NULL DEFAULT 1,
                first_published_at TEXT NOT NULL,
                last_published_at TEXT NOT NULL,
                current_batch_id TEXT,
                FOREIGN KEY(product_id) REFERENCES products(product_id),
                FOREIGN KEY(current_batch_id) REFERENCES publications(batch_id)
            );

            CREATE TABLE IF NOT EXISTS price_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                offer_id INTEGER NOT NULL,
                batch_id TEXT NOT NULL,
                prezzo_unitario REAL,
                prezzo_confezione REAL,
                minimo_movimentabile INTEGER,
                iva REAL,
                published_at TEXT NOT NULL,
                FOREIGN KEY(offer_id) REFERENCES offers(offer_id),
                FOREIGN KEY(batch_id) REFERENCES publications(batch_id)
            );

            CREATE TABLE IF NOT EXISTS publication_rows (
                publication_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                offer_key TEXT NOT NULL,
                action TEXT NOT NULL,
                row_json TEXT NOT NULL,
                published_at TEXT NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES publications(batch_id)
            );

            CREATE INDEX IF NOT EXISTS idx_products_aic ON products(aic);
            CREATE INDEX IF NOT EXISTS idx_products_name ON products(nome_commerciale);
            CREATE INDEX IF NOT EXISTS idx_products_active ON products(principio_attivo);
            CREATE INDEX IF NOT EXISTS idx_offers_supplier ON offers(fornitore);
            CREATE INDEX IF NOT EXISTS idx_history_offer ON price_history(offer_id, published_at);
            CREATE INDEX IF NOT EXISTS idx_pubrows_batch ON publication_rows(batch_id);
            """
        )
        # Lightweight migration for databases created by R5 or earlier.
        product_columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if "fala_lasa" not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN fala_lasa TEXT")

        # Backfill Fala/Lasa from the publication audit rows when upgrading an existing R5 database.
        # publication_rows keeps the original validated row JSON, so no data needs to be guessed.
        try:
            audit_rows = conn.execute(
                "SELECT product_key, row_json FROM publication_rows ORDER BY publication_row_id"
            ).fetchall()
            latest_fala = {}
            for audit in audit_rows:
                try:
                    payload = json.loads(audit["row_json"])
                except Exception:
                    continue
                value = _clean(payload.get("Fala / Lasa"))
                if value is not None:
                    latest_fala[audit["product_key"]] = value
            for pkey, value in latest_fala.items():
                conn.execute(
                    "UPDATE products SET fala_lasa = ? WHERE product_key = ? AND (fala_lasa IS NULL OR TRIM(fala_lasa) = '')",
                    (value, pkey),
                )
        except sqlite3.Error:
            pass


def _product_values(record: dict) -> dict:
    return {
        "aic": (_txt(record.get("AIC")) or None),
        "nome_commerciale": _clean(record.get("Nome Commerciale")),
        "principio_attivo": _clean(record.get("Principio Attivo")),
        "atc7": _clean(record.get("ATC7")),
        "atc9": _clean(record.get("ATC9")),
        "fala_lasa": _clean(record.get("Fala / Lasa")),
        "materiale_pericoloso": _clean(record.get("Materiale Pericoloso")),
        "stupefacente": _clean(record.get("Stupefacente")),
        "gruppo_stivaggio": _clean(record.get("Gruppo di Stivaggio")),
        "temperatura_stivaggio": _clean(record.get("Temperatura di Stivaggio")),
        "upc": _int(record.get("UPC")),
        "note": _clean(record.get("Note")),
    }


def _offer_values(record: dict) -> dict:
    iva = _float(record.get("IVA"))
    if iva is not None and iva > 1:
        iva = iva / 100.0
    return {
        "fornitore": _txt(record.get("Fornitore")),
        "codice_fornitore": _clean(record.get("Codice Fornitore")),
        "prezzo_unitario": _float(record.get("Prezzo Unitario")),
        "prezzo_confezione": _float(record.get("Prezzo Confezione")),
        "minimo_movimentabile": _int(record.get("Minimo Movimentabile")),
        "iva": iva,
    }


def _same(a: Any, b: Any) -> bool:
    a = _clean(a)
    b = _clean(b)
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            if a is None or b is None:
                return a is None and b is None
            return abs(float(a) - float(b)) < 1e-9
        except Exception:
            pass
    return _txt(a).casefold() == _txt(b).casefold()


def _diff_fields(existing: sqlite3.Row | None, values: dict) -> list[str]:
    if existing is None:
        return list(values)
    return [key for key, value in values.items() if not _same(existing[key], value)]


def preview_publication(records: list[dict], db_path: str | Path) -> dict:
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
    suppliers = sorted({_txt(r.get("Fornitore")) for r in records if _txt(r.get("Fornitore"))})

    with _connect(db_path) as conn:
        previous = conn.execute(
            "SELECT batch_id, published_at, source_name FROM publications WHERE source_hash = ? AND status = 'PUBBLICATO'",
            (source_hash,),
        ).fetchone()

        for idx, record in enumerate(records, start=1):
            pkey = product_key(record)
            okey = offer_key(record)
            pvals = _product_values(record)
            ovals = _offer_values(record)
            prod = conn.execute("SELECT * FROM products WHERE product_key = ?", (pkey,)).fetchone()
            offer = conn.execute("SELECT * FROM offers WHERE offer_key = ?", (okey,)).fetchone()

            pchanges = _diff_fields(prod, pvals)
            ochanges = _diff_fields(offer, ovals)
            price_fields = {"prezzo_unitario", "prezzo_confezione", "minimo_movimentabile", "iva"}
            price_changes = [f for f in ochanges if f in price_fields]
            commercial_meta = [f for f in ochanges if f not in price_fields]

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

    return {
        "source_hash": source_hash,
        "already_published": dict(previous) if previous else None,
        "suppliers": suppliers,
        "row_count": len(records),
        "details": details,
        **counts,
    }


def _batch_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = hashlib.sha1(f"{stamp}-{datetime.now().timestamp()}".encode()).hexdigest()[:4].upper()
    return f"PUB-{stamp}-{suffix}"


def backup_database(db_path: str | Path, backup_dir: str | Path, keep: int = 30) -> str | None:
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"listino_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db_path, target)
    backups = sorted(backup_dir.glob("listino_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(target)


def publish_records(
    records: list[dict],
    source_name: str,
    db_path: str | Path,
    app_version: str = "demo",
    archive_path: str | None = None,
) -> dict:
    init_db(db_path)
    preview = preview_publication(records, db_path)
    if preview["already_published"]:
        raise ValueError(
            f"Questo dataset risulta già pubblicato nel batch {preview['already_published']['batch_id']}."
        )

    batch_id = _batch_id()
    published_at = _now()
    supplier_label = " | ".join(preview["suppliers"])

    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO publications (
                batch_id, source_name, source_hash, supplier, published_at, row_count,
                new_products, new_offers, updated_offers, unchanged_offers,
                status, app_version, archive_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PUBBLICATO', ?, ?)
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

            prod = conn.execute("SELECT * FROM products WHERE product_key = ?", (pkey,)).fetchone()
            if prod is None:
                cur = conn.execute(
                    """
                    INSERT INTO products (
                        product_key, aic, nome_commerciale, principio_attivo, atc7, atc9, fala_lasa,
                        materiale_pericoloso, stupefacente, gruppo_stivaggio, temperatura_stivaggio,
                        upc, note, created_at, updated_at, last_batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pkey, pvals["aic"], pvals["nome_commerciale"], pvals["principio_attivo"],
                        pvals["atc7"], pvals["atc9"], pvals["fala_lasa"], pvals["materiale_pericoloso"],
                        pvals["stupefacente"], pvals["gruppo_stivaggio"], pvals["temperatura_stivaggio"],
                        pvals["upc"], pvals["note"], published_at, published_at, batch_id,
                    ),
                )
                product_id = cur.lastrowid
            else:
                product_id = prod["product_id"]
                conn.execute(
                    """
                    UPDATE products SET
                        aic=?, nome_commerciale=?, principio_attivo=?, atc7=?, atc9=?, fala_lasa=?,
                        materiale_pericoloso=?, stupefacente=?, gruppo_stivaggio=?, temperatura_stivaggio=?,
                        upc=?, note=?, updated_at=?, last_batch_id=?
                    WHERE product_id=?
                    """,
                    (
                        pvals["aic"], pvals["nome_commerciale"], pvals["principio_attivo"],
                        pvals["atc7"], pvals["atc9"], pvals["fala_lasa"], pvals["materiale_pericoloso"], pvals["stupefacente"],
                        pvals["gruppo_stivaggio"], pvals["temperatura_stivaggio"], pvals["upc"], pvals["note"],
                        published_at, batch_id, product_id,
                    ),
                )

            offer = conn.execute("SELECT * FROM offers WHERE offer_key = ?", (okey,)).fetchone()
            if offer is None:
                cur = conn.execute(
                    """
                    INSERT INTO offers (
                        offer_key, product_id, fornitore, codice_fornitore,
                        prezzo_unitario, prezzo_confezione, minimo_movimentabile, iva,
                        active, first_published_at, last_published_at, current_batch_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        okey, product_id, ovals["fornitore"], ovals["codice_fornitore"],
                        ovals["prezzo_unitario"], ovals["prezzo_confezione"], ovals["minimo_movimentabile"],
                        ovals["iva"], published_at, published_at, batch_id,
                    ),
                )
                offer_id = cur.lastrowid
                price_changed = True
            else:
                offer_id = offer["offer_id"]
                price_changed = any(
                    not _same(offer[field], ovals[field])
                    for field in ("prezzo_unitario", "prezzo_confezione", "minimo_movimentabile", "iva")
                )
                conn.execute(
                    """
                    UPDATE offers SET
                        product_id=?, fornitore=?, codice_fornitore=?, prezzo_unitario=?, prezzo_confezione=?,
                        minimo_movimentabile=?, iva=?, active=1, last_published_at=?, current_batch_id=?
                    WHERE offer_id=?
                    """,
                    (
                        product_id, ovals["fornitore"], ovals["codice_fornitore"], ovals["prezzo_unitario"],
                        ovals["prezzo_confezione"], ovals["minimo_movimentabile"], ovals["iva"],
                        published_at, batch_id, offer_id,
                    ),
                )

            if price_changed:
                conn.execute(
                    """
                    INSERT INTO price_history (
                        offer_id, batch_id, prezzo_unitario, prezzo_confezione,
                        minimo_movimentabile, iva, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        offer_id, batch_id, ovals["prezzo_unitario"], ovals["prezzo_confezione"],
                        ovals["minimo_movimentabile"], ovals["iva"], published_at,
                    ),
                )

            row_payload = {k: _clean(v) for k, v in record.items()}
            conn.execute(
                """
                INSERT INTO publication_rows (
                    batch_id, product_key, offer_key, action, row_json, published_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id, pkey, okey, detail["Azione"],
                    json.dumps(row_payload, ensure_ascii=False, default=str), published_at,
                ),
            )

        conn.commit()

    return {
        "batch_id": batch_id,
        "published_at": published_at,
        **preview,
    }


def catalogue_dataframe(db_path: str | Path) -> pd.DataFrame:
    init_db(db_path)
    query = """
        SELECT
            p.product_id,
            o.offer_id,
            p.aic AS AIC,
            p.nome_commerciale AS "Nome Commerciale",
            p.principio_attivo AS "Principio Attivo",
            p.atc7 AS ATC7,
            p.atc9 AS ATC9,
            p.fala_lasa AS "Fala / Lasa",
            p.materiale_pericoloso AS "Materiale Pericoloso",
            p.stupefacente AS Stupefacente,
            p.gruppo_stivaggio AS "Gruppo di Stivaggio",
            p.temperatura_stivaggio AS "Temperatura di Stivaggio",
            p.upc AS UPC,
            o.fornitore AS Fornitore,
            o.codice_fornitore AS "Codice Fornitore",
            o.prezzo_unitario AS "Prezzo Unitario",
            o.prezzo_confezione AS "Prezzo Confezione",
            o.minimo_movimentabile AS "Minimo Movimentabile",
            o.iva AS IVA,
            p.note AS Note,
            o.last_published_at AS "Ultimo aggiornamento",
            o.current_batch_id AS "Batch corrente"
        FROM offers o
        JOIN products p ON p.product_id = o.product_id
        WHERE o.active = 1
        ORDER BY p.nome_commerciale COLLATE NOCASE, o.fornitore COLLATE NOCASE
    """
    with _connect(db_path) as conn:
        return pd.read_sql_query(query, conn)


def publications_dataframe(db_path: str | Path) -> pd.DataFrame:
    init_db(db_path)
    with _connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT batch_id AS Batch, published_at AS Data, supplier AS Fornitore,
                   source_name AS File, row_count AS Articoli, new_products AS "Nuovi prodotti",
                   new_offers AS "Nuove offerte", updated_offers AS Aggiornamenti,
                   unchanged_offers AS Invariati, status AS Stato, app_version AS Versione
            FROM publications
            ORDER BY published_at DESC
            """,
            conn,
        )


def publication_rows_dataframe(db_path: str | Path, batch_id: str) -> pd.DataFrame:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT action, row_json FROM publication_rows WHERE batch_id = ? ORDER BY publication_row_id",
            (batch_id,),
        ).fetchall()
    data = []
    for row in rows:
        item = json.loads(row["row_json"])
        item = {"Azione": row["action"], **item}
        data.append(item)
    return pd.DataFrame(data)


def price_history_dataframe(db_path: str | Path, offer_id: int) -> pd.DataFrame:
    init_db(db_path)
    with _connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT published_at AS Data, prezzo_unitario AS "Prezzo Unitario",
                   prezzo_confezione AS "Prezzo Confezione",
                   minimo_movimentabile AS "Minimo Movimentabile", iva AS IVA,
                   batch_id AS Batch
            FROM price_history
            WHERE offer_id = ?
            ORDER BY published_at DESC, history_id DESC
            """,
            conn,
            params=(offer_id,),
        )


def dashboard_stats(db_path: str | Path) -> dict:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM products) AS products,
              (SELECT COUNT(*) FROM offers WHERE active=1) AS offers,
              (SELECT COUNT(DISTINCT fornitore) FROM offers WHERE active=1) AS suppliers,
              (SELECT COUNT(*) FROM publications WHERE status='PUBBLICATO') AS publications
            """
        ).fetchone()
        latest = conn.execute(
            "SELECT batch_id, published_at, supplier, row_count FROM publications ORDER BY published_at DESC LIMIT 1"
        ).fetchone()
    return {**dict(row), "latest": dict(latest) if latest else None}


def export_catalogue_excel(db_path: str | Path, dataframe: pd.DataFrame | None = None) -> bytes:
    from io import BytesIO

    df = dataframe.copy() if dataframe is not None else catalogue_dataframe(db_path)
    out = BytesIO()
    export_df = df.drop(columns=[c for c in ("product_id", "offer_id") if c in df.columns], errors="ignore")
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Listino")
        ws = writer.book["Listino"]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            max_len = max((len(str(cell.value)) if cell.value is not None else 0) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 11), 42)
    return out.getvalue()

from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd

from modules.auth import audit, current_user
from modules.db_backend import use_postgres
from modules.validator import LEVEL_BLOCK, LEVEL_WARNING, add_issue, parse_date
import modules.master22_catalog as master22
import modules.order_management as order_management


VALIDITY_FIELD = "Data Validità Farmaco"
STATUS_REGULAR = "REGOLARE"
STATUS_ATTENTION = "ATTENZIONE"
STATUS_CRITICAL = "CRITICO"
STATUS_EXPIRED = "SCADUTO"
STATUS_MISSING = "SENZA DATA"

ATTENTION_RATIO = 0.50
CRITICAL_RATIO = 2 / 3

_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = Lock()

_original_order_init_db = order_management.init_db
_original_master_preview = master22.preview_publication
_original_publish_core = order_management._r10_publish_records
_original_catalogue_dataframe = order_management.catalogue_dataframe


class ValidityError(RuntimeError):
    pass


class ValidityPersistenceError(ValidityError):
    """Errore avvenuto dopo il commit della pubblicazione core.

    Conserva il risultato della pubblicazione per consentire al guard R14.1
    di ricostruire in modo idempotente la sola persistenza della validità.
    """

    def __init__(self, result: dict[str, Any], cause: Exception):
        self.result = dict(result)
        self.cause = cause
        super().__init__(
            f"Pubblicazione {result.get('batch_id') or '-'} confermata, "
            f"ma persistenza validità non completata: {cause}"
        )


def _schema_key(db_path: str | Path | None = None) -> str:
    if use_postgres():
        return "postgres"
    return f"sqlite:{Path(db_path or 'data/listino.db').resolve()}"


def ensure_validity_schema(db_path: str | Path | None = None) -> None:
    key = _schema_key(db_path)
    if key in _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            return

        conn = order_management._connect(db_path)
        try:
            cur = conn.cursor()
            if use_postgres():
                cur.execute(
                    "ALTER TABLE products ADD COLUMN IF NOT EXISTS data_validita_farmaco DATE"
                )
                cur.execute(
                    "ALTER TABLE products ADD COLUMN IF NOT EXISTS validita_riferimento_at TIMESTAMPTZ"
                )
                cur.execute(
                    "ALTER TABLE products ADD COLUMN IF NOT EXISTS validita_iniziale_giorni INTEGER"
                )
                cur.execute(
                    "ALTER TABLE products ADD COLUMN IF NOT EXISTS validita_updated_at TIMESTAMPTZ"
                )
                cur.execute(
                    "ALTER TABLE products ADD COLUMN IF NOT EXISTS validita_updated_by UUID"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS product_validity_history (
                        validity_history_id BIGSERIAL PRIMARY KEY,
                        product_id BIGINT NOT NULL,
                        event_type TEXT NOT NULL,
                        old_valid_until DATE,
                        new_valid_until DATE NOT NULL,
                        reference_at TIMESTAMPTZ NOT NULL,
                        initial_days INTEGER NOT NULL,
                        reason TEXT,
                        source_batch_id TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        created_by UUID,
                        CONSTRAINT fk_product_validity_history_product
                            FOREIGN KEY (product_id) REFERENCES products(product_id),
                        CONSTRAINT product_validity_history_event_check
                            CHECK (event_type IN ('FIRST_LOAD', 'TRACE_RENEWAL', 'BUYER_RENEWAL'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_product_validity_history_product
                    ON product_validity_history(product_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_products_validity
                    ON products(data_validita_farmaco)
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_product_validity_history_source
                    ON product_validity_history(product_id, source_batch_id)
                    WHERE source_batch_id IS NOT NULL
                    """
                )
            else:
                columns = {
                    row[1]
                    for row in cur.execute("PRAGMA table_info(products)").fetchall()
                }
                for name, sql_type in {
                    "data_validita_farmaco": "TEXT",
                    "validita_riferimento_at": "TEXT",
                    "validita_iniziale_giorni": "INTEGER",
                    "validita_updated_at": "TEXT",
                    "validita_updated_by": "TEXT",
                }.items():
                    if name not in columns:
                        cur.execute(
                            f"ALTER TABLE products ADD COLUMN {name} {sql_type}"
                        )
                cur.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS product_validity_history (
                        validity_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product_id INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        old_valid_until TEXT,
                        new_valid_until TEXT NOT NULL,
                        reference_at TEXT NOT NULL,
                        initial_days INTEGER NOT NULL,
                        reason TEXT,
                        source_batch_id TEXT,
                        created_at TEXT NOT NULL,
                        created_by TEXT,
                        FOREIGN KEY (product_id) REFERENCES products(product_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_product_validity_history_product
                        ON product_validity_history(product_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_products_validity
                        ON products(data_validita_farmaco);
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_product_validity_history_source
                        ON product_validity_history(product_id, source_batch_id)
                        WHERE source_batch_id IS NOT NULL;
                    """
                )
            conn.commit()
            _SCHEMA_READY.add(key)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    _original_order_init_db(db_path)
    ensure_validity_schema(db_path)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def validity_metrics(
    valid_until: Any,
    reference_at: Any,
    initial_days: Any,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    current_day = today or date.today()
    expiry = parse_date(valid_until)
    reference_dt = _as_datetime(reference_at)
    reference_day = reference_dt.date() if reference_dt else None

    try:
        initial = int(initial_days) if initial_days is not None else None
    except (TypeError, ValueError):
        initial = None

    if expiry is None or reference_day is None:
        return {
            "status": STATUS_MISSING,
            "valid_until": expiry,
            "reference_date": reference_day,
            "initial_days": initial,
            "elapsed_days": None,
            "remaining_days": None,
            "consumed_ratio": None,
            "consumed_percent": None,
            "threshold_date": None,
        }

    if initial is None or initial <= 0:
        initial = (expiry - reference_day).days

    if initial <= 0:
        return {
            "status": STATUS_EXPIRED if current_day >= expiry else STATUS_MISSING,
            "valid_until": expiry,
            "reference_date": reference_day,
            "initial_days": initial,
            "elapsed_days": max(0, (current_day - reference_day).days),
            "remaining_days": (expiry - current_day).days,
            "consumed_ratio": 1.0 if current_day >= expiry else None,
            "consumed_percent": 100.0 if current_day >= expiry else None,
            "threshold_date": reference_day,
        }

    elapsed = max(0, (current_day - reference_day).days)
    remaining = (expiry - current_day).days
    ratio = elapsed / initial
    threshold_date = reference_day + timedelta(
        days=math.ceil(initial * CRITICAL_RATIO)
    )

    if current_day >= expiry:
        status = STATUS_EXPIRED
    elif ratio >= CRITICAL_RATIO:
        status = STATUS_CRITICAL
    elif ratio >= ATTENTION_RATIO:
        status = STATUS_ATTENTION
    else:
        status = STATUS_REGULAR

    return {
        "status": status,
        "valid_until": expiry,
        "reference_date": reference_day,
        "initial_days": initial,
        "elapsed_days": elapsed,
        "remaining_days": remaining,
        "consumed_ratio": ratio,
        "consumed_percent": round(ratio * 100, 1),
        "threshold_date": threshold_date,
    }


def _fetch_products_by_keys(
    product_keys: list[str],
    db_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    keys = list(dict.fromkeys(str(value) for value in product_keys if value))
    if not keys:
        return {}

    ensure_validity_schema(db_path)
    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
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
        columns = [
            "product_id",
            "product_key",
            "data_validita_farmaco",
            "validita_riferimento_at",
            "validita_iniziale_giorni",
        ]
        result: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            item = dict(row) if hasattr(row, "keys") else dict(zip(columns, row))
            result[str(item["product_key"])] = item
        return result
    finally:
        conn.close()


def apply_validity_validation(
    result: dict[str, Any],
    workbook_data,
    schema: dict,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    cfg = schema.get("validity", {})
    if not cfg.get("enabled", False):
        return result

    records = list(workbook_data.records)
    source_rows = list(workbook_data.source_rows)
    product_keys = [
        order_management.product_key(record)
        for record in records
        if parse_date(record.get(VALIDITY_FIELD)) is not None
    ]
    existing = _fetch_products_by_keys(product_keys, db_path)
    today = date.today()

    known_issue_keys = {
        (
            str(issue.get("Riga Excel")),
            str(issue.get("Campo")),
            str(issue.get("Codice Errore")),
        )
        for issue in result.get("issues", [])
    }

    for record, excel_row in zip(records, source_rows):
        incoming = parse_date(record.get(VALIDITY_FIELD))
        if incoming is None or incoming <= today:
            continue

        pkey = order_management.product_key(record)
        current = existing.get(pkey)
        if not current:
            continue

        current_validity = parse_date(current.get("data_validita_farmaco"))
        if current_validity is not None and incoming != current_validity:
            if incoming <= current_validity:
                key = (str(excel_row), VALIDITY_FIELD, "RINNOVO_VALIDITA_NON_PROGRESSIVO")
                if key not in known_issue_keys:
                    add_issue(
                        result["issues"],
                        excel_row,
                        VALIDITY_FIELD,
                        record.get(VALIDITY_FIELD),
                        "RINNOVO_VALIDITA_NON_PROGRESSIVO",
                        LEVEL_BLOCK,
                        "La nuova Data Validità Farmaco deve essere successiva alla validità già presente a listino.",
                        f"Validità corrente a listino: {current_validity.strftime('%d/%m/%Y')}.",
                    )
                    known_issue_keys.add(key)
            continue

        if current_validity is None or incoming != current_validity:
            continue

        metrics = validity_metrics(
            current_validity,
            current.get("validita_riferimento_at"),
            current.get("validita_iniziale_giorni"),
            today=today,
        )
        status = metrics["status"]

        if status == STATUS_CRITICAL:
            key = (str(excel_row), VALIDITY_FIELD, "VALIDITA_OLTRE_SOGLIA_2_3")
            if key not in known_issue_keys:
                pct = metrics.get("consumed_percent")
                threshold = metrics.get("threshold_date")
                add_issue(
                    result["issues"],
                    excel_row,
                    VALIDITY_FIELD,
                    record.get(VALIDITY_FIELD),
                    "VALIDITA_OLTRE_SOGLIA_2_3",
                    LEVEL_BLOCK,
                    "La stessa validità è già presente a listino e ha consumato almeno i 2/3 della vita residua iniziale: il prodotto deve essere respinto o rinnovato.",
                    (
                        f"Vita consumata: {pct:.1f}% · "
                        f"soglia 2/3: {threshold.strftime('%d/%m/%Y') if threshold else '-'}."
                    ),
                )
                known_issue_keys.add(key)
        elif status == STATUS_ATTENTION:
            key = (str(excel_row), VALIDITY_FIELD, "VALIDITA_IN_ATTENZIONE")
            if key not in known_issue_keys:
                pct = metrics.get("consumed_percent")
                add_issue(
                    result["issues"],
                    excel_row,
                    VALIDITY_FIELD,
                    record.get(VALIDITY_FIELD),
                    "VALIDITA_IN_ATTENZIONE",
                    LEVEL_WARNING,
                    "La validità è oltre il 50% della vita residua iniziale e si sta avvicinando alla soglia bloccante dei 2/3.",
                    f"Vita consumata: {pct:.1f}%.",
                )
                known_issue_keys.add(key)

    result["blocking_count"] = sum(
        1 for issue in result.get("issues", []) if issue.get("Livello") == LEVEL_BLOCK
    )
    result["warning_count"] = sum(
        1 for issue in result.get("issues", []) if issue.get("Livello") == LEVEL_WARNING
    )
    result["info_count"] = sum(
        1 for issue in result.get("issues", []) if issue.get("Livello") == "INFO"
    )
    result["is_valid"] = result["blocking_count"] == 0
    return result


def preview_publication(
    records: list[dict],
    db_path: str | Path | None = None,
) -> dict:
    ensure_validity_schema(db_path)
    preview = _original_master_preview(records, db_path)
    existing = _fetch_products_by_keys(
        [order_management.product_key(record) for record in records],
        db_path,
    )

    for record, detail in zip(records, preview.get("details", [])):
        pkey = order_management.product_key(record)
        current = existing.get(pkey)
        if not current:
            continue

        incoming = parse_date(record.get(VALIDITY_FIELD))
        current_validity = parse_date(current.get("data_validita_farmaco"))
        if incoming is None or incoming == current_validity:
            continue

        old_changes = str(detail.get("Campi prodotto variati") or "").strip()
        pieces = [value for value in [old_changes, VALIDITY_FIELD] if value]
        detail["Campi prodotto variati"] = ", ".join(dict.fromkeys(pieces))

        action = str(detail.get("Azione") or "").upper()
        if action == "INVARIATO":
            detail["Azione"] = "DATI MODIFICATI"
            preview["unchanged"] = max(0, int(preview.get("unchanged", 0)) - 1)
            preview["data_changes"] = int(preview.get("data_changes", 0)) + 1
            preview["updated_offers"] = int(preview.get("updated_offers", 0)) + 1
        elif action == "PREZZO MODIFICATO":
            detail["Azione"] = "PREZZO + ANAGRAFICA"
            preview["data_changes"] = int(preview.get("data_changes", 0)) + 1
        elif action == "NUOVA OFFERTA":
            preview["data_changes"] = int(preview.get("data_changes", 0)) + 1

    return preview


def _parse_publication_datetime(value: Any) -> datetime:
    parsed = _as_datetime(value)
    return parsed or datetime.now()


def _persist_publication_validity(
    records: list[dict],
    result: dict[str, Any],
    db_path: str | Path | None = None,
) -> None:
    ensure_validity_schema(db_path)
    published_at = _parse_publication_datetime(result.get("published_at"))
    actor = (current_user() or {}).get("user_id")
    batch_id = str(result.get("batch_id") or "")

    latest_by_key: dict[str, dict] = {}
    for record in records:
        incoming = parse_date(record.get(VALIDITY_FIELD))
        if incoming is None:
            continue
        latest_by_key[order_management.product_key(record)] = record

    existing = _fetch_products_by_keys(list(latest_by_key), db_path)
    updates: list[tuple] = []
    history_rows: list[tuple] = []

    for pkey, record in latest_by_key.items():
        incoming = parse_date(record.get(VALIDITY_FIELD))
        current = existing.get(pkey)
        if incoming is None or current is None:
            continue

        old_validity = parse_date(current.get("data_validita_farmaco"))
        old_reference = _as_datetime(current.get("validita_riferimento_at"))
        try:
            old_initial = (
                int(current.get("validita_iniziale_giorni"))
                if current.get("validita_iniziale_giorni") is not None
                else None
            )
        except (TypeError, ValueError):
            old_initial = None

        same_window = (
            old_validity == incoming
            and old_reference is not None
            and old_initial is not None
            and old_initial > 0
        )
        if same_window:
            continue

        if old_validity is not None and incoming < old_validity:
            raise ValidityError(
                f"La Data Validità Farmaco per {record.get('AIC') or pkey} "
                "non può essere ridotta durante la pubblicazione."
            )

        initial_days = (incoming - published_at.date()).days
        if initial_days <= 0:
            raise ValidityError(
                f"La Data Validità Farmaco per {record.get('AIC') or pkey} "
                "deve essere successiva alla data di pubblicazione."
            )

        event_type = (
            "FIRST_LOAD"
            if old_validity is None or old_reference is None or not old_initial
            else "TRACE_RENEWAL"
        )
        updates.append(
            (
                int(current["product_id"]),
                incoming,
                published_at,
                initial_days,
                published_at,
                actor,
            )
        )
        history_rows.append(
            (
                int(current["product_id"]),
                event_type,
                old_validity,
                incoming,
                published_at,
                initial_days,
                (
                    "Prima immissione validità da tracciato"
                    if event_type == "FIRST_LOAD"
                    else "Rinnovo validità da tracciato"
                ),
                batch_id or None,
                actor,
            )
        )

    if not updates:
        return

    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        if use_postgres():
            from psycopg2.extras import execute_values

            execute_values(
                cur,
                """
                UPDATE products AS p
                SET data_validita_farmaco = v.data_validita_farmaco::date,
                    validita_riferimento_at = v.validita_riferimento_at::timestamptz,
                    validita_iniziale_giorni = v.validita_iniziale_giorni::integer,
                    validita_updated_at = v.validita_updated_at::timestamptz,
                    validita_updated_by = v.validita_updated_by::uuid
                FROM (VALUES %s) AS v(
                    product_id, data_validita_farmaco, validita_riferimento_at,
                    validita_iniziale_giorni, validita_updated_at, validita_updated_by
                )
                WHERE p.product_id = v.product_id::bigint
                """,
                updates,
                page_size=1000,
            )
            execute_values(
                cur,
                """
                INSERT INTO product_validity_history (
                    product_id, event_type, old_valid_until, new_valid_until,
                    reference_at, initial_days, reason, source_batch_id, created_by
                ) VALUES %s
                """,
                history_rows,
                page_size=1000,
            )
        else:
            cur.executemany(
                """
                UPDATE products
                SET data_validita_farmaco = ?,
                    validita_riferimento_at = ?,
                    validita_iniziale_giorni = ?,
                    validita_updated_at = ?,
                    validita_updated_by = ?
                WHERE product_id = ?
                """,
                [
                    (
                        valid_until.isoformat(),
                        reference_at.isoformat(timespec="seconds"),
                        initial_days,
                        updated_at.isoformat(timespec="seconds"),
                        updated_by,
                        product_id,
                    )
                    for (
                        product_id,
                        valid_until,
                        reference_at,
                        initial_days,
                        updated_at,
                        updated_by,
                    ) in updates
                ],
            )
            cur.executemany(
                """
                INSERT INTO product_validity_history (
                    product_id, event_type, old_valid_until, new_valid_until,
                    reference_at, initial_days, reason, source_batch_id,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        product_id,
                        event_type,
                        old_valid_until.isoformat() if old_valid_until else None,
                        new_valid_until.isoformat(),
                        reference_at.isoformat(timespec="seconds"),
                        initial_days,
                        reason,
                        source_batch_id,
                        datetime.now().isoformat(timespec="seconds"),
                        created_by,
                    )
                    for (
                        product_id,
                        event_type,
                        old_valid_until,
                        new_valid_until,
                        reference_at,
                        initial_days,
                        reason,
                        source_batch_id,
                        created_by,
                    ) in history_rows
                ],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def publish_core_with_validity(
    records: list[dict],
    source_name: str,
    db_path: str | Path | None,
    app_version: str = "demo",
    archive_path: str | None = None,
) -> dict:
    result = _original_publish_core(
        records=records,
        source_name=source_name,
        db_path=db_path,
        app_version=app_version,
        archive_path=archive_path,
    )
    try:
        _persist_publication_validity(records, result, db_path)
    except Exception as exc:
        # Il core R13/R10 può essere già stato committato. Non perdiamo il
        # riferimento al batch: il guard R14.1 potrà riconciliare solo la
        # validità, senza mascherare errori avvenuti prima di questo punto.
        raise ValidityPersistenceError(result, exc) from exc
    return result


def _validity_column_dataframe(
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    ensure_validity_schema(db_path)
    conn = order_management._connect(db_path)
    try:
        query = """
            SELECT product_id,
                   data_validita_farmaco AS "Data Validità Farmaco"
            FROM products
        """
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def catalogue_dataframe(
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    df = _original_catalogue_dataframe(db_path)
    if "Data Validità Farmaco" in df.columns:
        return df
    if df.empty:
        df[VALIDITY_FIELD] = pd.Series(dtype="object")
        return df
    validity = _validity_column_dataframe(db_path)
    return df.merge(validity, how="left", on="product_id")


def catalogue_with_validity_metrics(
    db_path: str | Path | None = None,
    *,
    today: date | None = None,
) -> pd.DataFrame:
    """Catalogo corrente arricchito con metriche R14, senza alterare l'export ERP."""

    df = catalogue_dataframe(db_path)
    derived_columns = [
        "Stato Validità",
        "Giorni Residui",
        "Vita Consumata %",
        "Soglia Critica",
    ]
    if df.empty:
        for column in derived_columns:
            if column not in df.columns:
                df[column] = pd.Series(dtype="object")
        return df

    ensure_validity_schema(db_path)
    conn = order_management._connect(db_path)
    try:
        metadata = pd.read_sql_query(
            """
            SELECT product_id,
                   validita_riferimento_at AS __validity_reference,
                   validita_iniziale_giorni AS __validity_initial
            FROM products
            """,
            conn,
        )
    finally:
        conn.close()

    out = df.merge(metadata, how="left", on="product_id")
    current_day = today or date.today()
    statuses = []
    remaining = []
    consumed = []
    thresholds = []
    for _, row in out.iterrows():
        metrics = validity_metrics(
            row.get(VALIDITY_FIELD),
            row.get("__validity_reference"),
            row.get("__validity_initial"),
            today=current_day,
        )
        statuses.append(metrics.get("status"))
        remaining.append(metrics.get("remaining_days"))
        consumed.append(metrics.get("consumed_percent"))
        threshold = metrics.get("threshold_date")
        thresholds.append(threshold.isoformat() if threshold else None)

    out["Stato Validità"] = statuses
    out["Giorni Residui"] = remaining
    out["Vita Consumata %"] = consumed
    out["Soglia Critica"] = thresholds
    return out.drop(columns=["__validity_reference", "__validity_initial"], errors="ignore")


def validity_monitor_dataframe(
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    init_db(db_path)
    conn = order_management._connect(db_path)
    try:
        if use_postgres():
            query = """
                SELECT
                    p.product_id AS "ID",
                    p.aic AS "AIC",
                    p.nome_commerciale AS "Nome Commerciale",
                    p.data_validita_farmaco AS "Data Validità Farmaco",
                    p.validita_riferimento_at AS "Data riferimento",
                    p.validita_iniziale_giorni AS "Giorni iniziali",
                    COALESCE(p.catalogue_status, 'VISIBLE') AS "Stato Catalogo",
                    STRING_AGG(DISTINCT o.fornitore, ' | ') AS "Fornitori"
                FROM products p
                LEFT JOIN offers o
                  ON o.product_id = p.product_id AND o.active = 1
                WHERE COALESCE(p.catalogue_status, 'VISIBLE') <> 'ARCHIVED'
                GROUP BY
                    p.product_id, p.aic, p.nome_commerciale,
                    p.data_validita_farmaco, p.validita_riferimento_at,
                    p.validita_iniziale_giorni, p.catalogue_status
                ORDER BY LOWER(COALESCE(p.nome_commerciale, '')), p.product_id
            """
        else:
            query = """
                SELECT
                    p.product_id AS "ID",
                    p.aic AS "AIC",
                    p.nome_commerciale AS "Nome Commerciale",
                    p.data_validita_farmaco AS "Data Validità Farmaco",
                    p.validita_riferimento_at AS "Data riferimento",
                    p.validita_iniziale_giorni AS "Giorni iniziali",
                    COALESCE(p.catalogue_status, 'VISIBLE') AS "Stato Catalogo",
                    GROUP_CONCAT(DISTINCT o.fornitore) AS "Fornitori"
                FROM products p
                LEFT JOIN offers o
                  ON o.product_id = p.product_id AND o.active = 1
                WHERE COALESCE(p.catalogue_status, 'VISIBLE') <> 'ARCHIVED'
                GROUP BY
                    p.product_id, p.aic, p.nome_commerciale,
                    p.data_validita_farmaco, p.validita_riferimento_at,
                    p.validita_iniziale_giorni, p.catalogue_status
                ORDER BY LOWER(COALESCE(p.nome_commerciale, '')), p.product_id
            """
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()

    if df.empty:
        for name in (
            "Giorni residui",
            "% vita consumata",
            "Soglia 2/3",
            "Stato validità",
        ):
            df[name] = pd.Series(dtype="object")
        return df

    statuses = []
    remaining_days = []
    consumed = []
    thresholds = []
    validity_dates = []
    reference_dates = []

    for _, row in df.iterrows():
        metrics = validity_metrics(
            row.get(VALIDITY_FIELD),
            row.get("Data riferimento"),
            row.get("Giorni iniziali"),
        )
        statuses.append(metrics["status"])
        remaining_days.append(metrics["remaining_days"])
        consumed.append(metrics["consumed_percent"])
        thresholds.append(
            metrics["threshold_date"].isoformat()
            if metrics["threshold_date"] is not None
            else None
        )
        validity_dates.append(
            metrics["valid_until"].isoformat()
            if metrics["valid_until"] is not None
            else None
        )
        reference_dates.append(
            metrics["reference_date"].isoformat()
            if metrics["reference_date"] is not None
            else None
        )

    df[VALIDITY_FIELD] = validity_dates
    df["Data riferimento"] = reference_dates
    df["Giorni residui"] = remaining_days
    df["% vita consumata"] = consumed
    df["Soglia 2/3"] = thresholds
    df["Stato validità"] = statuses
    return df


def validity_history_dataframe(
    product_id: int,
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    init_db(db_path)
    conn = order_management._connect(db_path)
    try:
        ph = "%s" if use_postgres() else "?"
        query = f"""
            SELECT
                event_type AS "Evento",
                old_valid_until AS "Validità precedente",
                new_valid_until AS "Nuova validità",
                reference_at AS "Data riferimento",
                initial_days AS "Giorni iniziali",
                reason AS "Motivazione",
                source_batch_id AS "Batch / riferimento",
                created_at AS "Registrato il"
            FROM product_validity_history
            WHERE product_id = {ph}
            ORDER BY validity_history_id DESC
        """
        return pd.read_sql_query(query, conn, params=(int(product_id),))
    finally:
        conn.close()


def _renewal_snapshot_rows(cur, product_id: int) -> list[tuple[int | None, dict[str, Any]]]:
    ph = "%s" if use_postgres() else "?"
    cur.execute(
        f"""
        SELECT
            o.offer_id,
            o.fornitore,
            p.aic,
            o.codice_fornitore,
            p.nome_commerciale,
            p.principio_attivo,
            p.forma_farmaceutica,
            p.materiale_pericoloso,
            p.stupefacente,
            p.atc7,
            p.atc9,
            p.fala_lasa,
            p.gruppo_stivaggio,
            p.temperatura_stivaggio,
            o.prezzo_unitario,
            o.prezzo_confezione,
            p.upc,
            o.minimo_movimentabile,
            o.iva,
            p.note,
            p.x,
            p.y,
            p.z,
            p.data_validita_farmaco
        FROM products p
        JOIN offers o ON o.product_id = p.product_id
        WHERE p.product_id = {ph} AND o.active = 1
        ORDER BY o.offer_id
        """,
        (int(product_id),),
    )
    columns = [
        "offer_id",
        "Fornitore",
        "AIC",
        "Codice Fornitore",
        "Nome Commerciale",
        "Principio Attivo",
        "Forma Farmaceutica",
        "Materiale Pericoloso",
        "Stupefacente",
        "ATC7",
        "ATC9",
        "Fala / Lasa",
        "Gruppo di Stivaggio",
        "Temperatura di Stivaggio",
        "Prezzo Unitario",
        "Prezzo Confezione",
        "UPC",
        "Minimo Movimentabile",
        "IVA",
        "Note",
        "X",
        "Y",
        "Z",
        VALIDITY_FIELD,
    ]
    snapshots: list[tuple[int | None, dict[str, Any]]] = []
    for row in cur.fetchall():
        item = dict(row) if hasattr(row, "keys") else dict(zip(columns, row))
        offer_id = item.pop("offer_id", None)
        snapshots.append(
            (
                int(offer_id) if offer_id is not None else None,
                order_management._normalize_payload(item),
            )
        )
    return snapshots


def renew_product_validity(
    product_id: int,
    new_valid_until: Any,
    reason: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    user = current_user() or {}
    role = str(user.get("role_id") or "").upper()
    if role not in {"BUYER", "ADMIN"}:
        raise ValidityError(
            "Il rinnovo della validità è riservato ai profili Buyer e Admin."
        )

    reason = str(reason or "").strip()
    if not reason:
        raise ValidityError("Inserisci una motivazione per il rinnovo.")
    if len(reason) > 1000:
        raise ValidityError("La motivazione non può superare 1000 caratteri.")

    new_date = parse_date(new_valid_until)
    if new_date is None:
        raise ValidityError("Inserisci una nuova Data Validità Farmaco valida.")
    today = date.today()
    if new_date <= today:
        raise ValidityError("La nuova Data Validità Farmaco deve essere futura.")

    reference_at = datetime.now()
    initial_days = (new_date - reference_at.date()).days
    source_ref = (
        f"RENEWAL-{reference_at.strftime('%Y%m%d-%H%M%S')}-"
        f"{uuid.uuid4().hex[:6].upper()}"
    )
    actor = user.get("user_id")

    conn = order_management._connect(db_path)
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"
        lock = " FOR UPDATE" if use_postgres() else ""
        cur.execute(
            f"""
            SELECT product_id, aic, nome_commerciale, data_validita_farmaco
            FROM products
            WHERE product_id = {ph}{lock}
            """,
            (int(product_id),),
        )
        row = cur.fetchone()
        if not row:
            raise ValidityError("Prodotto non trovato.")

        if hasattr(row, "keys"):
            product = dict(row)
        else:
            product = dict(
                zip(
                    ["product_id", "aic", "nome_commerciale", "data_validita_farmaco"],
                    row,
                )
            )

        old_date = parse_date(product.get("data_validita_farmaco"))
        if old_date is not None and new_date <= old_date:
            raise ValidityError(
                "Il rinnovo deve impostare una data successiva alla validità corrente."
            )

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
                (
                    new_date,
                    reference_at,
                    initial_days,
                    reference_at,
                    actor,
                    int(product_id),
                ),
            )
            cur.execute(
                """
                INSERT INTO product_validity_history (
                    product_id, event_type, old_valid_until, new_valid_until,
                    reference_at, initial_days, reason, source_batch_id, created_by
                )
                VALUES (%s, 'BUYER_RENEWAL', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(product_id),
                    old_date,
                    new_date,
                    reference_at,
                    initial_days,
                    reason,
                    source_ref,
                    actor,
                ),
            )
        else:
            now_text = reference_at.isoformat(timespec="seconds")
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
                (
                    new_date.isoformat(),
                    now_text,
                    initial_days,
                    now_text,
                    actor,
                    int(product_id),
                ),
            )
            cur.execute(
                """
                INSERT INTO product_validity_history (
                    product_id, event_type, old_valid_until, new_valid_until,
                    reference_at, initial_days, reason, source_batch_id,
                    created_at, created_by
                ) VALUES (?, 'BUYER_RENEWAL', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(product_id),
                    old_date.isoformat() if old_date else None,
                    new_date.isoformat(),
                    now_text,
                    initial_days,
                    reason,
                    source_ref,
                    now_text,
                    actor,
                ),
            )

        snapshots = _renewal_snapshot_rows(cur, int(product_id))
        for offer_id, payload in snapshots:
            order_management._insert_delta_event(
                cur,
                event_key=f"BUYER_RENEWAL|{source_ref}|{offer_id or 'NOOFFER'}",
                product_id=int(product_id),
                offer_id=offer_id,
                action="UPDATE",
                source_type="BUYER",
                source_batch_id=source_ref,
                payload=payload,
                created_by=actor,
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    audit(
        "VALIDITY_RENEWAL",
        entity_type="product",
        entity_id=str(product_id),
        details={
            "old_valid_until": old_date.isoformat() if old_date else None,
            "new_valid_until": new_date.isoformat(),
            "reference_at": reference_at.isoformat(timespec="seconds"),
            "initial_days": initial_days,
            "reason": reason,
            "source_ref": source_ref,
        },
    )
    return {
        "product_id": int(product_id),
        "aic": product.get("aic"),
        "name": product.get("nome_commerciale"),
        "old_valid_until": old_date.isoformat() if old_date else None,
        "new_valid_until": new_date.isoformat(),
        "reference_at": reference_at.isoformat(timespec="seconds"),
        "initial_days": initial_days,
        "source_ref": source_ref,
    }


# Apply R14 patches. Import order is intentional: R13/R13.1 load first in app.py
# and catalog_db.py, then this layer extends the current publication path.
order_management.init_db = init_db
master22.preview_publication = preview_publication

# R13 usa una funzione di preview locale nel proprio master22_publish_records.
# La sostituiamo esplicitamente affinché anche la scrittura di publication_rows
# e la successiva coda ERP vedano Data Validità Farmaco come modifica anagrafica.
if use_postgres():
    try:
        import modules.catalog_db_postgres_r13 as _r13
        _r13.master22_preview_publication = preview_publication
    except Exception:
        pass

order_management._r10_publish_records = publish_core_with_validity
order_management.catalogue_dataframe = catalogue_dataframe

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd

from modules.auth import audit, current_user
from modules.catalog_db_sqlite import offer_key, product_key
from modules.catalogue_visibility import (
    STATUS_ARCHIVED,
    STATUS_COLUMN,
    STATUS_HIDDEN,
    STATUS_VISIBLE,
    init_db as _r11_init_db,
    set_product_status as _r11_set_product_status,
)
from modules.db_backend import get_postgres_url, use_postgres
from modules.master22_catalog import (
    catalogue_dataframe as _r10_catalogue_dataframe,
    export_catalogue_excel as _r10_export_catalogue_excel,
    publish_records as _r10_publish_records,
)


ROLE_ORDER_MANAGEMENT = "ORDER_MANAGEMENT"
PERMISSION_EDIT_CATALOGUE = "edit_catalogue"
PERMISSION_EXPORT_ERP = "export_erp"

MASTER_COLUMNS = [
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
    "Data Validità Farmaco",
]

PRODUCT_EDIT_FIELDS = {
    "Nome Commerciale": "nome_commerciale",
    "Principio Attivo": "principio_attivo",
    "Forma Farmaceutica": "forma_farmaceutica",
    "Materiale Pericoloso": "materiale_pericoloso",
    "Stupefacente": "stupefacente",
    "ATC7": "atc7",
    "ATC9": "atc9",
    "Fala / Lasa": "fala_lasa",
    "Gruppo di Stivaggio": "gruppo_stivaggio",
    "Temperatura di Stivaggio": "temperatura_stivaggio",
    "UPC": "upc",
    "Note": "note",
    "X": "x",
    "Y": "y",
    "Z": "z",
}

OFFER_EDIT_FIELDS = {
    "Prezzo Unitario": "prezzo_unitario",
    "Prezzo Confezione": "prezzo_confezione",
    "Minimo Movimentabile": "minimo_movimentabile",
    "IVA": "iva",
}

_SCHEMA_READY: set[str] = set()
_SCHEMA_LOCK = Lock()


class OrderManagementError(RuntimeError):
    pass


def _connect(db_path: str | Path | None = None):
    if use_postgres():
        import psycopg2

        url = get_postgres_url()
        if not url:
            raise OrderManagementError("Connessione PostgreSQL non configurata.")
        return psycopg2.connect(
            url,
            connect_timeout=10,
            sslmode="require",
            application_name="listino-farmaci-order-management",
        )

    path = Path(db_path or "data/listino.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _schema_key(db_path: str | Path | None = None) -> str:
    if use_postgres():
        return "postgres"
    return f"sqlite:{Path(db_path or 'data/listino.db').resolve()}"


def _insert_permission_postgres(cur, permission_id: str, display_name: str, description: str) -> None:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'permissions'
        """
    )
    columns_available = {row[0] for row in cur.fetchall()}
    if "permission_id" not in columns_available:
        return

    columns = ["permission_id"]
    values: list[Any] = [permission_id]
    if "display_name" in columns_available:
        columns.append("display_name")
        values.append(display_name)
    elif "name" in columns_available:
        columns.append("name")
        values.append(display_name)
    if "description" in columns_available:
        columns.append("description")
        values.append(description)

    placeholders = ", ".join(["%s"] * len(columns))
    assignments = [
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column != "permission_id"
    ]
    conflict = "DO UPDATE SET " + ", ".join(assignments) if assignments else "DO NOTHING"
    cur.execute(
        f"INSERT INTO public.permissions ({', '.join(columns)}) "
        f"VALUES ({placeholders}) ON CONFLICT (permission_id) {conflict}",
        tuple(values),
    )


def _ensure_rbac_postgres(cur) -> None:
    cur.execute(
        """
        INSERT INTO public.roles (role_id, display_name, description)
        VALUES (
            'ORDER_MANAGEMENT',
            'Order Management',
            'Gestione operativa del listino e dei delta destinati al gestionale ERP.'
        )
        ON CONFLICT (role_id) DO UPDATE
        SET display_name = EXCLUDED.display_name,
            description = EXCLUDED.description
        """
    )

    _insert_permission_postgres(
        cur,
        PERMISSION_EDIT_CATALOGUE,
        "Modifica catalogo",
        "Consente di correggere i dati correnti di prodotto e offerta con audit.",
    )
    _insert_permission_postgres(
        cur,
        PERMISSION_EXPORT_ERP,
        "Export ERP",
        "Consente di preparare, scaricare e confermare i delta CSV verso ERP.",
    )

    order_permissions = [
        "access_app",
        "view_catalogue",
        "export_catalogue",
        "view_publications",
        "view_history",
        "manage_catalogue",
        PERMISSION_EDIT_CATALOGUE,
        PERMISSION_EXPORT_ERP,
    ]
    admin_permissions = [
        "manage_catalogue",
        PERMISSION_EDIT_CATALOGUE,
        PERMISSION_EXPORT_ERP,
    ]

    for permission_id in order_permissions:
        cur.execute(
            """
            INSERT INTO public.role_permissions (role_id, permission_id)
            SELECT 'ORDER_MANAGEMENT', %s
            WHERE EXISTS (
                SELECT 1 FROM public.permissions WHERE permission_id = %s
            )
            ON CONFLICT DO NOTHING
            """,
            (permission_id, permission_id),
        )

    for permission_id in admin_permissions:
        cur.execute(
            """
            INSERT INTO public.role_permissions (role_id, permission_id)
            SELECT 'ADMIN', %s
            WHERE EXISTS (
                SELECT 1 FROM public.permissions WHERE permission_id = %s
            )
            ON CONFLICT DO NOTHING
            """,
            (permission_id, permission_id),
        )

    cur.execute(
        """
        DELETE FROM public.role_permissions
        WHERE permission_id IN ('edit_catalogue', 'export_erp')
          AND role_id NOT IN ('ADMIN', 'ORDER_MANAGEMENT')
        """
    )


def ensure_order_management_schema(db_path: str | Path | None = None) -> None:
    key = _schema_key(db_path)
    if key in _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            return

        conn = _connect(db_path)
        try:
            cur = conn.cursor()
            if use_postgres():
                _ensure_rbac_postgres(cur)
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.erp_export_packages (
                        package_id TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        created_by UUID,
                        row_count INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'EXPORTED',
                        confirmed_at TIMESTAMPTZ,
                        confirmed_by UUID,
                        CONSTRAINT erp_export_packages_status_check
                            CHECK (status IN ('EXPORTED', 'IMPORTED'))
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS public.erp_delta_events (
                        event_id BIGSERIAL PRIMARY KEY,
                        event_key TEXT NOT NULL UNIQUE,
                        product_id BIGINT,
                        offer_id BIGINT,
                        action TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_batch_id TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        created_by UUID,
                        export_status TEXT NOT NULL DEFAULT 'PENDING',
                        export_package_id TEXT,
                        exported_at TIMESTAMPTZ,
                        imported_at TIMESTAMPTZ,
                        imported_by UUID,
                        CONSTRAINT erp_delta_action_check
                            CHECK (action IN ('INSERT', 'UPDATE', 'DISABLE', 'REACTIVATE')),
                        CONSTRAINT erp_delta_status_check
                            CHECK (export_status IN ('PENDING', 'EXPORTED', 'IMPORTED')),
                        CONSTRAINT fk_erp_delta_package
                            FOREIGN KEY (export_package_id)
                            REFERENCES public.erp_export_packages(package_id)
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_erp_delta_status ON public.erp_delta_events(export_status, created_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_erp_delta_batch ON public.erp_delta_events(source_batch_id)"
                )
            else:
                cur.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS erp_export_packages (
                        package_id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        created_by TEXT,
                        row_count INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'EXPORTED',
                        confirmed_at TEXT,
                        confirmed_by TEXT
                    );
                    CREATE TABLE IF NOT EXISTS erp_delta_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_key TEXT NOT NULL UNIQUE,
                        product_id INTEGER,
                        offer_id INTEGER,
                        action TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_batch_id TEXT,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        created_by TEXT,
                        export_status TEXT NOT NULL DEFAULT 'PENDING',
                        export_package_id TEXT,
                        exported_at TEXT,
                        imported_at TEXT,
                        imported_by TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_erp_delta_status
                        ON erp_delta_events(export_status, created_at);
                    CREATE INDEX IF NOT EXISTS idx_erp_delta_batch
                        ON erp_delta_events(source_batch_id);
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
    _r11_init_db(db_path)
    ensure_order_management_schema(db_path)


def _require_permission(permission_id: str) -> dict[str, Any]:
    user = current_user()
    permissions = set((user or {}).get("permissions") or [])
    if not user or permission_id not in permissions:
        raise OrderManagementError("Non hai i privilegi necessari per questa operazione.")
    return user


def _status_dataframe(db_path: str | Path | None = None) -> pd.DataFrame:
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            'SELECT product_id, catalogue_status AS "Stato Catalogo" FROM products',
            conn,
        )
    finally:
        conn.close()


def catalogue_dataframe(db_path: str | Path | None = None) -> pd.DataFrame:
    init_db(db_path)
    df = _r10_catalogue_dataframe(db_path)
    if df.empty:
        if STATUS_COLUMN not in df.columns:
            df[STATUS_COLUMN] = pd.Series(dtype="object")
        return df

    df = df.merge(_status_dataframe(db_path), how="left", on="product_id")
    df[STATUS_COLUMN] = df[STATUS_COLUMN].fillna(STATUS_VISIBLE)
    role = str((current_user() or {}).get("role_id") or "").upper()
    status = df[STATUS_COLUMN].astype(str).str.upper()

    if role in {"ADMIN", "BUYER", "CUSTOMER_CARE", ROLE_ORDER_MANAGEMENT}:
        return df.loc[status.ne(STATUS_ARCHIVED)].copy()
    return df.loc[status.eq(STATUS_VISIBLE)].copy()


def export_catalogue_excel(
    db_path: str | Path | None,
    dataframe: pd.DataFrame | None = None,
) -> bytes:
    df = dataframe.copy() if dataframe is not None else catalogue_dataframe(db_path)
    role = str((current_user() or {}).get("role_id") or "").upper()
    if role == "CLIENTE":
        df = df.drop(columns=[STATUS_COLUMN], errors="ignore")
    return _r10_export_catalogue_excel(db_path, df)


def _normalize_payload(record: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for column in MASTER_COLUMNS:
        value = record.get(column)
        try:
            if pd.isna(value):
                value = None
        except Exception:
            pass
        if hasattr(value, "item"):
            try:
                value = value.item()
            except Exception:
                pass
        payload[column] = value
    return payload


def _action_from_publication(action: str) -> str | None:
    value = str(action or "").strip().upper()
    if value in {"NUOVO PRODOTTO", "NUOVA OFFERTA"}:
        return "INSERT"
    if value in {"PREZZO MODIFICATO", "DATI MODIFICATI", "PREZZO + ANAGRAFICA"}:
        return "UPDATE"
    return None


def _lookup_ids(cur, pkey: str, okey: str) -> tuple[int | None, int | None]:
    placeholder = "%s" if use_postgres() else "?"
    cur.execute(f"SELECT product_id FROM products WHERE product_key = {placeholder}", (pkey,))
    row = cur.fetchone()
    product_id = int(row[0]) if row else None
    cur.execute(f"SELECT offer_id FROM offers WHERE offer_key = {placeholder}", (okey,))
    row = cur.fetchone()
    offer_id = int(row[0]) if row else None
    return product_id, offer_id


def _insert_delta_event(
    cur,
    *,
    event_key: str,
    product_id: int | None,
    offer_id: int | None,
    action: str,
    source_type: str,
    source_batch_id: str | None,
    payload: dict[str, Any],
    created_by: str | None,
) -> None:
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
    if use_postgres():
        cur.execute(
            """
            INSERT INTO public.erp_delta_events (
                event_key, product_id, offer_id, action, source_type,
                source_batch_id, payload_json, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_key) DO NOTHING
            """,
            (
                event_key,
                product_id,
                offer_id,
                action,
                source_type,
                source_batch_id,
                payload_json,
                created_by,
            ),
        )
    else:
        cur.execute(
            """
            INSERT OR IGNORE INTO erp_delta_events (
                event_key, product_id, offer_id, action, source_type,
                source_batch_id, payload_json, created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key,
                product_id,
                offer_id,
                action,
                source_type,
                source_batch_id,
                payload_json,
                datetime.now().isoformat(timespec="seconds"),
                created_by,
            ),
        )


def _queue_buyer_delta(records: list[dict], result: dict, db_path=None) -> None:
    batch_id = result.get("batch_id")
    if not batch_id:
        return
    details = result.get("details") or []
    actor = (current_user() or {}).get("user_id")
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        for record, detail in zip(records, details):
            action = _action_from_publication(detail.get("Azione"))
            if not action:
                continue
            pkey = product_key(record)
            okey = offer_key(record)
            product_id, offer_id = _lookup_ids(cur, pkey, okey)
            _insert_delta_event(
                cur,
                event_key=f"BUYER|{batch_id}|{okey}",
                product_id=product_id,
                offer_id=offer_id,
                action=action,
                source_type="BUYER",
                source_batch_id=batch_id,
                payload=_normalize_payload(record),
                created_by=actor,
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
    init_db(db_path)
    result = _r10_publish_records(
        records=records,
        source_name=source_name,
        db_path=db_path,
        app_version=app_version,
        archive_path=archive_path,
    )
    _queue_buyer_delta(records, result, db_path)
    return result


def _current_rows_for_product(product_id: int, db_path=None) -> list[tuple[int | None, dict[str, Any]]]:
    conn = _connect(db_path)
    try:
        placeholder = "%s" if use_postgres() else "?"
        query = f"""
            SELECT
                o.offer_id,
                o.fornitore AS "Fornitore",
                p.aic AS "AIC",
                o.codice_fornitore AS "Codice Fornitore",
                p.nome_commerciale AS "Nome Commerciale",
                p.principio_attivo AS "Principio Attivo",
                p.forma_farmaceutica AS "Forma Farmaceutica",
                p.materiale_pericoloso AS "Materiale Pericoloso",
                p.stupefacente AS "Stupefacente",
                p.atc7 AS "ATC7",
                p.atc9 AS "ATC9",
                p.fala_lasa AS "Fala / Lasa",
                p.gruppo_stivaggio AS "Gruppo di Stivaggio",
                p.temperatura_stivaggio AS "Temperatura di Stivaggio",
                o.prezzo_unitario AS "Prezzo Unitario",
                o.prezzo_confezione AS "Prezzo Confezione",
                p.upc AS "UPC",
                o.minimo_movimentabile AS "Minimo Movimentabile",
                o.iva AS "IVA",
                p.note AS "Note",
                p.x AS "X",
                p.y AS "Y",
                p.z AS "Z",
                p.data_validita_farmaco AS "Data Validità Farmaco"
            FROM products p
            JOIN offers o ON o.product_id = p.product_id
            WHERE p.product_id = {placeholder} AND o.active = 1
            ORDER BY o.offer_id
        """
        df = pd.read_sql_query(query, conn, params=(int(product_id),))
        rows: list[tuple[int | None, dict[str, Any]]] = []
        for _, row in df.iterrows():
            offer_id = int(row["offer_id"]) if pd.notna(row["offer_id"]) else None
            rows.append((offer_id, _normalize_payload(row.to_dict())))
        return rows
    finally:
        conn.close()


def _queue_order_event_for_product(
    product_id: int,
    action: str,
    source_ref: str,
    db_path=None,
) -> None:
    actor = (current_user() or {}).get("user_id")
    snapshots = _current_rows_for_product(product_id, db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        for offer_id, payload in snapshots:
            _insert_delta_event(
                cur,
                event_key=f"ORDER|{source_ref}|{offer_id or 'NOOFFER'}",
                product_id=int(product_id),
                offer_id=offer_id,
                action=action,
                source_type="ORDER_MANAGEMENT",
                source_batch_id=None,
                payload=payload,
                created_by=actor,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_product_status(
    product_id: int,
    new_status: str,
    reason: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    init_db(db_path)
    result = _r11_set_product_status(product_id, new_status, reason, db_path)
    old_status = result["old_status"]
    target = result["new_status"]
    erp_action: str | None = None
    if target == STATUS_ARCHIVED:
        erp_action = "DISABLE"
    elif old_status == STATUS_ARCHIVED and target in {STATUS_VISIBLE, STATUS_HIDDEN}:
        erp_action = "REACTIVATE"

    if erp_action:
        _queue_order_event_for_product(
            int(product_id),
            erp_action,
            f"STATUS-{uuid.uuid4().hex}",
            db_path,
        )
    return result


def product_edit_snapshot(product_id: int, db_path=None) -> dict[str, Any]:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        placeholder = "%s" if use_postgres() else "?"
        product_df = pd.read_sql_query(
            f"""
            SELECT
                product_id AS "ID", aic AS "AIC",
                nome_commerciale AS "Nome Commerciale",
                principio_attivo AS "Principio Attivo",
                forma_farmaceutica AS "Forma Farmaceutica",
                materiale_pericoloso AS "Materiale Pericoloso",
                stupefacente AS "Stupefacente", atc7 AS "ATC7", atc9 AS "ATC9",
                fala_lasa AS "Fala / Lasa", gruppo_stivaggio AS "Gruppo di Stivaggio",
                temperatura_stivaggio AS "Temperatura di Stivaggio", upc AS "UPC",
                note AS "Note", x AS "X", y AS "Y", z AS "Z",
                data_validita_farmaco AS "Data Validità Farmaco"
            FROM products WHERE product_id = {placeholder}
            """,
            conn,
            params=(int(product_id),),
        )
        if product_df.empty:
            raise OrderManagementError("Prodotto non trovato.")
        offers_df = pd.read_sql_query(
            f"""
            SELECT offer_id AS "offer_id", fornitore AS "Fornitore",
                   codice_fornitore AS "Codice Fornitore",
                   prezzo_unitario AS "Prezzo Unitario",
                   prezzo_confezione AS "Prezzo Confezione",
                   minimo_movimentabile AS "Minimo Movimentabile",
                   iva AS "IVA"
            FROM offers
            WHERE product_id = {placeholder} AND active = 1
            ORDER BY fornitore, offer_id
            """,
            conn,
            params=(int(product_id),),
        )
        return {"product": product_df.iloc[0].to_dict(), "offers": offers_df}
    finally:
        conn.close()


def _clean_text(value: Any, required: bool = False) -> str | None:
    text = str(value or "").strip()
    if required and not text:
        raise OrderManagementError("Compila tutti i campi obbligatori dell'articolo.")
    return text or None


def _as_float(value: Any, label: str, minimum: float | None = None, exclusive: bool = False) -> float:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise OrderManagementError(f"{label}: valore numerico non valido.") from exc
    if minimum is not None:
        invalid = number <= minimum if exclusive else number < minimum
        if invalid:
            op = ">" if exclusive else ">="
            raise OrderManagementError(f"{label}: il valore deve essere {op} {minimum}.")
    return number


def _as_int(value: Any, label: str, minimum: int = 1) -> int:
    number = _as_float(value, label, float(minimum))
    integer = int(round(number))
    if abs(number - integer) > 1e-9 or integer < minimum:
        raise OrderManagementError(f"{label}: inserisci un intero >= {minimum}.")
    return integer


def update_product_and_offer(
    product_id: int,
    offer_id: int,
    values: dict[str, Any],
    reason: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    user = _require_permission(PERMISSION_EDIT_CATALOGUE)
    reason = str(reason or "").strip()
    if not reason:
        raise OrderManagementError("Inserisci una motivazione per la modifica.")
    if len(reason) > 1000:
        raise OrderManagementError("La motivazione non può superare 1000 caratteri.")

    product_values = {
        "nome_commerciale": _clean_text(values.get("Nome Commerciale"), True),
        "principio_attivo": _clean_text(values.get("Principio Attivo"), True),
        "forma_farmaceutica": _clean_text(values.get("Forma Farmaceutica"), True),
        "materiale_pericoloso": _clean_text(values.get("Materiale Pericoloso"), True),
        "stupefacente": _clean_text(values.get("Stupefacente"), True),
        "atc7": _clean_text(values.get("ATC7")),
        "atc9": _clean_text(values.get("ATC9")),
        "fala_lasa": _clean_text(values.get("Fala / Lasa")),
        "gruppo_stivaggio": _clean_text(values.get("Gruppo di Stivaggio"), True),
        "temperatura_stivaggio": _clean_text(values.get("Temperatura di Stivaggio"), True),
        "upc": _as_int(values.get("UPC"), "UPC"),
        "note": _clean_text(values.get("Note")),
        "x": _as_float(values.get("X"), "X", 0, True),
        "y": _as_float(values.get("Y"), "Y", 0, True),
        "z": _as_float(values.get("Z"), "Z", 0, True),
    }
    offer_values = {
        "prezzo_unitario": _as_float(values.get("Prezzo Unitario"), "Prezzo Unitario", 0),
        "prezzo_confezione": _as_float(values.get("Prezzo Confezione"), "Prezzo Confezione", 0),
        "minimo_movimentabile": _as_int(values.get("Minimo Movimentabile"), "Minimo Movimentabile"),
        "iva": _as_float(values.get("IVA"), "IVA", 0),
    }
    if round(offer_values["prezzo_unitario"] * product_values["upc"], 2) != round(offer_values["prezzo_confezione"], 2):
        raise OrderManagementError(
            "Prezzo Confezione non coerente: deve essere Prezzo Unitario × UPC, arrotondato a 2 decimali."
        )
    if round(offer_values["iva"], 6) not in {0.0, 0.04, 0.1, 0.22}:
        raise OrderManagementError("IVA non valida: usa 0%, 4%, 10% o 22%.")

    init_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"
        lock = " FOR UPDATE" if use_postgres() else ""
        cur.execute(f"SELECT * FROM products WHERE product_id = {ph}{lock}", (int(product_id),))
        old_product = cur.fetchone()
        cur.execute(
            f"SELECT * FROM offers WHERE offer_id = {ph} AND product_id = {ph}{lock}",
            (int(offer_id), int(product_id)),
        )
        old_offer = cur.fetchone()
        if not old_product or not old_offer:
            raise OrderManagementError("Prodotto o offerta non trovati.")

        product_columns = list(product_values)
        offer_columns = list(offer_values)
        product_set = ", ".join(f"{name} = {ph}" for name in product_columns)
        offer_set = ", ".join(f"{name} = {ph}" for name in offer_columns)
        cur.execute(
            f"UPDATE products SET {product_set}, updated_at = {ph} WHERE product_id = {ph}",
            tuple(product_values[name] for name in product_columns)
            + (datetime.now().isoformat(timespec="seconds"), int(product_id)),
        )
        cur.execute(
            f"UPDATE offers SET {offer_set}, last_published_at = {ph} WHERE offer_id = {ph}",
            tuple(offer_values[name] for name in offer_columns)
            + (datetime.now().isoformat(timespec="seconds"), int(offer_id)),
        )
        conn.commit()
    except OrderManagementError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    source_ref = f"EDIT-{uuid.uuid4().hex}"
    _queue_order_event_for_product(int(product_id), "UPDATE", source_ref, db_path)
    audit(
        "ORDER_CATALOGUE_EDIT",
        entity_type="product",
        entity_id=str(product_id),
        details={
            "offer_id": int(offer_id),
            "reason": reason,
            "source_ref": source_ref,
            "actor_role": user.get("role_id"),
        },
    )
    return {"product_id": int(product_id), "offer_id": int(offer_id), "source_ref": source_ref}


def delta_dataframe(db_path: str | Path | None = None) -> pd.DataFrame:
    init_db(db_path)
    _require_permission(PERMISSION_EXPORT_ERP)
    conn = _connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT event_id AS "ID", action AS "Azione", source_type AS "Origine",
                   source_batch_id AS "Batch", payload_json AS "Payload",
                   created_at AS "Creato il", export_status AS "Stato ERP",
                   export_package_id AS "Pacchetto"
            FROM erp_delta_events
            ORDER BY event_id DESC
            """,
            conn,
        )
    finally:
        conn.close()

    if df.empty:
        return df

    def extract(payload: Any, key: str) -> Any:
        try:
            return json.loads(payload or "{}").get(key)
        except Exception:
            return None

    df["AIC"] = df["Payload"].map(lambda value: extract(value, "AIC"))
    df["Nome Commerciale"] = df["Payload"].map(lambda value: extract(value, "Nome Commerciale"))
    df["Fornitore"] = df["Payload"].map(lambda value: extract(value, "Fornitore"))
    df["Data Validità Farmaco"] = df["Payload"].map(
        lambda value: extract(value, "Data Validità Farmaco")
    )
    return df.drop(columns=["Payload"])


def _load_export_config() -> dict[str, Any]:
    defaults = {
        "separator": ";",
        "encoding": "utf-8-sig",
        "decimal": ",",
        "include_metadata": True,
        "metadata_columns": ["Operazione", "Origine", "Batch sorgente"],
        "columns": MASTER_COLUMNS,
    }
    path = Path("config/erp_export.json")
    if not path.exists():
        return defaults
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return {**defaults, **loaded}
    except Exception:
        return defaults


def _events_by_ids(event_ids: list[int], db_path=None, *, package_id: str | None = None) -> list[dict[str, Any]]:
    if not event_ids and not package_id:
        return []
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"
        if package_id:
            cur.execute(
                f"SELECT event_id, action, source_type, source_batch_id, payload_json "
                f"FROM erp_delta_events WHERE export_package_id = {ph} ORDER BY event_id",
                (package_id,),
            )
        else:
            marks = ", ".join([ph] * len(event_ids))
            cur.execute(
                f"SELECT event_id, action, source_type, source_batch_id, payload_json "
                f"FROM erp_delta_events WHERE event_id IN ({marks}) ORDER BY event_id",
                tuple(int(value) for value in event_ids),
            )
        columns = ["event_id", "action", "source_type", "source_batch_id", "payload_json"]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _csv_from_events(events: list[dict[str, Any]]) -> bytes:
    cfg = _load_export_config()
    rows = []
    for event in events:
        payload = json.loads(event.get("payload_json") or "{}")
        row: dict[str, Any] = {}
        if cfg.get("include_metadata", True):
            row["Operazione"] = event.get("action")
            row["Origine"] = event.get("source_type")
            row["Batch sorgente"] = event.get("source_batch_id") or ""
        for column in cfg.get("columns") or MASTER_COLUMNS:
            row[column] = payload.get(column)
        rows.append(row)
    df = pd.DataFrame(rows)
    text = df.to_csv(
        index=False,
        sep=str(cfg.get("separator") or ";"),
        decimal=str(cfg.get("decimal") or ","),
        lineterminator="\n",
    )
    return text.encode(str(cfg.get("encoding") or "utf-8-sig"))


def prepare_export(event_ids: list[int], db_path=None) -> dict[str, Any]:
    user = _require_permission(PERMISSION_EXPORT_ERP)
    init_db(db_path)
    ids = sorted({int(value) for value in event_ids})
    if not ids:
        raise OrderManagementError("Seleziona almeno un delta da esportare.")

    conn = _connect(db_path)
    package_id = f"ERP-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"
        marks = ", ".join([ph] * len(ids))
        lock = " FOR UPDATE" if use_postgres() else ""
        cur.execute(
            f"SELECT event_id FROM erp_delta_events "
            f"WHERE event_id IN ({marks}) AND export_status = 'PENDING'{lock}",
            tuple(ids),
        )
        available = [int(row[0]) for row in cur.fetchall()]
        if len(available) != len(ids):
            raise OrderManagementError(
                "Uno o più delta non sono più PENDING. Aggiorna la pagina e riprova."
            )
        now = datetime.now().isoformat(timespec="seconds")
        if use_postgres():
            cur.execute(
                "INSERT INTO erp_export_packages (package_id, created_by, row_count, status) VALUES (%s, %s, %s, 'EXPORTED')",
                (package_id, user.get("user_id"), len(ids)),
            )
            cur.execute(
                f"UPDATE erp_delta_events SET export_status='EXPORTED', export_package_id=%s, exported_at=NOW() "
                f"WHERE event_id IN ({marks})",
                (package_id, *ids),
            )
        else:
            cur.execute(
                "INSERT INTO erp_export_packages (package_id, created_at, created_by, row_count, status) VALUES (?, ?, ?, ?, 'EXPORTED')",
                (package_id, now, user.get("user_id"), len(ids)),
            )
            cur.execute(
                f"UPDATE erp_delta_events SET export_status='EXPORTED', export_package_id=?, exported_at=? "
                f"WHERE event_id IN ({marks})",
                (package_id, now, *ids),
            )
        conn.commit()
    except OrderManagementError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    data = package_csv(package_id, db_path)
    audit(
        "ERP_EXPORT_PREPARED",
        entity_type="erp_export_package",
        entity_id=package_id,
        details={"row_count": len(ids)},
    )
    return {"package_id": package_id, "row_count": len(ids), "data": data}


def package_csv(package_id: str, db_path=None) -> bytes:
    _require_permission(PERMISSION_EXPORT_ERP)
    events = _events_by_ids([], db_path, package_id=str(package_id))
    if not events:
        raise OrderManagementError("Pacchetto ERP non trovato o vuoto.")
    return _csv_from_events(events)


def packages_dataframe(db_path=None) -> pd.DataFrame:
    init_db(db_path)
    _require_permission(PERMISSION_EXPORT_ERP)
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            """
            SELECT package_id AS "Pacchetto", created_at AS "Creato il",
                   row_count AS "Righe", status AS "Stato",
                   confirmed_at AS "Confermato il"
            FROM erp_export_packages
            ORDER BY created_at DESC
            """,
            conn,
        )
    finally:
        conn.close()


def confirm_import(package_id: str, db_path=None) -> None:
    user = _require_permission(PERMISSION_EXPORT_ERP)
    init_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = "%s" if use_postgres() else "?"
        now = datetime.now().isoformat(timespec="seconds")
        if use_postgres():
            cur.execute(
                "UPDATE erp_export_packages SET status='IMPORTED', confirmed_at=NOW(), confirmed_by=%s "
                "WHERE package_id=%s AND status='EXPORTED' RETURNING package_id",
                (user.get("user_id"), package_id),
            )
        else:
            cur.execute(
                "UPDATE erp_export_packages SET status='IMPORTED', confirmed_at=?, confirmed_by=? "
                "WHERE package_id=? AND status='EXPORTED'",
                (now, user.get("user_id"), package_id),
            )
        if cur.rowcount == 0:
            raise OrderManagementError("Pacchetto non trovato o già confermato.")
        if use_postgres():
            cur.execute(
                "UPDATE erp_delta_events SET export_status='IMPORTED', imported_at=NOW(), imported_by=%s "
                "WHERE export_package_id=%s",
                (user.get("user_id"), package_id),
            )
        else:
            cur.execute(
                "UPDATE erp_delta_events SET export_status='IMPORTED', imported_at=?, imported_by=? "
                "WHERE export_package_id=?",
                (now, user.get("user_id"), package_id),
            )
        conn.commit()
    except OrderManagementError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    audit(
        "ERP_IMPORT_CONFIRMED",
        entity_type="erp_export_package",
        entity_id=package_id,
    )


def catalogue_to_csv(dataframe: pd.DataFrame) -> bytes:
    _require_permission(PERMISSION_EXPORT_ERP)
    cfg = _load_export_config()
    columns = [column for column in (cfg.get("columns") or MASTER_COLUMNS) if column in dataframe.columns]
    df = dataframe[columns].copy()
    text = df.to_csv(
        index=False,
        sep=str(cfg.get("separator") or ";"),
        decimal=str(cfg.get("decimal") or ","),
        lineterminator="\n",
    )
    return text.encode(str(cfg.get("encoding") or "utf-8-sig"))

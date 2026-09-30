from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from modules.auth import audit, current_user
from modules.db_backend import get_postgres_url, use_postgres
from modules.master22_catalog import (
    catalogue_dataframe as _r10_catalogue_dataframe,
    export_catalogue_excel as _r10_export_catalogue_excel,
    init_db as _r10_init_db,
)


STATUS_VISIBLE = "VISIBLE"
STATUS_HIDDEN = "HIDDEN"
STATUS_ARCHIVED = "ARCHIVED"
VALID_STATUSES = {STATUS_VISIBLE, STATUS_HIDDEN, STATUS_ARCHIVED}

STATUS_LABELS = {
    STATUS_VISIBLE: "Visibile",
    STATUS_HIDDEN: "Nascosto",
    STATUS_ARCHIVED: "Archiviato",
}

STATUS_ICONS = {
    STATUS_VISIBLE: "🟢",
    STATUS_HIDDEN: "🟡",
    STATUS_ARCHIVED: "⚫",
}

STATUS_COLUMN = "Stato Catalogo"


class CatalogueVisibilityError(RuntimeError):
    pass


def _connect(db_path: str | Path | None = None):
    if use_postgres():
        import psycopg2

        url = get_postgres_url()
        if not url:
            raise CatalogueVisibilityError(
                "Connessione PostgreSQL non configurata."
            )
        return psycopg2.connect(
            url,
            connect_timeout=10,
            sslmode="require",
            application_name="listino-farmaci-catalogue-visibility",
        )

    path = Path(db_path or "data/listino.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_exists_sqlite(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _ensure_permission_postgres(cur) -> None:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'permissions'
        """
    )
    permission_columns = {row[0] for row in cur.fetchall()}

    if "permission_id" not in permission_columns:
        return

    columns = ["permission_id"]
    values: list[Any] = ["manage_catalogue"]

    if "display_name" in permission_columns:
        columns.append("display_name")
        values.append("Gestione catalogo")
    elif "name" in permission_columns:
        columns.append("name")
        values.append("Gestione catalogo")

    if "description" in permission_columns:
        columns.append("description")
        values.append(
            "Consente di nascondere, archiviare e ripristinare prodotti nel catalogo."
        )

    placeholders = ", ".join(["%s"] * len(columns))
    assignments = [
        f"{column} = EXCLUDED.{column}"
        for column in columns
        if column != "permission_id"
    ]
    conflict_sql = (
        "DO UPDATE SET " + ", ".join(assignments)
        if assignments
        else "DO NOTHING"
    )

    cur.execute(
        f"""
        INSERT INTO public.permissions ({', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT (permission_id) {conflict_sql}
        """,
        tuple(values),
    )

    cur.execute(
        """
        INSERT INTO public.role_permissions (role_id, permission_id)
        SELECT 'ADMIN', 'manage_catalogue'
        WHERE NOT EXISTS (
            SELECT 1
            FROM public.role_permissions
            WHERE role_id = 'ADMIN'
              AND permission_id = 'manage_catalogue'
        )
        """
    )
    cur.execute(
        """
        DELETE FROM public.role_permissions
        WHERE permission_id = 'manage_catalogue'
          AND role_id <> 'ADMIN'
        """
    )


def ensure_catalogue_visibility_schema(
    db_path: str | Path | None = None,
) -> None:
    """Migrazione R11 non distruttiva per stato catalogo e permesso Admin."""
    conn = _connect(db_path)
    try:
        cur = conn.cursor()

        if use_postgres():
            cur.execute(
                "ALTER TABLE public.products "
                "ADD COLUMN IF NOT EXISTS catalogue_status TEXT NOT NULL DEFAULT 'VISIBLE'"
            )
            cur.execute(
                "ALTER TABLE public.products "
                "ADD COLUMN IF NOT EXISTS status_reason TEXT"
            )
            cur.execute(
                "ALTER TABLE public.products "
                "ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ"
            )
            cur.execute(
                "ALTER TABLE public.products "
                "ADD COLUMN IF NOT EXISTS status_updated_by UUID"
            )
            cur.execute(
                """
                UPDATE public.products
                SET catalogue_status = 'VISIBLE'
                WHERE catalogue_status IS NULL
                   OR TRIM(catalogue_status) = ''
                """
            )
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE n.nspname = 'public'
                          AND t.relname = 'products'
                          AND c.conname = 'products_catalogue_status_check'
                    ) THEN
                        ALTER TABLE public.products
                        ADD CONSTRAINT products_catalogue_status_check
                        CHECK (catalogue_status IN ('VISIBLE', 'HIDDEN', 'ARCHIVED'));
                    END IF;
                END $$;
                """
            )
            _ensure_permission_postgres(cur)
        else:
            if not _table_exists_sqlite(conn, "products"):
                return

            columns = {
                row[1]
                for row in cur.execute("PRAGMA table_info(products)").fetchall()
            }
            migrations = {
                "catalogue_status": "TEXT NOT NULL DEFAULT 'VISIBLE'",
                "status_reason": "TEXT",
                "status_updated_at": "TEXT",
                "status_updated_by": "TEXT",
            }
            for name, sql_type in migrations.items():
                if name not in columns:
                    cur.execute(
                        f"ALTER TABLE products ADD COLUMN {name} {sql_type}"
                    )
            cur.execute(
                """
                UPDATE products
                SET catalogue_status = 'VISIBLE'
                WHERE catalogue_status IS NULL
                   OR TRIM(catalogue_status) = ''
                """
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    _r10_init_db(db_path)
    ensure_catalogue_visibility_schema(db_path)


def _status_dataframe(
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    conn = _connect(db_path)
    try:
        query = (
            'SELECT product_id, catalogue_status AS "Stato Catalogo" '
            'FROM products'
        )
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def filter_catalogue_for_role(
    dataframe: pd.DataFrame,
    role_id: str | None,
) -> pd.DataFrame:
    if dataframe.empty or STATUS_COLUMN not in dataframe.columns:
        return dataframe.copy()

    role = str(role_id or "").strip().upper()
    status = (
        dataframe[STATUS_COLUMN]
        .fillna(STATUS_VISIBLE)
        .astype(str)
        .str.strip()
        .str.upper()
    )

    if role in {"ADMIN", "BUYER", "CUSTOMER_CARE"}:
        mask = status.ne(STATUS_ARCHIVED)
    else:
        # Fail closed: CLIENTE e ruoli non riconosciuti vedono solo VISIBLE.
        mask = status.eq(STATUS_VISIBLE)

    return dataframe.loc[mask].copy()


def catalogue_dataframe(
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    init_db(db_path)
    df = _r10_catalogue_dataframe(db_path)

    if df.empty:
        if STATUS_COLUMN not in df.columns:
            df[STATUS_COLUMN] = pd.Series(dtype="object")
        return df

    status_df = _status_dataframe(db_path)
    df = df.merge(status_df, how="left", on="product_id")
    df[STATUS_COLUMN] = df[STATUS_COLUMN].fillna(STATUS_VISIBLE)

    user = current_user()
    role_id = user.get("role_id") if user else None
    return filter_catalogue_for_role(df, role_id)


def export_catalogue_excel(
    db_path: str | Path | None,
    dataframe: pd.DataFrame | None = None,
) -> bytes:
    df = (
        dataframe.copy()
        if dataframe is not None
        else catalogue_dataframe(db_path)
    )

    user = current_user()
    role_id = str((user or {}).get("role_id") or "").upper()
    if role_id == "CLIENTE":
        df = df.drop(columns=[STATUS_COLUMN], errors="ignore")

    return _r10_export_catalogue_excel(db_path, df)


def admin_catalogue_dataframe(
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        if use_postgres():
            query = """
                SELECT
                    p.product_id AS "ID",
                    p.aic AS "AIC",
                    p.nome_commerciale AS "Nome Commerciale",
                    p.principio_attivo AS "Principio Attivo",
                    p.forma_farmaceutica AS "Forma Farmaceutica",
                    COALESCE(p.catalogue_status, 'VISIBLE') AS "Stato",
                    p.status_reason AS "Motivazione",
                    p.status_updated_at AS "Aggiornato il",
                    COALESCE(
                        up.display_name,
                        up.email,
                        p.status_updated_by::text
                    ) AS "Aggiornato da",
                    COUNT(o.offer_id) FILTER (WHERE o.active = 1) AS "Offerte attive",
                    COALESCE(
                        STRING_AGG(
                            DISTINCT o.fornitore,
                            ' | ' ORDER BY o.fornitore
                        ) FILTER (WHERE o.active = 1),
                        ''
                    ) AS "Fornitori"
                FROM public.products p
                LEFT JOIN public.offers o
                  ON o.product_id = p.product_id
                LEFT JOIN public.user_profiles up
                  ON up.user_id = p.status_updated_by
                GROUP BY
                    p.product_id,
                    p.aic,
                    p.nome_commerciale,
                    p.principio_attivo,
                    p.forma_farmaceutica,
                    p.catalogue_status,
                    p.status_reason,
                    p.status_updated_at,
                    p.status_updated_by,
                    up.display_name,
                    up.email
                ORDER BY LOWER(COALESCE(p.nome_commerciale, '')), p.product_id
            """
        else:
            query = """
                SELECT
                    p.product_id AS "ID",
                    p.aic AS "AIC",
                    p.nome_commerciale AS "Nome Commerciale",
                    p.principio_attivo AS "Principio Attivo",
                    p.forma_farmaceutica AS "Forma Farmaceutica",
                    COALESCE(p.catalogue_status, 'VISIBLE') AS "Stato",
                    p.status_reason AS "Motivazione",
                    p.status_updated_at AS "Aggiornato il",
                    p.status_updated_by AS "Aggiornato da",
                    SUM(CASE WHEN o.active = 1 THEN 1 ELSE 0 END) AS "Offerte attive",
                    GROUP_CONCAT(
                        DISTINCT CASE WHEN o.active = 1 THEN o.fornitore END
                    ) AS "Fornitori"
                FROM products p
                LEFT JOIN offers o
                  ON o.product_id = p.product_id
                GROUP BY
                    p.product_id,
                    p.aic,
                    p.nome_commerciale,
                    p.principio_attivo,
                    p.forma_farmaceutica,
                    p.catalogue_status,
                    p.status_reason,
                    p.status_updated_at,
                    p.status_updated_by
                ORDER BY LOWER(COALESCE(p.nome_commerciale, '')), p.product_id
            """

        df = pd.read_sql_query(query, conn)
        if not use_postgres() and "Fornitori" in df.columns:
            df["Fornitori"] = (
                df["Fornitori"]
                .fillna("")
                .astype(str)
                .str.replace(",", " | ", regex=False)
            )
        return df
    finally:
        conn.close()


def _require_manage_catalogue() -> dict[str, Any]:
    user = current_user()
    permissions = set((user or {}).get("permissions") or [])
    if not user or "manage_catalogue" not in permissions:
        raise CatalogueVisibilityError(
            "Non hai i privilegi necessari per gestire il catalogo."
        )
    return user


def set_product_status(
    product_id: int,
    new_status: str,
    reason: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    user = _require_manage_catalogue()
    status = str(new_status or "").strip().upper()
    reason = str(reason or "").strip()

    if status not in VALID_STATUSES:
        raise CatalogueVisibilityError("Stato catalogo non valido.")
    if not reason:
        raise CatalogueVisibilityError(
            "Inserisci una motivazione prima di modificare lo stato."
        )
    if len(reason) > 1000:
        raise CatalogueVisibilityError(
            "La motivazione non può superare 1000 caratteri."
        )

    try:
        product_id = int(product_id)
    except (TypeError, ValueError) as exc:
        raise CatalogueVisibilityError("Prodotto non valido.") from exc

    init_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()

        if use_postgres():
            cur.execute(
                """
                SELECT product_id, aic, nome_commerciale,
                       COALESCE(catalogue_status, 'VISIBLE')
                FROM public.products
                WHERE product_id = %s
                FOR UPDATE
                """,
                (product_id,),
            )
        else:
            cur.execute(
                """
                SELECT product_id, aic, nome_commerciale,
                       COALESCE(catalogue_status, 'VISIBLE')
                FROM products
                WHERE product_id = ?
                """,
                (product_id,),
            )

        row = cur.fetchone()
        if not row:
            raise CatalogueVisibilityError("Prodotto non trovato.")

        old_status = str(row[3] or STATUS_VISIBLE).upper()
        aic = row[1]
        name = row[2]

        if old_status == status:
            raise CatalogueVisibilityError(
                f"Il prodotto è già nello stato {STATUS_LABELS[status]}."
            )

        if use_postgres():
            cur.execute(
                """
                UPDATE public.products
                SET
                    catalogue_status = %s,
                    status_reason = %s,
                    status_updated_at = NOW(),
                    status_updated_by = %s
                WHERE product_id = %s
                """,
                (status, reason, user["user_id"], product_id),
            )
        else:
            from datetime import datetime

            cur.execute(
                """
                UPDATE products
                SET
                    catalogue_status = ?,
                    status_reason = ?,
                    status_updated_at = ?,
                    status_updated_by = ?
                WHERE product_id = ?
                """,
                (
                    status,
                    reason,
                    datetime.now().isoformat(timespec="seconds"),
                    user["user_id"],
                    product_id,
                ),
            )

        conn.commit()
    except CatalogueVisibilityError:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    audit(
        "CATALOGUE_STATUS_CHANGE",
        entity_type="product",
        entity_id=str(product_id),
        details={
            "aic": aic,
            "name": name,
            "old_status": old_status,
            "new_status": status,
            "reason": reason,
        },
    )

    return {
        "product_id": product_id,
        "aic": aic,
        "name": name,
        "old_status": old_status,
        "new_status": status,
        "reason": reason,
    }

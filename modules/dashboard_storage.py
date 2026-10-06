from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from modules.catalog_db import init_db
from modules.db_backend import use_postgres
import modules.order_management as order_management


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path: str | Path | None = None):
    return order_management._connect(db_path)


def _ph() -> str:
    return "%s" if use_postgres() else "?"


def _row_dict(row, cur=None) -> dict[str, Any] | None:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    if cur is None or not cur.description:
        return {}
    return {
        description[0]: value
        for description, value in zip(cur.description, row)
    }


def init_dashboard_tables(db_path: str | Path | None = None) -> None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        if use_postgres():
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.custom_dashboards (
                    dashboard_id BIGSERIAL PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS public.dashboard_widgets (
                    widget_id BIGSERIAL PRIMARY KEY,
                    dashboard_id BIGINT NOT NULL,
                    title TEXT NOT NULL,
                    widget_type TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    width TEXT NOT NULL DEFAULT 'half',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CONSTRAINT fk_widgets_dashboard
                        FOREIGN KEY (dashboard_id)
                        REFERENCES public.custom_dashboards(dashboard_id)
                        ON DELETE CASCADE
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard
                ON public.dashboard_widgets(dashboard_id, position, widget_id)
                """
            )
        else:
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS custom_dashboards (
                    dashboard_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dashboard_widgets (
                    widget_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dashboard_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    widget_type TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    width TEXT NOT NULL DEFAULT 'half',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(dashboard_id)
                        REFERENCES custom_dashboards(dashboard_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard
                    ON dashboard_widgets(dashboard_id, position, widget_id);
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_dashboards(db_path: str | Path | None = None) -> pd.DataFrame:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        return pd.read_sql_query(
            """
            SELECT d.dashboard_id,
                   d.name AS "Nome",
                   d.description AS "Descrizione",
                   COUNT(w.widget_id) AS "Widget",
                   d.updated_at AS "Aggiornata"
            FROM custom_dashboards d
            LEFT JOIN dashboard_widgets w
              ON w.dashboard_id = d.dashboard_id
            GROUP BY d.dashboard_id, d.name, d.description, d.updated_at
            ORDER BY d.updated_at DESC, LOWER(d.name)
            """,
            conn,
        )
    finally:
        conn.close()


def create_dashboard(
    db_path: str | Path | None,
    name: str,
    description: str = "",
) -> int:
    init_dashboard_tables(db_path)
    name = (name or "").strip()
    if not name:
        raise ValueError("Il nome della dashboard è obbligatorio.")

    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        now = _now()
        if use_postgres():
            cur.execute(
                """
                INSERT INTO custom_dashboards(
                    name, description, created_at, updated_at
                ) VALUES (%s, %s, %s, %s)
                RETURNING dashboard_id
                """,
                (name, (description or "").strip(), now, now),
            )
            dashboard_id = int(cur.fetchone()[0])
        else:
            cur.execute(
                """
                INSERT INTO custom_dashboards(
                    name, description, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (name, (description or "").strip(), now, now),
            )
            dashboard_id = int(cur.lastrowid)
        conn.commit()
        return dashboard_id
    except Exception as exc:
        conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ValueError(
                f"Esiste già una dashboard chiamata '{name}'."
            ) from exc
        raise
    finally:
        conn.close()


def update_dashboard(
    db_path: str | Path | None,
    dashboard_id: int,
    name: str,
    description: str = "",
) -> None:
    init_dashboard_tables(db_path)
    name = (name or "").strip()
    if not name:
        raise ValueError("Il nome della dashboard è obbligatorio.")

    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = _ph()
        cur.execute(
            f"""
            UPDATE custom_dashboards
            SET name={ph}, description={ph}, updated_at={ph}
            WHERE dashboard_id={ph}
            """,
            (
                name,
                (description or "").strip(),
                _now(),
                int(dashboard_id),
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise ValueError(
                f"Esiste già una dashboard chiamata '{name}'."
            ) from exc
        raise
    finally:
        conn.close()


def delete_dashboard(
    db_path: str | Path | None,
    dashboard_id: int,
) -> None:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"DELETE FROM custom_dashboards WHERE dashboard_id={_ph()}",
            (int(dashboard_id),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_dashboard(
    db_path: str | Path | None,
    dashboard_id: int,
) -> dict[str, Any] | None:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT dashboard_id, name, description, created_at, updated_at
            FROM custom_dashboards
            WHERE dashboard_id={_ph()}
            """,
            (int(dashboard_id),),
        )
        return _row_dict(cur.fetchone(), cur)
    finally:
        conn.close()


def list_widgets(
    db_path: str | Path | None,
    dashboard_id: int,
) -> list[dict[str, Any]]:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT widget_id, dashboard_id, title, widget_type, config_json,
                   position, width, created_at, updated_at
            FROM dashboard_widgets
            WHERE dashboard_id={_ph()}
            ORDER BY position, widget_id
            """,
            (int(dashboard_id),),
        )
        widgets = []
        for row in cur.fetchall():
            item = _row_dict(row, cur) or {}
            try:
                item["config"] = json.loads(item.pop("config_json"))
            except Exception:
                item.pop("config_json", None)
                item["config"] = {}
            widgets.append(item)
        return widgets
    finally:
        conn.close()


def add_widget(
    db_path: str | Path | None,
    dashboard_id: int,
    title: str,
    widget_type: str,
    config: dict,
    width: str = "half",
) -> int:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = _ph()
        cur.execute(
            f"""
            SELECT COALESCE(MAX(position), -1)
            FROM dashboard_widgets
            WHERE dashboard_id={ph}
            """,
            (int(dashboard_id),),
        )
        max_pos = int(cur.fetchone()[0])
        params = (
            int(dashboard_id),
            (title or widget_type).strip(),
            widget_type,
            json.dumps(config, ensure_ascii=False),
            max_pos + 1,
            width if width in {"half", "full"} else "half",
            _now(),
            _now(),
        )
        if use_postgres():
            cur.execute(
                """
                INSERT INTO dashboard_widgets(
                    dashboard_id, title, widget_type, config_json,
                    position, width, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING widget_id
                """,
                params,
            )
            widget_id = int(cur.fetchone()[0])
        else:
            cur.execute(
                """
                INSERT INTO dashboard_widgets(
                    dashboard_id, title, widget_type, config_json,
                    position, width, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            widget_id = int(cur.lastrowid)

        cur.execute(
            f"""
            UPDATE custom_dashboards
            SET updated_at={ph}
            WHERE dashboard_id={ph}
            """,
            (_now(), int(dashboard_id)),
        )
        conn.commit()
        return widget_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_widget(
    db_path: str | Path | None,
    widget_id: int,
    title: str,
    widget_type: str,
    config: dict,
    width: str,
) -> None:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = _ph()
        cur.execute(
            f"""
            SELECT dashboard_id
            FROM dashboard_widgets
            WHERE widget_id={ph}
            """,
            (int(widget_id),),
        )
        row = cur.fetchone()
        if not row:
            return
        dashboard_id = int(row[0])
        now = _now()
        cur.execute(
            f"""
            UPDATE dashboard_widgets
            SET title={ph}, widget_type={ph}, config_json={ph},
                width={ph}, updated_at={ph}
            WHERE widget_id={ph}
            """,
            (
                (title or widget_type).strip(),
                widget_type,
                json.dumps(config, ensure_ascii=False),
                width if width in {"half", "full"} else "half",
                now,
                int(widget_id),
            ),
        )
        cur.execute(
            f"""
            UPDATE custom_dashboards
            SET updated_at={ph}
            WHERE dashboard_id={ph}
            """,
            (now, dashboard_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_widget(
    db_path: str | Path | None,
    widget_id: int,
) -> None:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = _ph()
        cur.execute(
            f"""
            SELECT dashboard_id
            FROM dashboard_widgets
            WHERE widget_id={ph}
            """,
            (int(widget_id),),
        )
        row = cur.fetchone()
        if not row:
            return
        dashboard_id = int(row[0])
        cur.execute(
            f"DELETE FROM dashboard_widgets WHERE widget_id={ph}",
            (int(widget_id),),
        )
        cur.execute(
            f"""
            SELECT widget_id
            FROM dashboard_widgets
            WHERE dashboard_id={ph}
            ORDER BY position, widget_id
            """,
            (dashboard_id,),
        )
        for position, item in enumerate(cur.fetchall()):
            cur.execute(
                f"""
                UPDATE dashboard_widgets
                SET position={ph}
                WHERE widget_id={ph}
                """,
                (position, int(item[0])),
            )
        cur.execute(
            f"""
            UPDATE custom_dashboards
            SET updated_at={ph}
            WHERE dashboard_id={ph}
            """,
            (_now(), dashboard_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def move_widget(
    db_path: str | Path | None,
    widget_id: int,
    direction: int,
) -> None:
    init_dashboard_tables(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        ph = _ph()
        cur.execute(
            f"""
            SELECT widget_id, dashboard_id, position
            FROM dashboard_widgets
            WHERE widget_id={ph}
            """,
            (int(widget_id),),
        )
        row = cur.fetchone()
        if not row:
            return
        dashboard_id = int(row[1])
        position = int(row[2])
        target_pos = position + (-1 if direction < 0 else 1)
        cur.execute(
            f"""
            SELECT widget_id, position
            FROM dashboard_widgets
            WHERE dashboard_id={ph} AND position={ph}
            """,
            (dashboard_id, target_pos),
        )
        target = cur.fetchone()
        if not target:
            return

        cur.execute(
            f"UPDATE dashboard_widgets SET position={ph} WHERE widget_id={ph}",
            (-999, int(row[0])),
        )
        cur.execute(
            f"UPDATE dashboard_widgets SET position={ph} WHERE widget_id={ph}",
            (position, int(target[0])),
        )
        cur.execute(
            f"UPDATE dashboard_widgets SET position={ph} WHERE widget_id={ph}",
            (target_pos, int(row[0])),
        )
        cur.execute(
            f"""
            UPDATE custom_dashboards
            SET updated_at={ph}
            WHERE dashboard_id={ph}
            """,
            (_now(), dashboard_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

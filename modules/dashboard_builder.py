from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from modules.catalog_db import catalogue_dataframe, init_db


CATEGORICAL_FIELDS = [
    "Fornitore",
    "AIC",
    "Codice Fornitore",
    "Nome Commerciale",
    "Principio Attivo",
    "Materiale Pericoloso",
    "Stupefacente",
    "ATC7",
    "ATC9",
    "Fala / Lasa",
    "Gruppo di Stivaggio",
    "Temperatura di Stivaggio",
    "IVA",
]

NUMERIC_FIELDS = [
    "Prezzo Unitario",
    "Prezzo Confezione",
    "UPC",
    "Minimo Movimentabile",
]

TABLE_FIELDS = [
    "Fornitore",
    "AIC",
    "Codice Fornitore",
    "Nome Commerciale",
    "Principio Attivo",
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
    "Ultimo aggiornamento",
    "Batch corrente",
]

GLOBAL_FILTER_FIELDS = [
    "Fornitore",
    "Principio Attivo",
    "ATC7",
    "Gruppo di Stivaggio",
    "Stupefacente",
    "Materiale Pericoloso",
    "Fala / Lasa",
    "IVA",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_dashboard_tables(db_path: str | Path) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.executescript(
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
                FOREIGN KEY(dashboard_id) REFERENCES custom_dashboards(dashboard_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_dashboard
                ON dashboard_widgets(dashboard_id, position, widget_id);
            """
        )


def list_dashboards(db_path: str | Path) -> pd.DataFrame:
    init_dashboard_tables(db_path)
    with _connect(db_path) as conn:
        return pd.read_sql_query(
            """
            SELECT d.dashboard_id, d.name AS Nome, d.description AS Descrizione,
                   COUNT(w.widget_id) AS Widget, d.updated_at AS Aggiornata
            FROM custom_dashboards d
            LEFT JOIN dashboard_widgets w ON w.dashboard_id = d.dashboard_id
            GROUP BY d.dashboard_id, d.name, d.description, d.updated_at
            ORDER BY d.updated_at DESC, d.name COLLATE NOCASE
            """,
            conn,
        )


def create_dashboard(db_path: str | Path, name: str, description: str = "") -> int:
    init_dashboard_tables(db_path)
    name = (name or "").strip()
    if not name:
        raise ValueError("Il nome della dashboard è obbligatorio.")
    now = _now()
    with _connect(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO custom_dashboards(name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, (description or "").strip(), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Esiste già una dashboard chiamata '{name}'.") from exc
        return int(cur.lastrowid)


def update_dashboard(db_path: str | Path, dashboard_id: int, name: str, description: str = "") -> None:
    init_dashboard_tables(db_path)
    name = (name or "").strip()
    if not name:
        raise ValueError("Il nome della dashboard è obbligatorio.")
    with _connect(db_path) as conn:
        try:
            conn.execute(
                "UPDATE custom_dashboards SET name=?, description=?, updated_at=? WHERE dashboard_id=?",
                (name, (description or "").strip(), _now(), int(dashboard_id)),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Esiste già una dashboard chiamata '{name}'.") from exc


def delete_dashboard(db_path: str | Path, dashboard_id: int) -> None:
    init_dashboard_tables(db_path)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM custom_dashboards WHERE dashboard_id=?", (int(dashboard_id),))


def get_dashboard(db_path: str | Path, dashboard_id: int) -> dict | None:
    init_dashboard_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT dashboard_id, name, description, created_at, updated_at FROM custom_dashboards WHERE dashboard_id=?",
            (int(dashboard_id),),
        ).fetchone()
    return dict(row) if row else None


def list_widgets(db_path: str | Path, dashboard_id: int) -> list[dict]:
    init_dashboard_tables(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT widget_id, dashboard_id, title, widget_type, config_json, position, width, created_at, updated_at
            FROM dashboard_widgets
            WHERE dashboard_id=?
            ORDER BY position, widget_id
            """,
            (int(dashboard_id),),
        ).fetchall()
    widgets = []
    for row in rows:
        item = dict(row)
        try:
            item["config"] = json.loads(item.pop("config_json"))
        except Exception:
            item["config"] = {}
        widgets.append(item)
    return widgets


def add_widget(
    db_path: str | Path,
    dashboard_id: int,
    title: str,
    widget_type: str,
    config: dict,
    width: str = "half",
) -> int:
    init_dashboard_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        max_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS max_pos FROM dashboard_widgets WHERE dashboard_id=?",
            (int(dashboard_id),),
        ).fetchone()["max_pos"]
        cur = conn.execute(
            """
            INSERT INTO dashboard_widgets(
                dashboard_id, title, widget_type, config_json, position, width, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(dashboard_id),
                (title or widget_type).strip(),
                widget_type,
                json.dumps(config, ensure_ascii=False),
                int(max_pos) + 1,
                width if width in {"half", "full"} else "half",
                now,
                now,
            ),
        )
        conn.execute("UPDATE custom_dashboards SET updated_at=? WHERE dashboard_id=?", (now, int(dashboard_id)))
        return int(cur.lastrowid)


def update_widget(
    db_path: str | Path,
    widget_id: int,
    title: str,
    widget_type: str,
    config: dict,
    width: str,
) -> None:
    init_dashboard_tables(db_path)
    now = _now()
    with _connect(db_path) as conn:
        row = conn.execute("SELECT dashboard_id FROM dashboard_widgets WHERE widget_id=?", (int(widget_id),)).fetchone()
        if not row:
            return
        conn.execute(
            """
            UPDATE dashboard_widgets
            SET title=?, widget_type=?, config_json=?, width=?, updated_at=?
            WHERE widget_id=?
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
        conn.execute("UPDATE custom_dashboards SET updated_at=? WHERE dashboard_id=?", (now, int(row["dashboard_id"])))


def delete_widget(db_path: str | Path, widget_id: int) -> None:
    init_dashboard_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT dashboard_id FROM dashboard_widgets WHERE widget_id=?", (int(widget_id),)).fetchone()
        if not row:
            return
        dashboard_id = int(row["dashboard_id"])
        conn.execute("DELETE FROM dashboard_widgets WHERE widget_id=?", (int(widget_id),))
        rows = conn.execute(
            "SELECT widget_id FROM dashboard_widgets WHERE dashboard_id=? ORDER BY position, widget_id",
            (dashboard_id,),
        ).fetchall()
        for position, item in enumerate(rows):
            conn.execute("UPDATE dashboard_widgets SET position=? WHERE widget_id=?", (position, int(item["widget_id"])))
        conn.execute("UPDATE custom_dashboards SET updated_at=? WHERE dashboard_id=?", (_now(), dashboard_id))


def move_widget(db_path: str | Path, widget_id: int, direction: int) -> None:
    init_dashboard_tables(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT widget_id, dashboard_id, position FROM dashboard_widgets WHERE widget_id=?",
            (int(widget_id),),
        ).fetchone()
        if not row:
            return
        dashboard_id = int(row["dashboard_id"])
        position = int(row["position"])
        target_pos = position + (-1 if direction < 0 else 1)
        target = conn.execute(
            "SELECT widget_id, position FROM dashboard_widgets WHERE dashboard_id=? AND position=?",
            (dashboard_id, target_pos),
        ).fetchone()
        if not target:
            return
        conn.execute("UPDATE dashboard_widgets SET position=-999 WHERE widget_id=?", (int(row["widget_id"]),))
        conn.execute("UPDATE dashboard_widgets SET position=? WHERE widget_id=?", (position, int(target["widget_id"])))
        conn.execute("UPDATE dashboard_widgets SET position=? WHERE widget_id=?", (target_pos, int(row["widget_id"])))
        conn.execute("UPDATE custom_dashboards SET updated_at=? WHERE dashboard_id=?", (_now(), dashboard_id))


def catalogue_for_dashboards(db_path: str | Path) -> pd.DataFrame:
    df = catalogue_dataframe(db_path)
    if df.empty:
        return df
    for col in NUMERIC_FIELDS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "IVA" in df.columns:
        df["IVA"] = pd.to_numeric(df["IVA"], errors="coerce")
    return df


def history_dataframe(db_path: str | Path) -> pd.DataFrame:
    init_dashboard_tables(db_path)
    query = """
        SELECT
            h.history_id,
            h.offer_id,
            h.published_at AS Data,
            h.prezzo_unitario AS "Prezzo Unitario",
            h.prezzo_confezione AS "Prezzo Confezione",
            h.minimo_movimentabile AS "Minimo Movimentabile",
            h.iva AS IVA,
            h.batch_id AS Batch,
            o.fornitore AS Fornitore,
            p.aic AS AIC,
            p.nome_commerciale AS "Nome Commerciale",
            p.principio_attivo AS "Principio Attivo",
            p.atc7 AS ATC7,
            p.gruppo_stivaggio AS "Gruppo di Stivaggio",
            p.stupefacente AS Stupefacente
        FROM price_history h
        JOIN offers o ON o.offer_id = h.offer_id
        JOIN products p ON p.product_id = o.product_id
        ORDER BY h.published_at
    """
    with _connect(db_path) as conn:
        df = pd.read_sql_query(query, conn)
    if not df.empty:
        df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
    return df


def apply_global_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    out = df.copy()
    for field, value in (filters or {}).items():
        if value in (None, "", "Tutti") or field not in out.columns:
            continue
        if field == "IVA":
            try:
                num = float(value)
                out = out[pd.to_numeric(out[field], errors="coerce").sub(num).abs() < 1e-9]
            except Exception:
                pass
        else:
            out = out[out[field].fillna("").astype(str) == str(value)]
    return out


def apply_price_range(df: pd.DataFrame, field: str, min_value: float | None, max_value: float | None) -> pd.DataFrame:
    if field not in df.columns:
        return df
    out = df.copy()
    values = pd.to_numeric(out[field], errors="coerce")
    if min_value is not None:
        out = out[values >= float(min_value)]
        values = pd.to_numeric(out[field], errors="coerce")
    if max_value is not None:
        out = out[values <= float(max_value)]
    return out


def apply_widget_filter(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    field = (config or {}).get("filter_field")
    value = (config or {}).get("filter_value")
    if not field or value in (None, "", "Tutti") or field not in df.columns:
        return df
    if field == "IVA":
        try:
            num = float(value)
            return df[pd.to_numeric(df[field], errors="coerce").sub(num).abs() < 1e-9]
        except Exception:
            return df
    return df[df[field].fillna("").astype(str) == str(value)]


def metric_value(df: pd.DataFrame, config: dict) -> tuple[Any, str]:
    metric = config.get("metric", "Righe listino")
    aggregation = config.get("aggregation", "count")

    if metric == "Righe listino":
        return int(len(df)), "integer"
    if metric == "Prodotti distinti":
        if "product_id" in df.columns:
            return int(df["product_id"].nunique()), "integer"
        return int(df["AIC"].fillna("").nunique()), "integer"
    if metric == "Fornitori distinti":
        return int(df["Fornitore"].dropna().nunique()), "integer"

    if metric not in df.columns:
        return None, "text"
    series = pd.to_numeric(df[metric], errors="coerce").dropna()
    if series.empty:
        return None, "number"

    if aggregation == "sum":
        value = series.sum()
    elif aggregation == "min":
        value = series.min()
    elif aggregation == "max":
        value = series.max()
    elif aggregation == "median":
        value = series.median()
    else:
        value = series.mean()
    return float(value), "money" if metric.startswith("Prezzo") else "number"


def bar_dataframe(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    dimension = config.get("dimension", "Fornitore")
    measure = config.get("measure", "Conteggio righe")
    aggregation = config.get("aggregation", "count")
    top_n = max(1, int(config.get("top_n", 10) or 10))
    if dimension not in df.columns:
        return pd.DataFrame(columns=[dimension, "Valore"])

    work = df.copy()
    work[dimension] = work[dimension].fillna("(vuoto)").astype(str)

    if measure == "Conteggio righe":
        result = work.groupby(dimension, dropna=False).size().reset_index(name="Valore")
    elif measure in work.columns:
        work[measure] = pd.to_numeric(work[measure], errors="coerce")
        group = work.groupby(dimension, dropna=False)[measure]
        if aggregation == "sum":
            values = group.sum(min_count=1)
        elif aggregation == "min":
            values = group.min()
        elif aggregation == "max":
            values = group.max()
        elif aggregation == "median":
            values = group.median()
        else:
            values = group.mean()
        result = values.reset_index(name="Valore").dropna(subset=["Valore"])
    else:
        return pd.DataFrame(columns=[dimension, "Valore"])

    return result.sort_values("Valore", ascending=False).head(top_n)


def top_n_dataframe(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    label = config.get("label", "Nome Commerciale")
    metric = config.get("metric", "Prezzo Confezione")
    direction = config.get("direction", "desc")
    top_n = max(1, int(config.get("top_n", 10) or 10))
    if label not in df.columns or metric not in df.columns:
        return pd.DataFrame(columns=[label, metric])
    work = df[[label, metric, "Fornitore", "AIC"]].copy()
    work[metric] = pd.to_numeric(work[metric], errors="coerce")
    work = work.dropna(subset=[metric])
    return work.sort_values(metric, ascending=direction == "asc").head(top_n)


def table_dataframe(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    columns = [c for c in config.get("columns", TABLE_FIELDS[:8]) if c in df.columns]
    limit = max(1, min(int(config.get("limit", 50) or 50), 500))
    if not columns:
        columns = [c for c in TABLE_FIELDS[:8] if c in df.columns]
    return df[columns].head(limit).copy()


def trend_dataframe(db_path: str | Path, current_filtered: pd.DataFrame, config: dict) -> pd.DataFrame:
    history = history_dataframe(db_path)
    if history.empty:
        return pd.DataFrame(columns=["Data", "Valore", "Serie"])

    if "offer_id" in current_filtered.columns:
        eligible = set(pd.to_numeric(current_filtered["offer_id"], errors="coerce").dropna().astype(int).tolist())
        if eligible:
            history = history[history["offer_id"].isin(eligible)]
        else:
            return pd.DataFrame(columns=["Data", "Valore", "Serie"])

    metric = config.get("metric", "Prezzo Confezione")
    aggregation = config.get("aggregation", "mean")
    group_by = config.get("group_by", "Nessuno")
    if metric not in history.columns:
        return pd.DataFrame(columns=["Data", "Valore", "Serie"])

    history[metric] = pd.to_numeric(history[metric], errors="coerce")
    history = history.dropna(subset=["Data", metric]).copy()
    if history.empty:
        return pd.DataFrame(columns=["Data", "Valore", "Serie"])

    history["Giorno"] = history["Data"].dt.date.astype(str)
    keys = ["Giorno"]
    if group_by != "Nessuno" and group_by in history.columns:
        keys.append(group_by)

    group = history.groupby(keys, dropna=False)[metric]
    if aggregation == "min":
        series = group.min()
    elif aggregation == "max":
        series = group.max()
    elif aggregation == "median":
        series = group.median()
    else:
        series = group.mean()

    result = series.reset_index(name="Valore")
    result["Data"] = pd.to_datetime(result["Giorno"], errors="coerce")
    if len(keys) == 2:
        result["Serie"] = result[group_by].fillna("(vuoto)").astype(str)
    else:
        result["Serie"] = metric
    return result[["Data", "Valore", "Serie"]].sort_values("Data")

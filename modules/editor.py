from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from modules.excel_reader import WorkbookData
from modules.validator import parse_percentage


META_ROW = "Riga Excel"
META_STATUS = "Stato controlli"
META_DELETE = "Elimina riga"


def _is_na(value: Any) -> bool:
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except Exception:
        return False


def _clean_scalar(value: Any) -> Any:
    if _is_na(value):
        return None
    return value


def _display(value: Any) -> str:
    value = _clean_scalar(value)
    if value is None:
        return ""
    return str(value)


def _percentage_label(value: Any, allowed: list[Any]) -> Any:
    value = _clean_scalar(value)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None

    parsed = parse_percentage(value)
    if parsed is None:
        return str(value)

    # Consente anche la rappresentazione umana 10 per 10%.
    allowed_decimals = [Decimal(str(v)) for v in allowed]
    if parsed not in allowed_decimals and Decimal("1") < parsed <= Decimal("100"):
        scaled = parsed / Decimal("100")
        if scaled in allowed_decimals:
            parsed = scaled

    if parsed in allowed_decimals:
        return f"{float(parsed) * 100:g}%"
    return str(value)


def _editor_value(field: str, value: Any, schema: dict) -> Any:
    value = _clean_scalar(value)
    if value is None:
        return None

    cfg = next((c for c in schema["columns"] if c["name"] == field), {})
    kind = cfg.get("type", "string")

    if kind == "percentage_list":
        return _percentage_label(value, schema.get("lists", {}).get(cfg.get("list_name"), []))
    if kind == "identifier":
        return str(value)
    return value


def issue_rows(issues: list[dict], levels: set[str] | None = None) -> set[int]:
    rows: set[int] = set()
    for issue in issues:
        if levels and issue.get("Livello") not in levels:
            continue
        row = issue.get("Riga Excel")
        try:
            rows.add(int(row))
        except (TypeError, ValueError):
            continue
    return rows


def _row_status_map(issues: list[dict]) -> dict[int, str]:
    counts: dict[int, dict[str, int]] = {}
    for issue in issues:
        row = issue.get("Riga Excel")
        try:
            row = int(row)
        except (TypeError, ValueError):
            continue
        level = issue.get("Livello", "")
        counts.setdefault(row, {"BLOCCANTE": 0, "WARNING": 0, "INFO": 0})
        if level in counts[row]:
            counts[row][level] += 1

    result = {}
    for row, item in counts.items():
        parts = []
        if item["BLOCCANTE"]:
            parts.append(f"🔴 {item['BLOCCANTE']} errore/i")
        if item["WARNING"]:
            parts.append(f"🟡 {item['WARNING']} warning")
        if item["INFO"]:
            parts.append(f"🔵 {item['INFO']} info")
        result[row] = " · ".join(parts) if parts else "✅ OK"
    return result


def to_editor_dataframe(
    workbook_data: WorkbookData,
    schema: dict,
    issues: list[dict] | None = None,
    only_rows: set[int] | None = None,
) -> pd.DataFrame:
    status_map = _row_status_map(issues or [])
    rows = []
    fields = [c["name"] for c in schema["columns"]]

    for record, source_row in zip(workbook_data.records, workbook_data.source_rows):
        if only_rows is not None and source_row not in only_rows:
            continue
        item = {
            META_ROW: source_row,
            META_STATUS: status_map.get(source_row, "✅ OK"),
            META_DELETE: False,
        }
        for field in fields:
            item[field] = _editor_value(field, record.get(field), schema)
        rows.append(item)

    columns = [META_ROW, META_STATUS, META_DELETE] + fields
    return pd.DataFrame(rows, columns=columns)


def merge_editor_dataframe(
    editor_df: pd.DataFrame,
    base_workbook: WorkbookData,
    schema: dict,
) -> WorkbookData:
    """Applica un editor anche filtrato al workbook completo.

    Le righe sono identificate dal numero di riga Excel originario. Non consente
    inserimenti arbitrari: l'operatore può modificare o marcare una riga per eliminazione.
    """
    fields = [c["name"] for c in schema["columns"]]
    by_row = {
        int(source_row): dict(record)
        for record, source_row in zip(base_workbook.records, base_workbook.source_rows)
    }
    deleted: set[int] = set()

    for _, row in editor_df.iterrows():
        raw_source = _clean_scalar(row.get(META_ROW))
        if raw_source is None:
            continue
        try:
            source_row = int(raw_source)
        except (TypeError, ValueError):
            continue
        if source_row not in by_row:
            continue

        if bool(_clean_scalar(row.get(META_DELETE)) or False):
            deleted.add(source_row)
            continue

        updated = dict(by_row[source_row])
        for field in fields:
            updated[field] = _clean_scalar(row.get(field))
        by_row[source_row] = updated

    new_records = []
    new_source_rows = []
    for source_row in base_workbook.source_rows:
        source_row = int(source_row)
        if source_row in deleted:
            continue
        new_source_rows.append(source_row)
        new_records.append(by_row[source_row])

    return replace(base_workbook, records=new_records, source_rows=new_source_rows)


def diff_editor_frames(before_df: pd.DataFrame, after_df: pd.DataFrame, schema: dict) -> list[dict]:
    fields = [c["name"] for c in schema["columns"]]

    def indexed(df: pd.DataFrame):
        result = {}
        for _, row in df.iterrows():
            raw = _clean_scalar(row.get(META_ROW))
            if raw is None:
                continue
            try:
                key = int(raw)
            except (TypeError, ValueError):
                continue
            result[key] = row
        return result

    before = indexed(before_df)
    after = indexed(after_df)
    changes = []
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    for source_row, row_after in after.items():
        row_before = before.get(source_row)
        if row_before is None:
            continue

        if bool(_clean_scalar(row_after.get(META_DELETE)) or False):
            changes.append({
                "Data/Ora": timestamp,
                "Riga Excel": source_row,
                "Campo": "(intera riga)",
                "Valore precedente": "Riga presente",
                "Valore nuovo": "Riga eliminata",
                "Azione": "ELIMINAZIONE",
            })
            continue

        for field in fields:
            old = _clean_scalar(row_before.get(field))
            new = _clean_scalar(row_after.get(field))
            if _display(old).strip() != _display(new).strip():
                changes.append({
                    "Data/Ora": timestamp,
                    "Riga Excel": source_row,
                    "Campo": field,
                    "Valore precedente": _display(old),
                    "Valore nuovo": _display(new),
                    "Azione": "MODIFICA",
                })

    return changes

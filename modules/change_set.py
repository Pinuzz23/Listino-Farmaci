from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from modules.excel_reader import WorkbookData
from modules.pipeline import process_workbook


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _same(a: Any, b: Any) -> bool:
    if a is None and b in (None, ""):
        return True
    if b is None and a in (None, ""):
        return True
    try:
        if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
            return Decimal(str(a)) == Decimal(str(b))
    except Exception:
        pass
    return _display(a).strip() == _display(b).strip()


def _change_id(row: int, field: str, old: Any, new: Any, reason: str) -> str:
    raw = f"{row}|{field}|{_display(old)}|{_display(new)}|{reason}"
    return "CH-" + sha256(raw.encode("utf-8")).hexdigest()[:10].upper()


def make_change(
    *,
    row: int,
    field: str,
    old: Any,
    new: Any,
    source: str,
    reason: str,
    confidence: str = "100%",
    risk: str = "controlled",
    tool: str = "",
    action: str = "MODIFICA",
) -> dict[str, Any]:
    return {
        "change_id": _change_id(row, field, old, new, reason),
        "Riga Excel": int(row),
        "Campo": field,
        "Valore precedente": _display(old),
        "Valore nuovo": _display(new),
        "raw_old": old,
        "raw_new": new,
        "Fonte": source,
        "Motivo": reason,
        "Confidence": confidence,
        "Rischio": risk,
        "Tool": tool,
        "Azione": action,
    }


def build_change_set(
    changes: list[dict[str, Any]],
    *,
    title: str,
    origin: str,
    description: str = "",
) -> dict[str, Any]:
    # Deduplica per riga/campo/nuovo valore mantenendo l'ordine.
    seen = set()
    clean = []
    for change in changes:
        key = (
            change.get("Riga Excel"),
            change.get("Campo"),
            _display(change.get("raw_new", change.get("Valore nuovo"))),
            change.get("Azione", "MODIFICA"),
        )
        if key in seen:
            continue
        seen.add(key)
        clean.append(change)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    digest = sha256(
        "|".join(str(item.get("change_id", "")) for item in clean).encode("utf-8")
    ).hexdigest()[:6].upper()
    return {
        "change_set_id": f"CS-{stamp}-{digest}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": title,
        "origin": origin,
        "description": description,
        "changes": clean,
        "count": len(clean),
        "risk_counts": {
            risk: sum(1 for item in clean if item.get("Rischio") == risk)
            for risk in ("safe", "controlled", "sensitive")
        },
        "status": "PROPOSTO",
    }


def validate_change_set(change_set: dict[str, Any], workbook: WorkbookData, schema: dict) -> dict[str, Any]:
    fields = {col["name"] for col in schema.get("columns", [])}
    row_map = {int(r): rec for r, rec in zip(workbook.source_rows, workbook.records)}
    errors = []
    valid = []

    for change in change_set.get("changes", []):
        try:
            row = int(change.get("Riga Excel"))
        except Exception:
            errors.append({"change_id": change.get("change_id"), "error": "Riga Excel non valida."})
            continue
        action = change.get("Azione", "MODIFICA")
        field = change.get("Campo")
        if row not in row_map:
            errors.append({"change_id": change.get("change_id"), "error": f"Riga {row} non presente."})
            continue
        if action != "ELIMINAZIONE" and field not in fields:
            errors.append({"change_id": change.get("change_id"), "error": f"Campo '{field}' non previsto."})
            continue
        if action == "MODIFICA":
            current = row_map[row].get(field)
            expected_old = change.get("raw_old", change.get("Valore precedente"))
            if not _same(current, expected_old):
                errors.append({
                    "change_id": change.get("change_id"),
                    "error": f"Il valore corrente di {field} alla riga {row} è cambiato dopo la proposta.",
                })
                continue
        valid.append(change)
    return {"valid": not errors, "errors": errors, "valid_changes": valid}


def apply_changes(
    workbook: WorkbookData,
    change_set: dict[str, Any],
    *,
    selected_ids: set[str] | None = None,
) -> tuple[WorkbookData, list[dict[str, Any]]]:
    records = deepcopy(workbook.records)
    source_rows = list(workbook.source_rows)
    index_by_row = {int(row): idx for idx, row in enumerate(source_rows)}
    applied = []
    delete_rows: set[int] = set()

    for change in change_set.get("changes", []):
        cid = str(change.get("change_id"))
        if selected_ids is not None and cid not in selected_ids:
            continue
        row = int(change["Riga Excel"])
        idx = index_by_row.get(row)
        if idx is None:
            continue
        if change.get("Azione") == "ELIMINAZIONE":
            delete_rows.add(row)
            applied.append(change)
            continue
        field = change["Campo"]
        records[idx][field] = change.get("raw_new", change.get("Valore nuovo"))
        applied.append(change)

    if delete_rows:
        kept_records, kept_rows = [], []
        for record, row in zip(records, source_rows):
            if int(row) in delete_rows:
                continue
            kept_records.append(record)
            kept_rows.append(row)
        records, source_rows = kept_records, kept_rows

    return replace(workbook, records=records, source_rows=source_rows), applied


def simulate_change_set(
    workbook: WorkbookData,
    change_set: dict[str, Any],
    schema: dict,
    aifa_dir: str,
    *,
    use_holder_as_supplier: bool = False,
    selected_ids: set[str] | None = None,
) -> dict[str, Any]:
    draft, applied = apply_changes(workbook, change_set, selected_ids=selected_ids)
    bundle = process_workbook(
        draft,
        schema,
        aifa_dir,
        use_holder_as_supplier=use_holder_as_supplier,
    )
    after = bundle["result"]
    return {
        "applied_count": len(applied),
        "after": {
            "blocking": int(after.get("blocking_count", 0)),
            "warnings": int(after.get("warning_count", 0)),
            "info": int(after.get("info_count", 0)),
            "is_valid": bool(after.get("is_valid")),
        },
        "bundle": bundle,
    }


def change_set_to_audit_rows(change_set: dict[str, Any], applied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    rows = []
    for item in applied:
        rows.append({
            "Data/Ora": timestamp,
            "Riga Excel": item.get("Riga Excel"),
            "Campo": item.get("Campo"),
            "Valore precedente": item.get("Valore precedente", ""),
            "Valore nuovo": item.get("Valore nuovo", ""),
            "Azione": "AI_" + str(item.get("Azione", "MODIFICA")),
            "Change Set": change_set.get("change_set_id"),
            "Fonte": item.get("Fonte", ""),
            "Motivo": item.get("Motivo", ""),
            "Confidence": item.get("Confidence", ""),
            "Rischio": item.get("Rischio", ""),
        })
    return rows

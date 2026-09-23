from __future__ import annotations

from decimal import Decimal
from typing import Any

from modules.excel_reader import WorkbookData
from modules.validator import normalize_identifier, normalize_text, parse_decimal, parse_percentage


def _display(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _clean_string(value: Any) -> str:
    text = str(value).replace("\u00a0", " ")
    return " ".join(text.strip().split())


def _canonical_list_value(value: Any, allowed: list[Any]) -> Any:
    cleaned = _clean_string(value)
    key = cleaned.casefold()
    for candidate in allowed:
        if _clean_string(candidate).casefold() == key:
            return candidate
    return cleaned


def _canonical_percentage(value: Any, allowed: list[Any]) -> Any:
    number = parse_percentage(value)
    allowed_decimals = [Decimal(str(v)) for v in allowed]

    # Accetta anche 10 come modo umano di esprimere 10%, ma solo se 10% è
    # effettivamente un valore previsto dallo schema.
    if number is not None and number not in allowed_decimals:
        if Decimal("1") < number <= Decimal("100"):
            scaled = number / Decimal("100")
            if scaled in allowed_decimals:
                number = scaled

    if number is None:
        return _clean_string(value) if isinstance(value, str) else value
    return float(number)


def _canonical_number(value: Any, integer: bool = False) -> Any:
    number = parse_decimal(value)
    if number is None:
        return _clean_string(value) if isinstance(value, str) else value
    if integer and number == number.to_integral_value():
        return int(number)
    return float(number)


def _changed(original: Any, normalized: Any) -> bool:
    if original is None and normalized is None:
        return False
    if isinstance(original, str):
        return original != str(normalized)
    if isinstance(original, (int, float)) and isinstance(normalized, (int, float)):
        try:
            return Decimal(str(original)) != Decimal(str(normalized))
        except Exception:
            return original != normalized
    return _display(original) != _display(normalized)


def _reason(kind: str, original: Any, normalized: Any) -> str:
    if kind == "list":
        return "Valore ricondotto alla forma canonica dell'elenco ammesso."
    if kind == "percentage_list":
        return "Aliquota IVA ricondotta alla rappresentazione numerica canonica."
    if kind in {"number", "integer"}:
        return "Valore numerico ricondotto a una rappresentazione standard."
    if kind == "identifier":
        return "Identificativo ripulito da spazi o formattazioni Excel non significative."
    return "Testo ripulito da spazi iniziali/finali o spaziature anomale."


def normalize_workbook(workbook_data: WorkbookData, schema: dict):
    lists = schema.get("lists", {})
    warn_tokens = {
        str(v).strip().casefold()
        for v in schema.get("quality", {}).get("warn_placeholder_tokens", [])
    }

    normalized_records = []
    transformations = []
    quality_issues = []

    column_map = {c["name"]: c for c in schema["columns"]}

    for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
        normalized_record = {}

        for field, original in record.items():
            column = column_map[field]
            kind = column.get("type", "string")

            cleaned_token = _clean_string(original).casefold() if isinstance(original, str) else None
            is_warn_placeholder = cleaned_token in warn_tokens if cleaned_token is not None else False

            if original is None:
                normalized = None
            elif isinstance(original, str) and original.strip() == "":
                normalized = ""
            elif is_warn_placeholder:
                normalized = None
            elif kind == "string":
                normalized = _clean_string(original)
            elif kind == "identifier":
                normalized = normalize_identifier(original)
                if isinstance(normalized, str):
                    normalized = _clean_string(normalized)
                if column.get("format") == "aic" and normalized:
                    digits = "".join(ch for ch in str(normalized) if ch.isdigit())
                    if digits and len(digits) <= 9:
                        normalized = digits.zfill(9)
            elif kind == "list":
                normalized = _canonical_list_value(original, lists[column["list_name"]])
            elif kind == "percentage_list":
                normalized = _canonical_percentage(original, lists[column["list_name"]])
            elif kind == "number":
                normalized = _canonical_number(original, integer=False)
            elif kind == "integer":
                normalized = _canonical_number(original, integer=True)
            else:
                normalized = original

            normalized_record[field] = normalized

            if is_warn_placeholder:
                quality_issues.append({
                    "Riga Excel": excel_row,
                    "Campo": field,
                    "Valore ricevuto": _display(original),
                    "Codice Errore": "VALORE_SEGNAPOSTO_DA_VERIFICARE",
                    "Livello": "WARNING",
                    "Descrizione": "È stato rilevato un valore segnaposto non consigliato; nel dataset normalizzato viene trasformato in cella vuota.",
                    "Valori ammessi / Regola": "Preferire una cella vuota quando il dato non è disponibile.",
                })

            if _changed(original, normalized):
                change_reason = (
                    "Valore segnaposto trasformato in cella vuota."
                    if is_warn_placeholder
                    else _reason(kind, original, normalized)
                )
                transformations.append({
                    "Riga Excel": excel_row,
                    "Campo": field,
                    "Valore originale": _display(original),
                    "Valore normalizzato": _display(normalized),
                    "Motivo": change_reason,
                })
                if not is_warn_placeholder:
                    quality_issues.append({
                        "Riga Excel": excel_row,
                        "Campo": field,
                        "Valore ricevuto": _display(original),
                        "Codice Errore": "NORMALIZZAZIONE_APPLICATA",
                        "Livello": "INFO",
                        "Descrizione": change_reason,
                        "Valori ammessi / Regola": f"Valore normalizzato: {_display(normalized)}",
                    })

        normalized_records.append(normalized_record)

    normalized_data = WorkbookData(
        filename=workbook_data.filename,
        headers=list(workbook_data.headers),
        header_row=workbook_data.header_row,
        records=normalized_records,
        source_rows=list(workbook_data.source_rows),
        sheet_names=list(workbook_data.sheet_names),
        extra_header_cells=list(workbook_data.extra_header_cells),
        leading_rows_present=workbook_data.leading_rows_present,
    )

    return normalized_data, transformations, quality_issues

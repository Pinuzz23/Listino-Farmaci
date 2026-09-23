from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from modules.storage_rules import infer_storage_group


LEVEL_BLOCK = "BLOCCANTE"
LEVEL_WARNING = "WARNING"
LEVEL_INFO = "INFO"


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def is_semantic_blank(value: Any, blank_tokens=None) -> bool:
    if is_blank(value):
        return True
    tokens = {str(token).strip().upper() for token in (blank_tokens or [])}
    return str(value).strip().upper() in tokens


def display_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\u00a0", " ").strip().split())


def parse_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        if text == "":
            return None
        text = text.replace("€", "")
        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return Decimal(text)
        except InvalidOperation:
            return None
    return None


def parse_percentage(value: Any) -> Decimal | None:
    if isinstance(value, str):
        text = value.strip().replace(" ", "")
        if text.endswith("%"):
            number = parse_decimal(text[:-1])
            return None if number is None else number / Decimal("100")
    return parse_decimal(value)


def normalize_identifier(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def add_issue(issues, row, field, value, code, level, description, allowed=""):
    issues.append({
        "Riga Excel": row,
        "Campo": field,
        "Valore ricevuto": display_value(value),
        "Codice Errore": code,
        "Livello": level,
        "Descrizione": description,
        "Valori ammessi / Regola": allowed,
    })


def validate_workbook(workbook_data, schema, initial_issues=None):
    issues = list(initial_issues or [])
    expected_headers = [column["name"] for column in schema["columns"]]
    global_blank_tokens = schema.get("quality", {}).get("semantic_blank_tokens", [])

    # 1. Struttura/intestazioni.
    for pos, expected in enumerate(expected_headers):
        actual = workbook_data.headers[pos] if pos < len(workbook_data.headers) else ""
        if normalize_text(actual) != normalize_text(expected):
            add_issue(
                issues,
                workbook_data.header_row,
                f"Colonna {pos + 1}",
                actual,
                "INTESTAZIONE_NON_VALIDA",
                LEVEL_BLOCK,
                f"L'intestazione non coincide con il tracciato atteso. Atteso '{expected}'.",
                expected,
            )

    for col_letter, value in workbook_data.extra_header_cells:
        add_issue(
            issues,
            workbook_data.header_row,
            col_letter,
            value,
            "COLONNA_EXTRA",
            LEVEL_BLOCK,
            "È presente una colonna aggiuntiva non prevista dal tracciato.",
            "Rimuovere la colonna aggiuntiva.",
        )

    preferred_header_row = int(schema.get("preferred_header_row", 1))
    accepted_header_rows = {int(v) for v in schema.get("accepted_header_rows", [preferred_header_row])}
    if workbook_data.header_row not in accepted_header_rows:
        accepted_label = ", ".join(str(v) for v in sorted(accepted_header_rows))
        add_issue(
            issues,
            workbook_data.header_row,
            "Struttura file",
            f"Intestazioni alla riga {workbook_data.header_row}",
            "RIGHE_PRIMA_DELL_INTESTAZIONE",
            LEVEL_WARNING,
            "Le intestazioni sono state rilevate fuori dalle righe standard previste. Il file viene comunque letto correttamente.",
            f"Righe intestazione ammesse: {accepted_label}.",
        )

    # 2. Validazione delle singole celle sul dataset già normalizzato.
    lists = schema.get("lists", {})

    for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
        for column in schema["columns"]:
            name = column["name"]
            value = record.get(name)
            required = column.get("required", False)
            kind = column.get("type", "string")

            if is_semantic_blank(value, global_blank_tokens):
                if required:
                    add_issue(
                        issues,
                        excel_row,
                        name,
                        value,
                        "CAMPO_OBBLIGATORIO",
                        LEVEL_BLOCK,
                        f"Il campo '{name}' è obbligatorio.",
                        "Valorizzare il campo con un dato reale; non utilizzare valori segnaposto.",
                    )
                continue

            if kind == "string":
                continue

            if kind == "identifier":
                identifier = normalize_identifier(value)
                if column.get("format") == "aic":
                    if not re.fullmatch(r"\d{9}", identifier):
                        add_issue(
                            issues,
                            excel_row,
                            name,
                            value,
                            "FORMATO_AIC_DA_VERIFICARE",
                            LEVEL_WARNING,
                            "L'AIC valorizzato non è composto da 9 cifre.",
                            "Formato atteso: 9 cifre (es. 012745055).",
                        )
                continue

            if kind == "list":
                allowed = lists[column["list_name"]]
                received = normalize_text(value)
                allowed_normalized = [normalize_text(v) for v in allowed]
                if received not in allowed_normalized:
                    add_issue(
                        issues,
                        excel_row,
                        name,
                        value,
                        "VALORE_NON_AMMESSO",
                        LEVEL_BLOCK,
                        f"Il valore indicato per '{name}' non appartiene all'elenco ammesso.",
                        " | ".join(str(v) for v in allowed),
                    )
                continue

            if kind in {"number", "integer"}:
                number = parse_decimal(value)
                if number is None:
                    add_issue(
                        issues,
                        excel_row,
                        name,
                        value,
                        "TIPO_NUMERICO_NON_VALIDO",
                        LEVEL_BLOCK,
                        f"Il campo '{name}' deve contenere un numero.",
                        "Valore numerico.",
                    )
                    continue

                if kind == "integer" and number != number.to_integral_value():
                    add_issue(
                        issues,
                        excel_row,
                        name,
                        value,
                        "INTERO_RICHIESTO",
                        LEVEL_BLOCK,
                        f"Il campo '{name}' deve contenere un numero intero.",
                        "Numero intero.",
                    )

                if "min" in column and number < Decimal(str(column["min"])):
                    add_issue(
                        issues,
                        excel_row,
                        name,
                        value,
                        "VALORE_SOTTO_MINIMO",
                        LEVEL_BLOCK,
                        f"Il valore di '{name}' è inferiore al minimo ammesso.",
                        f"Minimo: {column['min']}",
                    )
                continue

            if kind == "percentage_list":
                number = parse_percentage(value)
                allowed = [Decimal(str(v)) for v in lists[column["list_name"]]]
                if number is None or number not in allowed:
                    add_issue(
                        issues,
                        excel_row,
                        name,
                        value,
                        "IVA_NON_AMMESSA",
                        LEVEL_BLOCK,
                        "L'aliquota IVA non appartiene all'elenco previsto.",
                        " | ".join(f"{float(v) * 100:g}%" for v in allowed),
                    )
                continue

    # 3. Coerenza tra classificazione Stupefacente, temperatura e gruppo di stivaggio.
    storage_cfg = schema.get("storage_group_rules", {})
    if storage_cfg.get("enabled", False):
        for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
            expected = infer_storage_group(
                record.get("Stupefacente"),
                record.get("Temperatura di Stivaggio"),
                schema,
            )
            expected_group = expected.get("expected")
            if not expected_group:
                continue

            actual_group = normalize_text(record.get("Gruppo di Stivaggio"))
            if actual_group.casefold() != normalize_text(expected_group).casefold():
                source = expected.get("source") or "regola di stivaggio"
                reason = expected.get("reason") or "Il gruppo di stivaggio non è coerente con le regole definite."
                add_issue(
                    issues,
                    excel_row,
                    "Gruppo di Stivaggio",
                    record.get("Gruppo di Stivaggio"),
                    storage_cfg.get("code", "GRUPPO_STIVAGGIO_NON_COHERENTE"),
                    storage_cfg.get("level", LEVEL_BLOCK),
                    f"{reason} Valore ricevuto non coerente con {source}.",
                    f"Valore atteso: {expected_group}",
                )

    # 4. Il caricamento è normalmente mono-fornitore: più fornitori = warning.
    supplier_cfg = schema.get("supplier_control", {})
    if supplier_cfg.get("single_supplier_expected", False):
        suppliers = {}
        for record in workbook_data.records:
            raw = record.get("Fornitore")
            if is_semantic_blank(raw, global_blank_tokens):
                continue
            clean = normalize_text(raw)
            suppliers.setdefault(clean.casefold(), clean)
        if len(suppliers) > 1:
            supplier_names = list(suppliers.values())
            add_issue(
                issues,
                "-",
                "Fornitore",
                " | ".join(supplier_names),
                supplier_cfg.get("code", "FORNITORI_MULTIPLI"),
                supplier_cfg.get("level", LEVEL_WARNING),
                "Nel file sono stati rilevati più fornitori. Il caricamento è normalmente riferito a un solo fornitore.",
                "Verificare che il file sia corretto. Il warning non blocca la validazione.",
            )

    # 4. Obbligatorietà condizionale (AIC oppure Codice Fornitore).
    for rule in schema.get("conditional_required", []):
        if rule.get("type") != "at_least_one":
            continue
        fields = rule.get("fields", [])
        blank_tokens = rule.get("blank_tokens", global_blank_tokens)
        for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
            if all(is_semantic_blank(record.get(field), blank_tokens) for field in fields):
                add_issue(
                    issues,
                    excel_row,
                    " / ".join(fields),
                    "",
                    rule.get("code", "CAMPO_CONDIZIONALE_MANCANTE"),
                    rule.get("level", LEVEL_BLOCK),
                    rule.get("message", "Valorizzare almeno uno dei campi richiesti."),
                    "Almeno uno tra: " + " | ".join(fields),
                )

    # 5. Regole di coerenza tra campi.
    for rule in schema.get("coherence_checks", []):
        if rule.get("type") != "product_equals":
            continue

        target_field = rule["target"]
        factor_fields = rule.get("factors", [])
        decimals = int(rule.get("decimals", 2))
        quantizer = Decimal("1").scaleb(-decimals)

        for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
            target_raw = record.get(target_field)
            factor_raw = [record.get(field) for field in factor_fields]

            if is_semantic_blank(target_raw, global_blank_tokens) or any(
                is_semantic_blank(value, global_blank_tokens) for value in factor_raw
            ):
                continue

            target = parse_decimal(target_raw)
            factors = [parse_decimal(value) for value in factor_raw]
            if target is None or any(value is None for value in factors):
                continue

            expected = Decimal("1")
            for factor in factors:
                expected *= factor

            expected_rounded = expected.quantize(quantizer, rounding=ROUND_HALF_UP)
            target_rounded = target.quantize(quantizer, rounding=ROUND_HALF_UP)

            if target_rounded != expected_rounded:
                formula = " × ".join(factor_fields)
                add_issue(
                    issues,
                    excel_row,
                    target_field,
                    target_raw,
                    rule.get("code", "COERENZA_PREZZO_CONFEZIONE"),
                    rule.get("level", LEVEL_BLOCK),
                    rule.get("message", f"Il campo '{target_field}' non rispetta la regola prevista."),
                    f"{target_field} = {formula}. Valore atteso: {expected_rounded:.{decimals}f}",
                )

    # 6. Duplicati: AIC se presente; altrimenti Fornitore + Codice Fornitore.
    for rule in schema.get("duplicate_checks", []):
        rule_type = rule.get("type")
        blank_tokens = rule.get("blank_tokens", global_blank_tokens)

        if rule_type == "aic_if_present":
            field = rule["field"]
            seen = {}
            duplicate_rows = set()
            for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
                raw = record.get(field)
                if is_semantic_blank(raw, blank_tokens):
                    continue
                value = normalize_identifier(raw)
                if value in seen:
                    duplicate_rows.update([seen[value], excel_row])
                else:
                    seen[value] = excel_row
            for excel_row in sorted(duplicate_rows):
                record = workbook_data.records[workbook_data.source_rows.index(excel_row)]
                add_issue(
                    issues,
                    excel_row,
                    field,
                    record.get(field),
                    rule.get("code", "DUPLICATO"),
                    rule.get("level", LEVEL_BLOCK),
                    rule.get("message", "Valore duplicato nel file."),
                    "Ogni AIC valorizzato deve essere univoco nel file.",
                )

        if rule_type == "supplier_code_when_no_aic":
            supplier_field = rule["supplier_field"]
            aic_field = rule["aic_field"]
            code_field = rule["supplier_code_field"]
            seen = {}
            duplicate_rows = set()
            for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
                if not is_semantic_blank(record.get(aic_field), blank_tokens):
                    continue
                supplier_code = record.get(code_field)
                if is_semantic_blank(supplier_code, blank_tokens):
                    continue
                key = (
                    normalize_text(record.get(supplier_field, "")).casefold(),
                    normalize_text(supplier_code).casefold(),
                )
                if key in seen:
                    duplicate_rows.update([seen[key], excel_row])
                else:
                    seen[key] = excel_row
            for excel_row in sorted(duplicate_rows):
                record = workbook_data.records[workbook_data.source_rows.index(excel_row)]
                value = f"{display_value(record.get(supplier_field))} | {display_value(record.get(code_field))}"
                add_issue(
                    issues,
                    excel_row,
                    f"{supplier_field} + {code_field}",
                    value,
                    rule.get("code", "DUPLICATO"),
                    rule.get("level", LEVEL_BLOCK),
                    rule.get("message", "Valore duplicato nel file."),
                    "La coppia Fornitore + Codice Fornitore deve essere univoca quando AIC è assente.",
                )

    blocking = sum(1 for issue in issues if issue["Livello"] == LEVEL_BLOCK)
    warnings = sum(1 for issue in issues if issue["Livello"] == LEVEL_WARNING)
    infos = sum(1 for issue in issues if issue["Livello"] == LEVEL_INFO)

    return {
        "issues": issues,
        "blocking_count": blocking,
        "warning_count": warnings,
        "info_count": infos,
        "rows_count": len(workbook_data.records),
        "header_row": workbook_data.header_row,
        "is_valid": blocking == 0,
    }

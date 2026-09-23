from __future__ import annotations

from modules.validator import is_semantic_blank, normalize_text, parse_decimal


def _money_range(records, field):
    values = []
    for record in records:
        value = parse_decimal(record.get(field))
        if value is not None:
            values.append(value)
    if not values:
        return None, None
    return min(values), max(values)


def build_dataset_summary(
    workbook_data,
    schema,
    transformations,
    enrichments=None,
    upc_checks=None,
    aifa_checks=None,
    supplier_proposals=None,
):
    blank_tokens = schema.get("quality", {}).get("semantic_blank_tokens", [])
    enrichments = enrichments or []
    upc_checks = upc_checks or []
    aifa_checks = aifa_checks or []
    supplier_proposals = supplier_proposals or []

    suppliers_by_key = {}
    aic_count = 0
    without_aic = 0
    active_count = 0
    without_active = 0
    atc_count = 0
    without_atc = 0
    name_count = 0
    without_name = 0

    for record in workbook_data.records:
        supplier_raw = record.get("Fornitore")
        if not is_semantic_blank(supplier_raw, blank_tokens):
            supplier = normalize_text(supplier_raw)
            suppliers_by_key.setdefault(supplier.casefold(), supplier)

        if is_semantic_blank(record.get("AIC"), blank_tokens):
            without_aic += 1
        else:
            aic_count += 1

        if is_semantic_blank(record.get("Principio Attivo"), blank_tokens):
            without_active += 1
        else:
            active_count += 1

        if is_semantic_blank(record.get("ATC7"), blank_tokens):
            without_atc += 1
        else:
            atc_count += 1

        if is_semantic_blank(record.get("Nome Commerciale"), blank_tokens):
            without_name += 1
        else:
            name_count += 1

    suppliers = list(suppliers_by_key.values())
    unit_min, unit_max = _money_range(workbook_data.records, "Prezzo Unitario")
    pack_min, pack_max = _money_range(workbook_data.records, "Prezzo Confezione")

    if len(suppliers) == 1:
        supplier_label = suppliers[0]
    elif not suppliers:
        supplier_label = "NON RILEVATO"
    else:
        supplier_label = f"MULTIPLI ({len(suppliers)})"

    upc_ok = sum(1 for item in upc_checks if item.get("Esito") == "COERENTE")
    upc_warning = sum(1 for item in upc_checks if item.get("Esito") == "DA VERIFICARE")
    upc_unchecked = sum(1 for item in upc_checks if item.get("Esito") not in {"COERENTE", "DA VERIFICARE"})
    exact_matches = sum(1 for item in aifa_checks if item.get("Match AIFA") == "ESATTO")
    match_missing = sum(1 for item in aifa_checks if item.get("Match AIFA") == "NON TROVATO")

    by_field = {}
    for item in enrichments:
        field = item.get("Campo", "")
        by_field[field] = by_field.get(field, 0) + 1

    return {
        "supplier": supplier_label,
        "suppliers": suppliers,
        "supplier_count": len(suppliers),
        "rows_count": len(workbook_data.records),
        "aic_count": aic_count,
        "without_aic_count": without_aic,
        "active_count": active_count,
        "without_active_count": without_active,
        "atc_count": atc_count,
        "without_atc_count": without_atc,
        "name_count": name_count,
        "without_name_count": without_name,
        "aifa_enrichment_count": len(enrichments),
        "aifa_enrichment_by_field": by_field,
        "aifa_exact_matches": exact_matches,
        "aifa_missing_matches": match_missing,
        "supplier_proposal_count": len(supplier_proposals),
        "aifa_upc_ok": upc_ok,
        "aifa_upc_warning": upc_warning,
        "aifa_upc_unchecked": upc_unchecked,
        "unit_price_min": unit_min,
        "unit_price_max": unit_max,
        "package_price_min": pack_min,
        "package_price_max": pack_max,
        "normalization_count": len(transformations),
    }

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from modules.aifa import lookup_by_aic
from modules.change_set import (
    apply_changes,
    build_change_set,
    make_change,
    validate_change_set,
)
from modules.storage_rules import infer_storage_group
from modules.tool_registry import ToolRegistry, ToolSpec
from modules.validator import is_semantic_blank, normalize_identifier, normalize_text, parse_decimal


def _workbook(context):
    workbook = context.get("workbook")
    if workbook is None:
        raise ValueError("Contesto workbook non disponibile per i tool di proposta.")
    return workbook


def _schema(context):
    return context.get("schema") or {}


def _row_map(context) -> dict[int, dict[str, Any]]:
    workbook = _workbook(context)
    return {int(row): record for row, record in zip(workbook.source_rows, workbook.records)}


def _rows_filter(arguments: dict[str, Any]) -> set[int] | None:
    rows = arguments.get("rows")
    if rows in (None, "", []):
        row = arguments.get("row")
        if row in (None, ""):
            return None
        rows = [row]
    if not isinstance(rows, (list, tuple, set)):
        rows = [rows]
    result = set()
    for value in rows:
        try:
            result.add(int(value))
        except Exception:
            continue
    return result or None


def _filtered_rows(context, arguments):
    row_filter = _rows_filter(arguments)
    workbook = _workbook(context)
    for record, row in zip(workbook.records, workbook.source_rows):
        row = int(row)
        if row_filter is not None and row not in row_filter:
            continue
        yield record, row


def _tool_propose_package_price(context, arguments):
    schema = _schema(context)
    changes = []
    quantizer = Decimal("0.01")
    for record, row in _filtered_rows(context, arguments):
        unit = parse_decimal(record.get("Prezzo Unitario"))
        upc = parse_decimal(record.get("UPC"))
        package = parse_decimal(record.get("Prezzo Confezione"))
        if unit is None or upc is None or package is None:
            continue
        expected = (unit * upc).quantize(quantizer, rounding=ROUND_HALF_UP)
        actual = package.quantize(quantizer, rounding=ROUND_HALF_UP)
        if actual == expected:
            continue
        changes.append(make_change(
            row=row,
            field="Prezzo Confezione",
            old=record.get("Prezzo Confezione"),
            new=float(expected),
            source="Regola interna",
            reason="Prezzo Confezione deve essere uguale a Prezzo Unitario × UPC, arrotondato a 2 decimali.",
            confidence="100% sulla formula; verificare che Prezzo Unitario e UPC siano corretti",
            risk="controlled",
            tool="validation_propose_package_price_fix",
        ))
    return build_change_set(
        changes,
        title="Correzione Prezzo Confezione",
        origin="validation_propose_package_price_fix",
        description="Ricalcola solo Prezzo Confezione; non modifica Prezzo Unitario o UPC.",
    )


def _tool_propose_storage_group(context, arguments):
    schema = _schema(context)
    changes = []
    for record, row in _filtered_rows(context, arguments):
        inferred = infer_storage_group(
            record.get("Stupefacente"),
            record.get("Temperatura di Stivaggio"),
            schema,
        )
        expected = inferred.get("expected")
        if not expected:
            continue
        actual = record.get("Gruppo di Stivaggio")
        if normalize_text(actual).casefold() == normalize_text(expected).casefold():
            continue
        changes.append(make_change(
            row=row,
            field="Gruppo di Stivaggio",
            old=actual,
            new=expected,
            source="Regola di stivaggio",
            reason=inferred.get("reason") or "Gruppo di Stivaggio derivato dalla regola configurata.",
            confidence="100% per le casistiche deterministiche configurate",
            risk="controlled",
            tool="validation_propose_storage_group_fix",
        ))
    return build_change_set(
        changes,
        title="Correzione Gruppo di Stivaggio",
        origin="validation_propose_storage_group_fix",
        description="Applica solo regole di stivaggio deterministiche configurate nel master.",
    )


def _values_equal(field: str, left: Any, right: Any) -> bool:
    a = normalize_text(left).casefold()
    b = normalize_text(right).casefold()
    if field == "ATC7":
        a = a.replace(" ", "")
        b = b.replace(" ", "")
    return a == b


def _tool_propose_aifa_alignment(context, arguments):
    schema = _schema(context)
    aifa_dir = context.get("aifa_dir")
    if not aifa_dir:
        raise ValueError("Percorso banca dati AIFA non disponibile.")
    fields_arg = arguments.get("fields") or ["Nome Commerciale", "Principio Attivo", "ATC7"]
    if isinstance(fields_arg, str):
        fields_arg = [fields_arg]
    allowed_fields = [f for f in fields_arg if f in {"Nome Commerciale", "Principio Attivo", "ATC7"}]
    blank_tokens = schema.get("quality", {}).get("semantic_blank_tokens", [])
    key_map = {"Nome Commerciale": "nome", "Principio Attivo": "principio_attivo", "ATC7": "atc"}
    changes = []
    for record, row in _filtered_rows(context, arguments):
        aic = record.get("AIC")
        if is_semantic_blank(aic, blank_tokens):
            continue
        match = lookup_by_aic(aic, aifa_dir)
        if not match:
            continue
        for field in allowed_fields:
            aifa_value = match.get(key_map[field])
            current = record.get(field)
            if is_semantic_blank(aifa_value, blank_tokens):
                continue
            if _values_equal(field, current, aifa_value):
                continue
            # Se il campo è vuoto è un completamento; se è valorizzato è un allineamento controllato.
            empty = is_semantic_blank(current, blank_tokens)
            changes.append(make_change(
                row=row,
                field=field,
                old=current,
                new=aifa_value,
                source="AIFA Open Data",
                reason=(
                    f"Campo mancante completabile tramite match AIC esatto {match.get('aic', '')}."
                    if empty
                    else f"Il valore ricevuto differisce dal riferimento AIFA per AIC {match.get('aic', '')}."
                ),
                confidence="100% sul match AIC esatto",
                risk="controlled" if empty else "sensitive",
                tool="validation_propose_aifa_alignment",
            ))
    return build_change_set(
        changes,
        title="Allineamento anagrafico AIFA",
        origin="validation_propose_aifa_alignment",
        description="Propone modifiche AIFA; le discordanti rispetto a valori già presenti sono marcate come sensibili.",
    )


def _tool_propose_supplier_from_aifa(context, arguments):
    changes = []
    row_filter = _rows_filter(arguments)
    result = context.get("result") or {}
    row_map = _row_map(context)
    for proposal in result.get("supplier_proposals", []) or []:
        try:
            row = int(proposal.get("Riga Excel"))
        except Exception:
            continue
        if row_filter is not None and row not in row_filter:
            continue
        record = row_map.get(row) or {}
        current = record.get("Fornitore")
        new = proposal.get("Azienda titolare AIFA") or proposal.get("Valore AIFA") or proposal.get("Azienda AIFA")
        if not new or normalize_text(current).casefold() == normalize_text(new).casefold():
            continue
        changes.append(make_change(
            row=row,
            field="Fornitore",
            old=current,
            new=new,
            source="AIFA Open Data",
            reason="Azienda titolare AIFA proposta come Fornitore commerciale. Le due entità possono non coincidere.",
            confidence="Match AIC esatto; equivalenza commerciale da confermare",
            risk="sensitive",
            tool="validation_propose_supplier_from_aifa",
        ))
    return build_change_set(
        changes,
        title="Proposta Fornitore da Azienda AIFA",
        origin="validation_propose_supplier_from_aifa",
        description="Operazione sensibile: Azienda titolare AIFA e Fornitore commerciale non sono semanticamente equivalenti.",
    )


def _identity_key(record: dict[str, Any]) -> tuple[str, ...] | None:
    aic = normalize_identifier(record.get("AIC"))
    if aic:
        return ("AIC", aic)
    supplier = normalize_text(record.get("Fornitore")).casefold()
    code = normalize_text(record.get("Codice Fornitore")).casefold()
    if supplier and code:
        return ("SUPPLIER_CODE", supplier, code)
    return None


def _record_signature(record: dict[str, Any], fields: list[str]) -> tuple[str, ...]:
    return tuple(normalize_text(record.get(field)).casefold() for field in fields)


def _tool_propose_identical_duplicate_deletion(context, arguments):
    workbook = _workbook(context)
    schema = _schema(context)
    fields = [c["name"] for c in schema.get("columns", [])]
    groups: dict[tuple[str, ...], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    row_filter = _rows_filter(arguments)
    for record, row in zip(workbook.records, workbook.source_rows):
        row = int(row)
        if row_filter is not None and row not in row_filter:
            continue
        key = _identity_key(record)
        if key:
            groups[key].append((row, record))

    changes = []
    ambiguous = []
    for key, rows in groups.items():
        if len(rows) < 2:
            continue
        signatures = {_record_signature(record, fields) for _, record in rows}
        if len(signatures) != 1:
            ambiguous.append({"key": key, "rows": [row for row, _ in rows], "reason": "Duplicati con contenuti diversi."})
            continue
        for row, record in rows[1:]:
            changes.append(make_change(
                row=row,
                field="(intera riga)",
                old="Riga duplicata identica",
                new="Riga eliminata",
                source="Controllo duplicati",
                reason=f"Duplicato perfettamente identico della chiave {key}; viene mantenuta la prima occorrenza.",
                confidence="100% sull'identità dei 18 campi",
                risk="sensitive",
                tool="validation_propose_identical_duplicate_deletion",
                action="ELIMINAZIONE",
            ))
    change_set = build_change_set(
        changes,
        title="Eliminazione duplicati identici",
        origin="validation_propose_identical_duplicate_deletion",
        description="Propone solo duplicati perfettamente identici. I duplicati discordanti restano da verificare manualmente.",
    )
    change_set["ambiguous_duplicates"] = ambiguous
    return change_set


def _tool_propose_all_deterministic(context, arguments):
    sets = [
        _tool_propose_package_price(context, arguments),
        _tool_propose_storage_group(context, arguments),
        _tool_propose_aifa_alignment(context, {**arguments, "fields": ["Nome Commerciale", "Principio Attivo", "ATC7"]}),
    ]
    changes = []
    for item in sets:
        changes.extend(item.get("changes", []))
    # Per "tutto ciò che puoi" escludiamo di default le proposte sensibili.
    include_sensitive = bool(arguments.get("include_sensitive", False))
    if not include_sensitive:
        changes = [item for item in changes if item.get("Rischio") != "sensitive"]
    return build_change_set(
        changes,
        title="Correzioni deterministiche disponibili",
        origin="validation_propose_all_deterministic",
        description="Raccoglie prezzo confezione, stivaggio e completamenti/allineamenti AIFA. Le modifiche sensibili sono escluse salvo richiesta esplicita.",
    )


def _tool_apply_change_set(context, arguments):
    change_set = arguments.get("change_set")
    if not isinstance(change_set, dict):
        raise ValueError("Change Set non fornito.")
    workbook = _workbook(context)
    validation = validate_change_set(change_set, workbook, _schema(context))
    if not validation["valid"]:
        return {"ok": False, "errors": validation["errors"], "workbook": workbook, "applied": []}
    ids = arguments.get("selected_ids")
    selected_ids = set(str(x) for x in ids) if isinstance(ids, (list, tuple, set)) else None
    updated, applied = apply_changes(workbook, change_set, selected_ids=selected_ids)
    return {"ok": True, "workbook": updated, "applied": applied}


def register_validation_write_tools(registry: ToolRegistry) -> ToolRegistry:
    registry.register(ToolSpec(
        "validation_propose_package_price_fix",
        "Genera un Change Set per ricalcolare Prezzo Confezione = Prezzo Unitario × UPC senza modificare i fattori.",
        {"row": "riga opzionale", "rows": "lista righe opzionale"},
        _tool_propose_package_price,
        access="generate", risk="controlled", category="validation_write",
    ))
    registry.register(ToolSpec(
        "validation_propose_storage_group_fix",
        "Genera un Change Set per correggere Gruppo di Stivaggio usando solo regole deterministiche S7/S8 e temperatura.",
        {"row": "riga opzionale", "rows": "lista righe opzionale"},
        _tool_propose_storage_group,
        access="generate", risk="controlled", category="validation_write",
    ))
    registry.register(ToolSpec(
        "validation_propose_aifa_alignment",
        "Genera un Change Set per Nome Commerciale, Principio Attivo e ATC7 usando match AIC esatto AIFA. Le discordanti sono sensibili.",
        {"row": "riga opzionale", "rows": "lista righe opzionale", "fields": "lista campi opzionale"},
        _tool_propose_aifa_alignment,
        access="generate", risk="controlled", category="aifa_write",
    ))
    registry.register(ToolSpec(
        "validation_propose_supplier_from_aifa",
        "Propone l'Azienda titolare AIFA come Fornitore quando richiesto. È una modifica sensibile perché le entità possono non coincidere.",
        {"row": "riga opzionale", "rows": "lista righe opzionale"},
        _tool_propose_supplier_from_aifa,
        access="generate", risk="sensitive", category="aifa_write",
    ))
    registry.register(ToolSpec(
        "validation_propose_identical_duplicate_deletion",
        "Propone l'eliminazione solo di duplicati perfettamente identici sui 18 campi; i duplicati discordanti restano manuali.",
        {"rows": "lista righe opzionale"},
        _tool_propose_identical_duplicate_deletion,
        access="generate", risk="sensitive", category="validation_write",
    ))
    registry.register(ToolSpec(
        "validation_propose_all_deterministic",
        "Genera un unico Change Set con tutte le correzioni deterministiche disponibili. Esclude le modifiche sensibili per default.",
        {"include_sensitive": "boolean opzionale", "rows": "lista righe opzionale"},
        _tool_propose_all_deterministic,
        access="generate", risk="controlled", category="validation_write",
    ))
    registry.register(ToolSpec(
        "validation_apply_change_set",
        "Applica un Change Set già approvato dall'operatore. Non è disponibile al planner LLM e richiede conferma esplicita.",
        {"change_set": "Change Set approvato", "selected_ids": "ID modifiche selezionate opzionale"},
        _tool_apply_change_set,
        access="write", risk="controlled", requires_confirmation=True, category="validation_write",
    ))
    return registry

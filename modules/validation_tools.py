from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from modules.tool_registry import ToolRegistry, ToolSpec
from modules.validation_write_tools import register_validation_write_tools


ISSUE_KNOWLEDGE: dict[str, dict[str, str]] = {
    "INTESTAZIONE_NON_VALIDA": {
        "meaning": "Una o più intestazioni non coincidono con il tracciato master.",
        "action": "Riallineare nome e ordine delle colonne al template master prima di proseguire.",
    },
    "COLONNA_EXTRA": {
        "meaning": "Il file contiene una colonna non prevista dal tracciato.",
        "action": "Rimuovere la colonna extra oppure trasferire il dato nelle Note se deve essere conservato.",
    },
    "CAMPO_OBBLIGATORIO": {
        "meaning": "Un campo richiesto dal tracciato è vuoto dopo normalizzazione e arricchimento AIFA.",
        "action": "Valorizzare il campo nella scheda Correggi. Se il campo è anagrafico e l'AIC è valido, verificare prima la sezione AIFA.",
    },
    "CAMPO_CONDIZIONALE_MANCANTE": {
        "meaning": "Manca un campo richiesto da una regola condizionale, ad esempio AIC oppure Codice Fornitore.",
        "action": "Compilare almeno uno dei campi richiesti dalla regola indicata nell'anomalia.",
    },
    "VALORE_NON_AMMESSO": {
        "meaning": "Il valore non appartiene all'elenco ufficiale previsto per il campo.",
        "action": "Usare il menu a tendina della scheda Correggi e scegliere uno dei valori ammessi.",
    },
    "IVA_NON_AMMESSA": {
        "meaning": "L'aliquota IVA non appartiene all'elenco configurato.",
        "action": "Impostare una delle aliquote ammesse dal master: 0%, 4%, 10% o 22%.",
    },
    "TIPO_NUMERICO_NON_VALIDO": {
        "meaning": "Un campo numerico contiene un valore che non può essere interpretato come numero.",
        "action": "Correggere il valore eliminando testo, simboli o formati non numerici.",
    },
    "INTERO_RICHIESTO": {
        "meaning": "UPC o Minimo Movimentabile richiedono un numero intero.",
        "action": "Inserire un intero positivo.",
    },
    "VALORE_SOTTO_MINIMO": {
        "meaning": "Il valore numerico è inferiore al minimo consentito.",
        "action": "Verificare il dato sorgente e riportarlo entro il range ammesso.",
    },
    "COERENZA_PREZZO_CONFEZIONE": {
        "meaning": "Prezzo Confezione non coincide con Prezzo Unitario × UPC secondo la regola del tracciato.",
        "action": "Verificare quale dei tre valori è errato e correggerlo. Il confronto monetario usa l'arrotondamento configurato.",
    },
    "PREZZO_CONFEZIONE_NON_COHERENTE": {
        "meaning": "Prezzo Confezione non coincide con Prezzo Unitario × UPC secondo la regola del tracciato.",
        "action": "Verificare Prezzo Unitario e UPC. Se sono corretti, il Copilot può proporre il ricalcolo del solo Prezzo Confezione.",
    },
    "GRUPPO_STIVAGGIO_NON_COHERENTE": {
        "meaning": "Il Gruppo di Stivaggio non è coerente con Stupefacente e/o Temperatura di Stivaggio.",
        "action": "Applicare la regola suggerita dal validatore: S7/S8 prevalgono e richiedono STUPEF; negli altri casi usare la classificazione derivata dalla temperatura.",
    },
    "DUPLICATO": {
        "meaning": "La chiave articolo è duplicata nel tracciato.",
        "action": "Confrontare le righe duplicate e mantenere/correggere quella realmente valida.",
    },
    "FORMATO_AIC_DA_VERIFICARE": {
        "meaning": "L'AIC non è espresso nel formato atteso di 9 cifre.",
        "action": "Verificare il codice e preservare gli zeri iniziali.",
    },
    "AIC_NON_TROVATO_AIFA": {
        "meaning": "L'AIC presente nel file non è stato trovato nell'indice AIFA locale.",
        "action": "Aggiornare AIFA e verificare il codice. Se continua a non essere trovato, richiedere conferma sul dato sorgente.",
    },
    "UPC_DIVERSO_DA_AIFA": {
        "meaning": "L'UPC del tracciato differisce dalle unità posologiche/confezione rilevate da AIFA.",
        "action": "È un warning: verificare se UPC rappresenta un multiplo logistico della confezione AIFA prima di correggere.",
    },
    "FORNITORI_MULTIPLI": {
        "meaning": "Nel file sono presenti più fornitori, mentre il processo è normalmente mono-fornitore.",
        "action": "Verificare che il file sia intenzionalmente multi-fornitore; il warning non blocca da solo la validazione.",
    },
    "VALORE_SEGNAPOSTO_DA_VERIFICARE": {
        "meaning": "È stato trovato un placeholder come N/A, ND o NULL invece di un dato effettivo.",
        "action": "Sostituire il placeholder con il valore reale oppure lasciare vuoto solo se il campo è facoltativo.",
    },
}



def _load_config_json(filename: str) -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "config" / filename
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tool_describe_field(context, arguments):
    field = str(arguments.get("field") or "").strip()
    dictionary = _load_config_json("data_dictionary.json")
    schema = context.get("schema") or {}
    if not field:
        return {"fields": dictionary}
    matches = {k: v for k, v in dictionary.items() if field.casefold() in k.casefold()}
    if not matches:
        return {"found": False, "field": field, "message": "Campo non trovato nel dizionario dati."}
    result = []
    for name, description in matches.items():
        cfg = next((c for c in schema.get("columns", []) if c.get("name") == name), {})
        result.append({
            "field": name,
            "description": description,
            "required": bool(cfg.get("required")),
            "type": cfg.get("type", "string"),
            "list_name": cfg.get("list_name"),
        })
    return {"found": True, "items": result}


def _tool_business_rules(context, arguments):
    rules = _load_config_json("business_rules.json")
    key = str(arguments.get("rule") or "").strip().upper()
    if not key:
        return {"rules": rules}
    matching = {k: v for k, v in rules.items() if key in k.upper()}
    return {"found": bool(matching), "rules": matching}


def _issues(context: dict[str, Any]) -> list[dict[str, Any]]:
    return list((context.get("result") or {}).get("issues", []) or [])


def _summary(context: dict[str, Any]) -> dict[str, Any]:
    result = context.get("result") or {}
    summary = result.get("summary") or {}
    issues = _issues(context)
    by_code = Counter(str(item.get("Codice Errore", "-")) for item in issues)
    by_field = Counter(str(item.get("Campo", "-")) for item in issues)
    return {
        "valid": bool(result.get("is_valid")),
        "rows": int(summary.get("rows_count", len(context.get("records") or []))),
        "blocking": int(result.get("blocking_count", 0)),
        "warnings": int(result.get("warning_count", 0)),
        "info": int(result.get("info_count", 0)),
        "aifa_exact_matches": int(summary.get("aifa_exact_matches", 0)),
        "aifa_missing_matches": int(summary.get("aifa_missing_matches", 0)),
        "aifa_enrichments": int(summary.get("aifa_enrichment_count", 0)),
        "aifa_upc_warning": int(summary.get("aifa_upc_warning", 0)),
        "operator_changes": len(result.get("operator_changes", []) or []),
        "top_issue_codes": [{"code": key, "count": value} for key, value in by_code.most_common(8)],
        "top_fields": [{"field": key, "count": value} for key, value in by_field.most_common(8)],
    }


def _tool_summary(context, arguments):
    return _summary(context)


def _tool_list_issues(context, arguments):
    issues = _issues(context)
    level = str(arguments.get("level") or "").strip().upper()
    code = str(arguments.get("code") or "").strip().upper()
    field = str(arguments.get("field") or "").strip().casefold()
    row = arguments.get("row")
    try:
        row = int(row) if row not in (None, "") else None
    except Exception:
        row = None
    limit = min(max(int(arguments.get("limit") or 25), 1), 100)

    filtered = []
    for item in issues:
        if level and str(item.get("Livello", "")).upper() != level:
            continue
        if code and str(item.get("Codice Errore", "")).upper() != code:
            continue
        if field and field not in str(item.get("Campo", "")).casefold():
            continue
        if row is not None:
            try:
                if int(item.get("Riga Excel")) != row:
                    continue
            except Exception:
                continue
        filtered.append(item)
    return {"count": len(filtered), "items": filtered[:limit], "truncated": len(filtered) > limit}


def _tool_group_issues(context, arguments):
    issues = _issues(context)
    group_by = str(arguments.get("by") or "code").strip().lower()
    level = str(arguments.get("level") or "").strip().upper()
    limit = min(max(int(arguments.get("limit") or 15), 1), 50)
    column = {
        "code": "Codice Errore",
        "field": "Campo",
        "level": "Livello",
        "row": "Riga Excel",
    }.get(group_by, "Codice Errore")
    if level:
        issues = [item for item in issues if str(item.get("Livello", "")).upper() == level]
    counts = Counter(str(item.get(column, "-")) for item in issues)
    return {
        "group_by": group_by,
        "items": [{"value": key, "count": value} for key, value in counts.most_common(limit)],
    }


def _tool_show_row(context, arguments):
    try:
        row = int(arguments.get("row"))
    except Exception as exc:
        raise ValueError("Specificare una riga Excel numerica.") from exc
    source_rows = list(context.get("source_rows") or [])
    records = list(context.get("records") or [])
    if row not in source_rows:
        return {"found": False, "row": row, "message": "Riga non presente nel dataset corrente."}
    idx = source_rows.index(row)
    return {
        "found": True,
        "row": row,
        "record": records[idx],
        "issues": [item for item in _issues(context) if str(item.get("Riga Excel")) == str(row)],
    }


def _tool_explain_issue(context, arguments):
    code = str(arguments.get("code") or "").strip().upper()
    if not code:
        raise ValueError("Specificare il Codice Errore da spiegare.")
    matching = [item for item in _issues(context) if str(item.get("Codice Errore", "")).upper() == code]
    knowledge = ISSUE_KNOWLEDGE.get(code, {})
    sample = matching[0] if matching else {}
    return {
        "code": code,
        "present_in_file": bool(matching),
        "count": len(matching),
        "meaning": knowledge.get("meaning") or sample.get("Descrizione") or "Nessuna spiegazione specifica configurata.",
        "recommended_action": knowledge.get("action") or sample.get("Valori ammessi / Regola") or "Verificare il dettaglio dell'anomalia.",
        "sample": sample,
    }


def _priority_for_issue(item: dict[str, Any]) -> tuple[int, str]:
    code = str(item.get("Codice Errore", ""))
    level = str(item.get("Livello", ""))
    if code in {"INTESTAZIONE_NON_VALIDA", "COLONNA_EXTRA"}:
        return (1, "Struttura file")
    if level == "BLOCCANTE" and code in {
        "CAMPO_OBBLIGATORIO", "CAMPO_CONDIZIONALE_MANCANTE", "TIPO_NUMERICO_NON_VALIDO",
        "INTERO_RICHIESTO", "VALORE_NON_AMMESSO", "IVA_NON_AMMESSA", "VALORE_SOTTO_MINIMO",
    }:
        return (2, "Data entry bloccante")
    if level == "BLOCCANTE":
        return (3, "Regole di coerenza")
    if code.startswith("AIC_") or "AIFA" in code or code == "UPC_DIVERSO_DA_AIFA":
        return (4, "Verifiche AIFA")
    if level == "WARNING":
        return (5, "Warning")
    return (6, "Informazioni")


def _tool_correction_plan(context, arguments):
    issues = _issues(context)
    if not issues:
        return {"valid": True, "steps": [], "message": "Non risultano anomalie da correggere."}
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for item in issues:
        priority, category = _priority_for_issue(item)
        code = str(item.get("Codice Errore", "-"))
        grouped.setdefault((priority, category, code), []).append(item)
    steps = []
    for (priority, category, code), rows in sorted(grouped.items(), key=lambda x: (x[0][0], -len(x[1]), x[0][2])):
        knowledge = ISSUE_KNOWLEDGE.get(code, {})
        steps.append({
            "priority": priority,
            "category": category,
            "code": code,
            "count": len(rows),
            "rows": [item.get("Riga Excel") for item in rows[:12]],
            "action": knowledge.get("action") or rows[0].get("Valori ammessi / Regola") or rows[0].get("Descrizione"),
        })
    return {"valid": False, "steps": steps[:20]}


def _tool_aifa_findings(context, arguments):
    result = context.get("result") or {}
    summary = result.get("summary") or {}
    return {
        "exact_matches": int(summary.get("aifa_exact_matches", 0)),
        "missing_matches": int(summary.get("aifa_missing_matches", 0)),
        "enrichments": list(result.get("aifa_enrichments", []) or [])[:50],
        "upc_checks": list(result.get("aifa_upc_checks", []) or [])[:50],
        "supplier_proposals": list(result.get("supplier_proposals", []) or [])[:30],
        "candidates_without_aic": list(context.get("aifa_candidates") or [])[:30],
    }


def _tool_normalizations(context, arguments):
    field = str(arguments.get("field") or "").strip().casefold()
    limit = min(max(int(arguments.get("limit") or 30), 1), 100)
    rows = list((context.get("result") or {}).get("transformations", []) or [])
    if field:
        rows = [item for item in rows if field in str(item.get("Campo", "")).casefold()]
    return {"count": len(rows), "items": rows[:limit], "truncated": len(rows) > limit}


def _tool_operator_changes(context, arguments):
    limit = min(max(int(arguments.get("limit") or 30), 1), 100)
    rows = list((context.get("result") or {}).get("operator_changes", []) or [])
    return {"count": len(rows), "items": rows[-limit:]}


def build_validation_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        "validation_summary",
        "Restituisce stato complessivo, conteggi errori/warning, statistiche AIFA e anomalie più frequenti.",
        {},
        _tool_summary,
    ))
    registry.register(ToolSpec(
        "validation_list_issues",
        "Elenca le anomalie correnti filtrabili per livello, codice errore, campo o riga Excel.",
        {
            "level": "BLOCCANTE|WARNING|INFO opzionale",
            "code": "Codice Errore opzionale",
            "field": "nome o parte del campo opzionale",
            "row": "riga Excel opzionale",
            "limit": "1-100",
        },
        _tool_list_issues,
    ))
    registry.register(ToolSpec(
        "validation_group_issues",
        "Raggruppa le anomalie per codice, campo, livello o riga per individuare i problemi più frequenti.",
        {"by": "code|field|level|row", "level": "livello opzionale", "limit": "1-50"},
        _tool_group_issues,
    ))
    registry.register(ToolSpec(
        "validation_show_row",
        "Mostra i valori e tutte le anomalie associate a una specifica riga Excel.",
        {"row": "numero riga Excel"},
        _tool_show_row,
    ))
    registry.register(ToolSpec(
        "validation_explain_issue",
        "Spiega un Codice Errore, quanto è frequente nel file e quale correzione è consigliata.",
        {"code": "Codice Errore"},
        _tool_explain_issue,
    ))
    registry.register(ToolSpec(
        "validation_correction_plan",
        "Costruisce un piano ordinato di correzione partendo dagli errori strutturali e bloccanti, poi warning e AIFA.",
        {},
        _tool_correction_plan,
    ))
    registry.register(ToolSpec(
        "validation_aifa_findings",
        "Mostra match AIFA, arricchimenti, controlli UPC, proposte fornitore e candidati senza AIC.",
        {},
        _tool_aifa_findings,
    ))
    registry.register(ToolSpec(
        "validation_normalizations",
        "Elenca le normalizzazioni già applicate al dataset, eventualmente filtrate per campo.",
        {"field": "campo opzionale", "limit": "1-100"},
        _tool_normalizations,
    ))
    registry.register(ToolSpec(
        "validation_operator_changes",
        "Mostra le ultime modifiche manuali/AI registrate nella sessione corrente.",
        {"limit": "1-100"},
        _tool_operator_changes,
        category="audit",
    ))
    registry.register(ToolSpec(
        "validation_describe_field",
        "Descrive semanticamente un campo del tracciato e ne indica obbligatorietà e tipo.",
        {"field": "nome o parte del nome del campo; vuoto per tutti"},
        _tool_describe_field,
        category="knowledge",
    ))
    registry.register(ToolSpec(
        "validation_business_rules",
        "Restituisce le regole di business centralizzate usate dal Copilot per spiegare le validazioni.",
        {"rule": "chiave/parola della regola opzionale"},
        _tool_business_rules,
        category="knowledge",
    ))
    return register_validation_write_tools(registry)

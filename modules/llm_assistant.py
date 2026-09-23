from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

import pandas as pd
import requests

from modules.assistant import (
    _fuzzy_from_hint,
    _match_entity,
    _unique_values,
    interpret_request,
    normalize_text,
)


ALLOWED_ACTIONS = {"search", "export", "count", "history", "aggregate"}
ALLOWED_AGGREGATIONS = {"max", "min", "avg", "sum", "count_distinct"}
ALLOWED_DIRECTIONS = {"asc", "desc"}

TRACE_FIELDS = [
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
]

TEXT_FIELDS = {
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
    "Note",
}
NUMERIC_FIELDS = {
    "Prezzo Unitario",
    "Prezzo Confezione",
    "UPC",
    "Minimo Movimentabile",
    "IVA",
}
SORTABLE_FIELDS = set(TRACE_FIELDS) | {"Ultimo aggiornamento"}

FIELD_ALIASES = {
    "fornitore": "Fornitore",
    "supplier": "Fornitore",
    "aic": "AIC",
    "codice fornitore": "Codice Fornitore",
    "codice interno fornitore": "Codice Fornitore",
    "nome commerciale": "Nome Commerciale",
    "prodotto": "Nome Commerciale",
    "farmaco": "Nome Commerciale",
    "principio attivo": "Principio Attivo",
    "molecola": "Principio Attivo",
    "materiale pericoloso": "Materiale Pericoloso",
    "pericoloso": "Materiale Pericoloso",
    "stupefacente": "Stupefacente",
    "atc7": "ATC7",
    "atc 7": "ATC7",
    "atc9": "ATC9",
    "atc 9": "ATC9",
    "fala / lasa": "Fala / Lasa",
    "fala lasa": "Fala / Lasa",
    "fala": "Fala / Lasa",
    "lasa": "Fala / Lasa",
    "gruppo di stivaggio": "Gruppo di Stivaggio",
    "gruppo stivaggio": "Gruppo di Stivaggio",
    "stivaggio": "Gruppo di Stivaggio",
    "temperatura di stivaggio": "Temperatura di Stivaggio",
    "temperatura stivaggio": "Temperatura di Stivaggio",
    "temperatura": "Temperatura di Stivaggio",
    "prezzo unitario": "Prezzo Unitario",
    "prezzo confezione": "Prezzo Confezione",
    "prezzo a confezione": "Prezzo Confezione",
    "upc": "UPC",
    "unita per confezione": "UPC",
    "unità per confezione": "UPC",
    "minimo movimentabile": "Minimo Movimentabile",
    "iva": "IVA",
    "note": "Note",
    "ultimo aggiornamento": "Ultimo aggiornamento",
}

SYSTEM_PROMPT = r"""
Sei l'interprete di richieste per un listino farmaceutico locale.
NON devi rispondere alla domanda, NON devi scrivere SQL e NON devi inventare dati.
Devi SOLO trasformare la frase italiana dell'utente in JSON strutturato.

Campi interrogabili del tracciato:
- Fornitore
- AIC
- Codice Fornitore
- Nome Commerciale
- Principio Attivo
- Materiale Pericoloso
- Stupefacente
- ATC7
- ATC9
- Fala / Lasa
- Gruppo di Stivaggio
- Temperatura di Stivaggio
- Prezzo Unitario
- Prezzo Confezione
- UPC
- Minimo Movimentabile
- IVA
- Note

Puoi anche ordinare per Ultimo aggiornamento.

Restituisci ESCLUSIVAMENTE un oggetto JSON valido con questa struttura:
{
  "action": "search|export|count|history|aggregate",
  "filters": [
    {"field": "Nome campo", "operator": "eq|contains|gt|gte|lt|lte|between", "value": null, "value2": null}
  ],
  "sort": {"field": null, "direction": "asc|desc"},
  "limit": null,
  "aggregation": {"function": null, "field": null},
  "needs_clarification": false,
  "clarification": ""
}

Regole azione:
- export: scarica, estrai, esporta, excel, xlsx, download.
- count: quanti, quante, conta, numero di.
- history: storico prezzi, andamento prezzo.
- aggregate: richieste di valore massimo/minimo/medio/somma/distinti quando NON chiedono esplicitamente il prodotto o l'estrazione della riga.
- search: tutti gli altri casi.

Regole filtri:
- Usa SOLO i nomi campo dell'elenco sopra.
- eq = uguaglianza, contains = contiene testo.
- gt/gte/lt/lte/between solo sui campi numerici Prezzo Unitario, Prezzo Confezione, UPC, Minimo Movimentabile, IVA.
- Per between usa value come limite inferiore e value2 come limite superiore.
- IVA va espressa come percentuale leggibile: 0, 4, 10 o 22 (non 0.10).
- Materiale Pericoloso e Fala / Lasa: usa Y o N.
- Non inventare nomi di fornitori, prodotti, principi attivi o codici.

Regole ordinamento e ranking:
- "più alto", "massimo", "maggiore" -> sort direction desc.
- "più basso", "minimo", "minore" -> sort direction asc.
- "il prodotto con ... più alto/più basso" -> action search, sort sul campo, limit 1.
- "estrai/scarica il prodotto con ... più alto/più basso" -> action export, sort sul campo, limit 1.
- IMPORTANTE: nelle richieste di ranking (più alto/più basso/top/minimo/massimo) NON aggiungere un filtro sul campo ordinato se l'utente non ha indicato una soglia numerica. In questi casi filters deve restare vuoto e devi usare sort + limit.
- "i 10 prodotti con prezzo più alto" -> limit 10.
- Se l'utente dice genericamente "prezzo" senza specificare unitario/confezione, usa Prezzo Confezione.

Regole aggregazione:
- "qual è il prezzo unitario più alto" -> action aggregate, aggregation max su Prezzo Unitario.
- "qual è il prezzo medio" -> aggregation avg sul campo prezzo richiesto.
- "somma" -> sum.
- "quanti valori distinti" -> count_distinct.
- Per max/min, se l'utente chiede anche QUALE prodotto, usa invece sort + limit 1.

Esempi:
Utente: "estrai il prodotto con il prezzo unitario più alto"
JSON: {"action":"export","filters":[],"sort":{"field":"Prezzo Unitario","direction":"desc"},"limit":1,"aggregation":{"function":null,"field":null},"needs_clarification":false,"clarification":""}

Utente: "estrai il prodotto con prezzo unitario più basso"
JSON: {"action":"export","filters":[],"sort":{"field":"Prezzo Unitario","direction":"asc"},"limit":1,"aggregation":{"function":null,"field":null},"needs_clarification":false,"clarification":""}

Utente: "prodotti pericolosi con UPC maggiore di 20"
JSON: {"action":"search","filters":[{"field":"Materiale Pericoloso","operator":"eq","value":"Y","value2":null},{"field":"UPC","operator":"gt","value":20,"value2":null}],"sort":{"field":null,"direction":"asc"},"limit":null,"aggregation":{"function":null,"field":null},"needs_clarification":false,"clarification":""}

Utente: "scarica i prodotti con temperatura da + 2 a 8°C"
JSON: {"action":"export","filters":[{"field":"Temperatura di Stivaggio","operator":"eq","value":"da + 2 a 8°C","value2":null}],"sort":{"field":null,"direction":"asc"},"limit":null,"aggregation":{"function":null,"field":null},"needs_clarification":false,"clarification":""}

Utente: "note contengono urgente"
JSON: {"action":"search","filters":[{"field":"Note","operator":"contains","value":"urgente","value2":null}],"sort":{"field":null,"direction":"asc"},"limit":null,"aggregation":{"function":null,"field":null},"needs_clarification":false,"clarification":""}

Se la frase è incompleta o manca il valore richiesto, needs_clarification=true.
Non usare markdown, commenti o testo fuori dal JSON.
""".strip()


def _base_url(url: str) -> str:
    return (url or "http://127.0.0.1:11434").rstrip("/")


def ollama_status(base_url: str = "http://127.0.0.1:11434", timeout: float = 2.0) -> dict:
    url = _base_url(base_url)
    try:
        response = requests.get(f"{url}/api/tags", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        models = []
        for item in payload.get("models", []) or []:
            name = item.get("name") or item.get("model")
            if name:
                models.append(str(name))
        return {"available": True, "models": sorted(set(models)), "error": None}
    except Exception as exc:
        return {"available": False, "models": [], "error": str(exc)}


def _extract_json(content: str) -> dict:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(text[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("Il modello non ha restituito un JSON interpretabile.")


def call_ollama(
    user_text: str,
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
) -> dict:
    if not model:
        raise ValueError("Nessun modello Ollama selezionato.")
    response = requests.post(
        f"{_base_url(base_url)}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    content = ((data.get("message") or {}).get("content") or "").strip()
    return _extract_json(content)


def _canonical_field(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text in SORTABLE_FIELDS:
        return text
    norm = normalize_text(text)
    if norm in FIELD_ALIASES:
        return FIELD_ALIASES[norm]
    for field in SORTABLE_FIELDS:
        if normalize_text(field) == norm:
            return field
    return None


def _explicit_full_catalogue_requested(text: str) -> bool:
    norm = normalize_text(text)
    patterns = (
        r"\b(?:scarica|scaricami|esporta|estrai|mostra|visualizza)\s+(?:tutto|tutti)\s+(?:il\s+)?(?:listino|catalogo|prodotti)\b",
        r"\b(?:scarica|scaricami|esporta|estrai|mostra|visualizza)\s+(?:il\s+)?(?:listino|catalogo)\s+(?:completo|intero)\b",
        r"\b(?:listino|catalogo)\s+(?:completo|intero)\b",
    )
    return any(re.search(pattern, norm) for pattern in patterns)


def _top_suggestions(query: str, values: list[str], limit: int = 5) -> list[str]:
    query_n = normalize_text(query)
    if not query_n:
        return values[:limit]
    scored = []
    for value in values:
        ratio = SequenceMatcher(None, query_n, normalize_text(value)).ratio()
        scored.append((ratio, value))
    scored.sort(reverse=True)
    return [value for score, value in scored[:limit] if score >= 0.30]


def _resolve_entity(raw: Any, values: list[str], fuzzy_threshold: float = 0.70, token_match: bool = False) -> tuple[str | None, list[str]]:
    text = str(raw or "").strip()
    if not text:
        return None, []
    direct = _match_entity(text, values, allow_token_match=token_match)
    if direct:
        return direct, []
    fuzzy = _fuzzy_from_hint(text, values, fuzzy_threshold)
    if fuzzy:
        return fuzzy, []
    return None, _top_suggestions(text, values)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace("€", "").replace("%", "").replace(" ", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
        return float(text)
    except Exception:
        return None


def _yn_value(raw: Any) -> str | None:
    norm = normalize_text(raw)
    yes = {"y", "yes", "si", "sì", "vero", "true", "pericoloso"}
    no = {"n", "no", "falso", "false", "non pericoloso"}
    if norm in {normalize_text(v) for v in yes}:
        return "Y"
    if norm in {normalize_text(v) for v in no}:
        return "N"
    return None


def _storage_value(raw: Any) -> str | None:
    norm = normalize_text(raw)
    mapping = {
        "frigo": "FRIGO_Frigo",
        "frigorifero": "FRIGO_Frigo",
        "refrigerato": "FRIGO_Frigo",
        "freezer": "FREEZER_Freezer",
        "congelatore": "FREEZER_Freezer",
        "standard": "STD_Standard",
        "temperatura ambiente": "STD_Standard",
        "stupef": "STUPEF_Stupefacenti",
        "stupefacenti": "STUPEF_Stupefacenti",
    }
    if norm in mapping:
        return mapping[norm]
    for canonical in ("FRIGO_Frigo", "FREEZER_Freezer", "STD_Standard", "STUPEF_Stupefacenti"):
        if normalize_text(canonical) == norm:
            return canonical
    return None


def _ground_text_filter(field: str, operator: str, raw: Any, catalogue: pd.DataFrame) -> tuple[dict | None, list[str], str | None]:
    value = str(raw or "").strip()
    if not value:
        return None, [], field

    if field == "AIC":
        digits = re.sub(r"\D", "", value)
        if len(digits) not in (8, 9):
            return None, [], field
        return {"field": field, "operator": "eq", "value": digits.zfill(9)}, [], None

    if field in {"ATC7", "ATC9"}:
        code = re.sub(r"\s+", "", value).upper()
        if not re.fullmatch(r"[A-Z0-9]{3,12}", code):
            return None, [], field
        return {"field": field, "operator": "eq", "value": code}, [], None

    if field in {"Materiale Pericoloso", "Fala / Lasa"}:
        yn = _yn_value(value)
        if not yn:
            return None, ["Y", "N"], field
        return {"field": field, "operator": "eq", "value": yn}, [], None

    if field == "Gruppo di Stivaggio":
        mapped = _storage_value(value)
        if mapped:
            return {"field": field, "operator": "eq", "value": mapped}, [], None

    if field == "Note" and operator == "contains":
        return {"field": field, "operator": "contains", "value": value}, [], None

    if field == "Codice Fornitore" and operator == "contains":
        return {"field": field, "operator": "contains", "value": value}, [], None

    values = _unique_values(catalogue, field)
    token_match = field == "Fornitore"
    threshold = 0.66 if field == "Fornitore" else 0.70

    if operator == "contains":
        matches = [v for v in values if normalize_text(value) in normalize_text(v)]
        if matches:
            return {"field": field, "operator": "contains", "value": value}, [], None
        return None, _top_suggestions(value, values), field

    resolved, suggestions = _resolve_entity(value, values, threshold, token_match)
    if resolved:
        return {"field": field, "operator": "eq", "value": resolved}, [], None

    # Exact identifiers/codes can safely return zero rows rather than broaden the query.
    if field == "Codice Fornitore":
        return {"field": field, "operator": "eq", "value": value}, [], None

    return None, suggestions, field


def _ground_numeric_filter(field: str, operator: str, value: Any, value2: Any = None) -> tuple[dict | None, str | None]:
    if operator not in {"eq", "gt", "gte", "lt", "lte", "between"}:
        return None, field
    v1 = _to_float(value)
    v2 = _to_float(value2)
    if v1 is None:
        return None, field
    if field == "IVA":
        # LLM uses 0/4/10/22; database stores decimal fractions.
        if v1 > 1:
            v1 /= 100.0
        if v2 is not None and v2 > 1:
            v2 /= 100.0
    if operator == "between":
        if v2 is None:
            return None, field
        lo, hi = sorted((v1, v2))
        return {"field": field, "operator": operator, "value": lo, "value2": hi}, None
    return {"field": field, "operator": operator, "value": v1}, None


def _format_filter_label(item: dict) -> str:
    field = item["field"]
    op = item["operator"]
    value = item.get("value")
    value2 = item.get("value2")
    if field == "IVA" and isinstance(value, (int, float)):
        value = f"{value * 100:g}%"
        if isinstance(value2, (int, float)):
            value2 = f"{value2 * 100:g}%"
    labels = {"eq": "=", "contains": "contiene", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "tra"}
    if op == "between":
        return f"{field} tra {value} e {value2}"
    return f"{field} {labels.get(op, op)} {value}"


def _infer_ranking_from_text(text: str) -> dict | None:
    norm = normalize_text(text)
    direction = None
    if any(term in norm for term in ("piu alto", "piu alta", "massimo", "massima", "maggiore", "piu grande")):
        direction = "desc"
    elif any(term in norm for term in ("piu basso", "piu bassa", "minimo", "minima", "minore", "piu piccolo")):
        direction = "asc"
    if not direction:
        return None

    aliases = [
        ("prezzo unitario", "Prezzo Unitario"),
        ("prezzo confezione", "Prezzo Confezione"),
        ("prezzo a confezione", "Prezzo Confezione"),
        ("minimo movimentabile", "Minimo Movimentabile"),
        ("upc", "UPC"),
        ("iva", "IVA"),
    ]
    field = None
    for alias, canonical in aliases:
        if alias in norm:
            field = canonical
            break
    if field is None and "prezzo" in norm:
        field = "Prezzo Confezione"
    if field is None:
        return None

    m = re.search(r"\b(?:i|le|primi|prime|top)\s*(\d{1,4})\b", norm)
    limit = max(1, min(int(m.group(1)), 5000)) if m else 1
    return {"field": field, "direction": direction, "limit": limit}


def _action_from_text(text: str) -> str | None:
    norm = normalize_text(text)
    if any(term in norm for term in ("storico prezzi", "storico del prezzo", "andamento prezzi", "andamento del prezzo")):
        return "history"
    if re.search(r"\b(quanti|quante|conteggio|conta|numero di)\b", norm):
        return "count"
    if any(term in norm for term in ("scarica", "scaricami", "esporta", "estrai", "excel", "xlsx", "download")):
        return "export"
    return None


def ground_llm_payload(payload: dict, catalogue: pd.DataFrame, original_text: str, model: str | None = None) -> dict:
    action = str(payload.get("action") or "search").strip().lower()
    if action not in ALLOWED_ACTIONS:
        action = "search"
    deterministic_action = _action_from_text(original_text)
    # Keep an explicit aggregate intent from the LLM (e.g. "quanti valori distinti")
    # instead of flattening it to a simple row count.
    if deterministic_action and action != "aggregate":
        action = deterministic_action

    grounded_filters: list[dict] = []
    unresolved: list[str] = []
    suggestions: dict[str, list[str]] = {}

    # Infer ranking directly from the original sentence before grounding LLM filters.
    # This deterministic layer protects against a common LLM artefact where a ranking
    # field is incorrectly emitted as an empty equality filter, e.g.
    # {"field": "Prezzo Unitario", "operator": "eq", "value": null}.
    inferred_ranking = _infer_ranking_from_text(original_text)

    raw_filters = payload.get("filters")
    if not isinstance(raw_filters, list):
        raw_filters = []

    for raw_item in raw_filters:
        if not isinstance(raw_item, dict):
            continue
        field = _canonical_field(raw_item.get("field"))
        if not field or field not in TRACE_FIELDS:
            unresolved.append(str(raw_item.get("field") or "Campo non riconosciuto"))
            continue
        operator = str(raw_item.get("operator") or "eq").lower().strip()
        if field in NUMERIC_FIELDS:
            # Ranking queries do not require a numeric threshold. Some local models
            # nevertheless return the ranking field as an empty filter. Ignore only
            # that empty artefact; a real numeric filter with a supplied value is
            # still grounded and validated normally.
            raw_value = raw_item.get("value")
            raw_value2 = raw_item.get("value2")
            empty_numeric_filter = raw_value in (None, "") and raw_value2 in (None, "")
            ranking_same_field = bool(inferred_ranking and inferred_ranking.get("field") == field)
            if empty_numeric_filter and ranking_same_field:
                continue
            item, err = _ground_numeric_filter(field, operator, raw_value, raw_value2)
            if err:
                unresolved.append(err)
            elif item:
                grounded_filters.append(item)
        else:
            if operator not in {"eq", "contains"}:
                unresolved.append(field)
                continue
            item, hints, err = _ground_text_filter(field, operator, raw_item.get("value"), catalogue)
            if err:
                unresolved.append(err)
                if hints:
                    suggestions[field] = hints
            elif item:
                grounded_filters.append(item)

    raw_sort = payload.get("sort") if isinstance(payload.get("sort"), dict) else {}
    sort_field = _canonical_field(raw_sort.get("field"))
    sort_direction = str(raw_sort.get("direction") or "asc").lower().strip()
    sort_spec = None
    if raw_sort.get("field") not in (None, ""):
        if not sort_field or sort_field not in SORTABLE_FIELDS:
            unresolved.append("Campo ordinamento")
        elif sort_direction not in ALLOWED_DIRECTIONS:
            unresolved.append("Direzione ordinamento")
        else:
            sort_spec = {"field": sort_field, "direction": sort_direction}

    limit = payload.get("limit")
    if limit not in (None, ""):
        try:
            limit = max(1, min(int(limit), 5000))
        except Exception:
            unresolved.append("Limite risultati")
            limit = None
    else:
        limit = None

    if inferred_ranking:
        if sort_spec is None:
            sort_spec = {"field": inferred_ranking["field"], "direction": inferred_ranking["direction"]}
        if limit is None and sort_spec.get("field") == inferred_ranking["field"]:
            limit = inferred_ranking["limit"]

    aggregation = None
    raw_agg = payload.get("aggregation") if isinstance(payload.get("aggregation"), dict) else {}
    agg_func = str(raw_agg.get("function") or "").lower().strip()
    agg_field = _canonical_field(raw_agg.get("field"))
    if agg_func:
        if agg_func not in ALLOWED_AGGREGATIONS:
            unresolved.append("Funzione aggregazione")
        elif not agg_field or agg_field not in TRACE_FIELDS:
            unresolved.append("Campo aggregazione")
        elif agg_func in {"max", "min", "avg", "sum"} and agg_field not in NUMERIC_FIELDS:
            unresolved.append("Campo numerico aggregazione")
        else:
            aggregation = {"function": agg_func, "field": agg_field}
            if action not in {"export", "history", "count"}:
                action = "aggregate"

    explicit_full = _explicit_full_catalogue_requested(original_text)
    has_scope = bool(grounded_filters or sort_spec or aggregation)

    # Never allow an export with an empty/accidentally broadened query.
    if action == "export" and not has_scope and not explicit_full and not unresolved:
        unresolved.append("Filtro o criterio di estrazione")
    if action == "history" and not grounded_filters and not unresolved:
        unresolved.append("Prodotto/offerta per storico")
    if action == "search" and not has_scope and not explicit_full and not unresolved:
        unresolved.append("Filtro o criterio di ricerca")
    if action == "aggregate" and not aggregation and not unresolved:
        unresolved.append("Aggregazione")

    if payload.get("needs_clarification") and not has_scope and action != "count":
        unresolved.append("Richiesta da chiarire")

    unresolved = list(dict.fromkeys(unresolved))
    recognised = [_format_filter_label(item) for item in grounded_filters]
    if sort_spec:
        recognised.append(f"Ordina {sort_spec['field']} {'decrescente' if sort_spec['direction'] == 'desc' else 'crescente'}")
    if limit:
        recognised.append(f"Limite = {limit}")
    if aggregation:
        recognised.append(f"{aggregation['function'].upper()}({aggregation['field']})")

    return {
        "query_version": 2,
        "text": original_text,
        "action": action,
        "filters": {},  # legacy compatibility
        "generic_filters": grounded_filters,
        "sort": sort_spec,
        "limit": limit,
        "aggregation": aggregation,
        "recognised": recognised,
        "unresolved": unresolved,
        "blocking_ambiguity": bool(unresolved),
        "explicit_full_catalogue": explicit_full,
        "available_suppliers": _unique_values(catalogue, "Fornitore"),
        "suggestions": suggestions,
        "engine": "ollama",
        "llm_model": model,
        "llm_payload": payload,
        "clarification": str(payload.get("clarification") or "").strip(),
    }


def _legacy_to_v2(request: dict) -> dict:
    """Convert the deterministic R5 fallback into the generic R6 request shape."""
    if request.get("query_version") == 2:
        return request
    generic = []
    filters = request.get("filters", {}) or {}
    for field, value in filters.items():
        if field.startswith("_"):
            continue
        generic.append({"field": field, "operator": "eq", "value": value})
    price = filters.get("_price") or {}
    if price:
        field = price.get("field", "Prezzo Confezione")
        if "min" in price and "max" in price:
            generic.append({"field": field, "operator": "between", "value": price["min"], "value2": price["max"]})
        elif "min" in price:
            generic.append({"field": field, "operator": "gte", "value": price["min"]})
        elif "max" in price:
            generic.append({"field": field, "operator": "lte", "value": price["max"]})
    request = dict(request)
    request["query_version"] = 2
    request["generic_filters"] = generic
    request.setdefault("sort", None)
    request.setdefault("limit", None)
    request.setdefault("aggregation", None)
    return request


def interpret_request_llm(
    text: str,
    catalogue: pd.DataFrame,
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
    fallback_to_rules: bool = True,
) -> dict:
    try:
        payload = call_ollama(text, model=model, base_url=base_url, timeout=timeout)
        return ground_llm_payload(payload, catalogue, text, model=model)
    except Exception as exc:
        if not fallback_to_rules:
            return {
                "query_version": 2,
                "text": text,
                "action": "search",
                "filters": {},
                "generic_filters": [],
                "sort": None,
                "limit": None,
                "aggregation": None,
                "recognised": [],
                "unresolved": ["Motore LLM non disponibile"],
                "blocking_ambiguity": True,
                "explicit_full_catalogue": False,
                "available_suppliers": _unique_values(catalogue, "Fornitore"),
                "suggestions": {},
                "engine": "ollama_error",
                "llm_model": model,
                "llm_error": str(exc),
            }

        request = _legacy_to_v2(interpret_request(text, catalogue))
        request["engine"] = "rules_fallback"
        request["llm_model"] = model
        request["llm_error"] = str(exc)
        return request

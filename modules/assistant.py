from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

import pandas as pd


LEGAL_TOKENS = {
    "srl", "spa", "s", "p", "a", "srls", "sas", "snc", "nv", "ltd", "limited",
    "pharma", "farmaceutica", "farmaceutici", "farmaceutiche", "italia", "italiana",
    "italiano", "group", "gruppo", "company", "co",
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().casefold()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9%+.,<>=\-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _meaningful_tokens(value: str) -> set[str]:
    tokens = {tok for tok in normalize_text(value).split() if len(tok) >= 3}
    return {tok for tok in tokens if tok not in LEGAL_TOKENS}


def _unique_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns:
        return []
    vals = df[column].dropna().astype(str).str.strip()
    vals = vals[vals.ne("")]
    return sorted(vals.unique().tolist(), key=str.casefold)


def _match_entity(request: str, values: list[str], allow_token_match: bool = True) -> str | None:
    """Resolve a user phrase to one of the real values in the published catalogue."""
    if not values:
        return None
    req = normalize_text(request)

    # 1. Exact phrase containment: strongest and safest match.
    exact = []
    for v in values:
        nv = normalize_text(v)
        if not nv:
            continue
        # Match full normalized tokens/phrases, never raw substrings (e.g. value "No" must not match "listino").
        pattern = r"(?<![a-z0-9])" + re.escape(nv).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
        if re.search(pattern, req):
            exact.append(v)
    if exact:
        exact.sort(key=lambda v: len(normalize_text(v)), reverse=True)
        return exact[0]

    # 2. Distinctive token overlap, useful for legal company names (e.g. Angelini -> ANGELINI PHARMA S.P.A.).
    if allow_token_match:
        req_tokens = set(req.split())
        scored: list[tuple[float, int, str]] = []
        for value in values:
            tokens = _meaningful_tokens(value)
            if not tokens:
                continue
            overlap = tokens & req_tokens
            if not overlap:
                continue
            score = len(overlap) / len(tokens)
            # A single long distinctive token is enough for a short company name.
            longest = max((len(tok) for tok in overlap), default=0)
            if score >= 0.45 or longest >= 6:
                scored.append((score, longest, value))
        if scored:
            scored.sort(reverse=True)
            best = scored[0]
            # Avoid ambiguous matches with effectively equal scores.
            if len(scored) == 1 or best[:2] > scored[1][:2]:
                return best[2]

    return None


def _capture_after(text: str, marker: str) -> str:
    norm = normalize_text(text)
    pos = norm.find(marker)
    if pos < 0:
        return ""
    tail = norm[pos + len(marker):].strip(" :,-")
    # Cut on common filter conjunctions, but retain multi-word entity names.
    cuts = [" con iva ", " con prezzo ", " sotto ", " sopra ", " in frigo", " in freezer", " che costano"]
    end = len(tail)
    for cut in cuts:
        idx = tail.find(cut)
        if idx >= 0:
            end = min(end, idx)
    return tail[:end].strip()


def _fuzzy_from_hint(hint: str, values: list[str], threshold: float = 0.72) -> str | None:
    hint = normalize_text(hint)
    if not hint or not values:
        return None
    scored = []
    for value in values:
        target = normalize_text(value)
        if not target:
            continue
        ratio = SequenceMatcher(None, hint, target).ratio()
        # Also compare against the distinctive token sequence.
        token_target = " ".join(sorted(_meaningful_tokens(value)))
        if token_target:
            ratio = max(ratio, SequenceMatcher(None, hint, token_target).ratio())
        scored.append((ratio, value))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < threshold:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.06:
        return None
    return scored[0][1]


def _parse_price(text: str) -> dict:
    norm = normalize_text(text).replace(",", ".")
    field = "Prezzo Unitario" if "prezzo unitario" in norm else "Prezzo Confezione"

    # Range, e.g. "tra 10 e 20 euro".
    m = re.search(r"(?:tra|da)\s+(\d+(?:\.\d+)?)\s+(?:e|a)\s+(\d+(?:\.\d+)?)\s*(?:euro|€)?", norm)
    if m and ("prezz" in norm or "cost" in norm):
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        return {"field": field, "min": lo, "max": hi}

    # Maximum.
    m = re.search(
        r"(?:sotto|inferiore\s+a|meno\s+di|massimo|max|non\s+oltre|fino\s+a)\s*(?:€\s*)?(\d+(?:\.\d+)?)",
        norm,
    )
    if m and ("prezz" in norm or "cost" in norm or "euro" in norm):
        return {"field": field, "max": float(m.group(1))}

    # Minimum.
    m = re.search(
        r"(?:sopra|superiore\s+a|piu\s+di|minimo|min|almeno)\s*(?:€\s*)?(\d+(?:\.\d+)?)",
        norm,
    )
    if m and ("prezz" in norm or "cost" in norm or "euro" in norm):
        return {"field": field, "min": float(m.group(1))}

    return {}


def _interpret_storage(text: str) -> str | None:
    norm = normalize_text(text)
    if any(term in norm for term in ("frigo", "frigorif", "refrigerat")):
        return "FRIGO_Frigo"
    if any(term in norm for term in ("freezer", "congelatore", "congelat", "-20")):
        return "FREEZER_Freezer"
    if any(term in norm for term in ("stupef", "stupefacenti")) and not any(term in norm for term in ("s7", "s8")):
        return "STUPEF_Stupefacenti"
    if any(term in norm for term in ("standard", "temperatura ambiente")):
        return "STD_Standard"
    return None


def interpret_request(text: str, catalogue: pd.DataFrame) -> dict:
    """Interpret Italian natural language into a safe, structured catalogue query.

    The result contains only whitelisted filters. No SQL is produced or accepted.
    """
    original = text.strip()
    norm = normalize_text(original)

    action = "search"
    if any(term in norm for term in ("storico prezzi", "storico del prezzo", "andamento prezzi", "andamento del prezzo")):
        action = "history"
    elif re.search(r"\b(quanti|quante|conteggio|conta|numero di)\b", norm):
        action = "count"
    elif any(term in norm for term in ("scarica", "scaricami", "esporta", "excel", "xlsx", "download")):
        action = "export"

    filters: dict[str, Any] = {}
    unresolved: list[str] = []
    recognised: list[str] = []

    # AIC: accept 8/9 digits and normalize to 9 digits.
    aic_match = re.search(r"(?:aic\s*[:#-]?\s*)?(?<!\d)(\d{8,9})(?!\d)", norm)
    if "aic" in norm and not aic_match:
        unresolved.append("AIC")
    elif aic_match:
        aic = re.sub(r"\D", "", aic_match.group(1)).zfill(9)
        filters["AIC"] = aic
        recognised.append(f"AIC = {aic}")

    # Supplier.
    suppliers = _unique_values(catalogue, "Fornitore")
    supplier = _match_entity(original, suppliers, allow_token_match=True)

    # Support variants such as:
    #   "listino Pfizer", "listino: Pfizer", "listino fornitore Pfizer",
    #   "listino di Pfizer", "fornitore: Pfizer".
    supplier_markers = ("fornitore", "listino fornitore", "listino di", "listino del")
    explicit_full_catalogue = bool(
        re.search(
            r"\b(?:tutto|intero|completo)\s+(?:il\s+)?(?:listino|catalogo)\b|"
            r"\b(?:listino|catalogo)\s+(?:completo|intero)\b|"
            r"\btutti\s+i\s+prodotti\b",
            norm,
        )
    )

    asks_supplier = any(marker in norm for marker in supplier_markers)
    listino_hint = ""
    if not asks_supplier and "listino" in norm and not explicit_full_catalogue:
        # Bare "listino X" is interpreted as a supplier-filtered request when X is present.
        m = re.search(r"\blistino\s*[:=-]?\s+(.+)$", norm)
        if m:
            listino_hint = m.group(1).strip()
            # Remove trailing generic/export words that do not belong to the supplier name.
            listino_hint = re.sub(r"\s+(?:in\s+excel|excel|xlsx|per\s+favore)$", "", listino_hint).strip()
            if listino_hint and listino_hint not in {"prodotti", "farmaci"}:
                asks_supplier = True

    if supplier:
        filters["Fornitore"] = supplier
        recognised.append(f"Fornitore = {supplier}")
    elif asks_supplier:
        hint = _capture_after(original, "fornitore")
        if not hint:
            hint = _capture_after(original, "listino fornitore")
        if not hint:
            hint = _capture_after(original, "listino di") or _capture_after(original, "listino del")
        if not hint:
            hint = listino_hint
        fuzzy = _fuzzy_from_hint(hint, suppliers, 0.66)
        if fuzzy:
            filters["Fornitore"] = fuzzy
            recognised.append(f"Fornitore = {fuzzy}")
        else:
            unresolved.append("Fornitore")

    # Active ingredient.
    actives = _unique_values(catalogue, "Principio Attivo")
    active = _match_entity(original, actives, allow_token_match=False)
    asks_active = "principio attivo" in norm or "principio" in norm
    if active:
        filters["Principio Attivo"] = active
        recognised.append(f"Principio Attivo = {active}")
    elif asks_active:
        hint = _capture_after(original, "principio attivo") or _capture_after(original, "principio")
        fuzzy = _fuzzy_from_hint(hint, actives, 0.70)
        if fuzzy:
            filters["Principio Attivo"] = fuzzy
            recognised.append(f"Principio Attivo = {fuzzy}")
        else:
            unresolved.append("Principio Attivo")

    # ATC7 / ATC9: resolve against values actually present.
    atc7_values = _unique_values(catalogue, "ATC7")
    atc9_values = _unique_values(catalogue, "ATC9")
    atc7 = _match_entity(original, atc7_values, allow_token_match=False)
    atc9 = _match_entity(original, atc9_values, allow_token_match=False)
    if atc9:
        filters["ATC9"] = atc9
        recognised.append(f"ATC9 = {atc9}")
    elif atc7:
        filters["ATC7"] = atc7
        recognised.append(f"ATC7 = {atc7}")
    elif "atc" in norm:
        code = re.search(r"\batc(?:7|9)?\s*[:#-]?\s*([a-z0-9]{4,12})\b", norm)
        if code:
            candidate = code.group(1).upper()
            # Exact code only. If not in catalogue, return zero results rather than silently widening.
            column = "ATC9" if "atc9" in norm else "ATC7"
            filters[column] = candidate
            recognised.append(f"{column} = {candidate}")
        else:
            unresolved.append("ATC")

    # Storage group via natural synonyms or exact group value.
    storage = _interpret_storage(original)
    if storage:
        filters["Gruppo di Stivaggio"] = storage
        recognised.append(f"Gruppo di Stivaggio = {storage}")

    # Narcotic / anti-doping classification.
    narcotics = _unique_values(catalogue, "Stupefacente")
    narcotic = _match_entity(original, narcotics, allow_token_match=False)
    if narcotic and "stupef" not in norm:  # "stupefacenti" alone means storage/category broad search.
        filters["Stupefacente"] = narcotic
        recognised.append(f"Stupefacente = {narcotic}")
    elif "s7" in norm:
        filters["Stupefacente"] = "S7 NARCOTICI"
        recognised.append("Stupefacente = S7 NARCOTICI")
    elif "s8" in norm:
        filters["Stupefacente"] = "S8 CANNABINOIDI"
        recognised.append("Stupefacente = S8 CANNABINOIDI")

    # IVA.
    iva_match = re.search(r"\biva\s*(?:al|del|=|:)?\s*(0|4|10|22)\s*%?", norm)
    if iva_match:
        iva_pct = int(iva_match.group(1))
        filters["IVA"] = iva_pct / 100.0
        recognised.append(f"IVA = {iva_pct}%")
    elif "iva" in norm:
        unresolved.append("IVA")

    # Price range.
    price = _parse_price(original)
    if price:
        filters["_price"] = price
        description = price["field"]
        if "min" in price and "max" in price:
            recognised.append(f"{description} tra €{price['min']:g} e €{price['max']:g}")
        elif "max" in price:
            recognised.append(f"{description} ≤ €{price['max']:g}")
        else:
            recognised.append(f"{description} ≥ €{price['min']:g}")

    # Safe deterministic ranking fallback for common numeric questions.
    sort_spec = None
    limit = None
    ranking_field = None
    numeric_aliases = [
        ("prezzo unitario", "Prezzo Unitario"),
        ("prezzo confezione", "Prezzo Confezione"),
        ("prezzo a confezione", "Prezzo Confezione"),
        ("minimo movimentabile", "Minimo Movimentabile"),
        ("upc", "UPC"),
        ("iva", "IVA"),
    ]
    for alias, canonical in numeric_aliases:
        if alias in norm:
            ranking_field = canonical
            break
    if ranking_field:
        desc_terms = ("piu alto", "piu alta", "massimo", "massima", "maggiore", "piu grande")
        asc_terms = ("piu basso", "piu bassa", "minimo", "minima", "minore", "piu piccolo")
        direction = None
        if any(term in norm for term in desc_terms):
            direction = "desc"
        elif any(term in norm for term in asc_terms):
            direction = "asc"
        if direction:
            sort_spec = {"field": ranking_field, "direction": direction}
            m_top = re.search(r"\b(?:i|le|primi|prime|top)\s*(\d{1,4})\b", norm)
            limit = int(m_top.group(1)) if m_top else 1
            limit = max(1, min(limit, 5000))
            recognised.append(
                f"Ordina {ranking_field} {'decrescente' if direction == 'desc' else 'crescente'} · Limite = {limit}"
            )

    # Optional commercial code lookup.
    if "codice fornitore" in norm:
        match = re.search(r"codice fornitore\s*[:#-]?\s*([a-z0-9._/\-]+)", norm)
        if match:
            filters["Codice Fornitore"] = match.group(1)
            recognised.append(f"Codice Fornitore = {match.group(1)}")
        else:
            unresolved.append("Codice Fornitore")

    # Fail-safe export rule. An export with no recognised filters is allowed only when
    # the user explicitly asks for the whole catalogue. This prevents an unknown
    # supplier/ingredient from accidentally becoming a full-list export.
    if action == "export" and not filters and not sort_spec and not explicit_full_catalogue:
        unresolved.append("Filtro di estrazione")

    # If the user explicitly references a field and it could not be resolved, do not run a broad query.
    unresolved = list(dict.fromkeys(unresolved))
    blocking_ambiguity = bool(unresolved)

    return {
        "text": original,
        "action": action,
        "filters": filters,
        "recognised": recognised,
        "unresolved": unresolved,
        "blocking_ambiguity": blocking_ambiguity,
        "explicit_full_catalogue": explicit_full_catalogue,
        "available_suppliers": suppliers,
        "sort": sort_spec,
        "limit": limit,
    }


def _apply_generic_filters(df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
    out = df.copy()
    for item in filters or []:
        field = item.get("field")
        operator = item.get("operator", "eq")
        if not field or field not in out.columns:
            continue

        value = item.get("value")
        value2 = item.get("value2")

        numeric_fields = {"Prezzo Unitario", "Prezzo Confezione", "UPC", "Minimo Movimentabile", "IVA"}
        if field not in numeric_fields and operator in {"eq", "contains"}:
            series = out[field].fillna("").astype(str).map(normalize_text)
            target = normalize_text(value)
            if operator == "eq":
                out = out[series == target]
            else:
                out = out[series.str.contains(re.escape(target), regex=True, na=False)]
            continue

        numeric = pd.to_numeric(out[field], errors="coerce")
        try:
            v1 = float(value)
        except Exception:
            continue

        if operator == "eq":
            out = out[(numeric - v1).abs() < 1e-9]
        elif operator == "gt":
            out = out[numeric > v1]
        elif operator == "gte":
            out = out[numeric >= v1]
        elif operator == "lt":
            out = out[numeric < v1]
        elif operator == "lte":
            out = out[numeric <= v1]
        elif operator == "between":
            try:
                v2 = float(value2)
            except Exception:
                continue
            lo, hi = sorted((v1, v2))
            out = out[(numeric >= lo) & (numeric <= hi)]

    return out


def apply_request(catalogue: pd.DataFrame, request: dict) -> pd.DataFrame:
    df = catalogue.copy()

    # R6 generic query model used by the local LLM.
    if request.get("query_version") == 2:
        df = _apply_generic_filters(df, request.get("generic_filters", []))

        sort_spec = request.get("sort") or {}
        sort_field = sort_spec.get("field")
        if sort_field and sort_field in df.columns:
            ascending = sort_spec.get("direction", "asc") != "desc"
            if sort_field in {"Prezzo Unitario", "Prezzo Confezione", "UPC", "Minimo Movimentabile", "IVA"}:
                # Stable numeric sort while keeping original display values.
                order = pd.to_numeric(df[sort_field], errors="coerce")
                df = df.assign(__sort_value=order).sort_values(
                    "__sort_value", ascending=ascending, na_position="last", kind="mergesort"
                ).drop(columns="__sort_value")
            else:
                df = df.sort_values(sort_field, ascending=ascending, na_position="last", kind="mergesort")

        limit = request.get("limit")
        if limit:
            df = df.head(int(limit))
        return df

    # Legacy deterministic parser retained as a safe fallback.
    filters = request.get("filters", {})

    for field in ("AIC", "Fornitore", "Principio Attivo", "ATC7", "ATC9", "Stupefacente"):
        if field not in filters or field not in df.columns:
            continue
        target = normalize_text(filters[field])
        df = df[df[field].fillna("").astype(str).map(normalize_text) == target]

    if "Codice Fornitore" in filters and "Codice Fornitore" in df.columns:
        target = normalize_text(filters["Codice Fornitore"])
        df = df[df["Codice Fornitore"].fillna("").astype(str).map(normalize_text) == target]

    if "Gruppo di Stivaggio" in filters and "Gruppo di Stivaggio" in df.columns:
        target = normalize_text(filters["Gruppo di Stivaggio"])
        df = df[df["Gruppo di Stivaggio"].fillna("").astype(str).map(normalize_text) == target]

    if "IVA" in filters and "IVA" in df.columns:
        iva = pd.to_numeric(df["IVA"], errors="coerce")
        df = df[(iva - float(filters["IVA"])).abs() < 1e-9]

    price = filters.get("_price")
    if price and price.get("field") in df.columns:
        values = pd.to_numeric(df[price["field"]], errors="coerce")
        if "min" in price:
            df = df[values >= float(price["min"])]
            values = pd.to_numeric(df[price["field"]], errors="coerce")
        if "max" in price:
            df = df[values <= float(price["max"])]

    return df


def aggregate_request(catalogue: pd.DataFrame, request: dict) -> dict | None:
    """Compute a safe aggregate after applying the grounded filters."""
    spec = request.get("aggregation") or {}
    function = spec.get("function")
    field = spec.get("field")
    if not function or not field or field not in catalogue.columns:
        return None

    # Apply only filters, not sort/limit, so aggregates use the entire requested scope.
    scoped = catalogue.copy()
    if request.get("query_version") == 2:
        scoped = _apply_generic_filters(scoped, request.get("generic_filters", []))
    else:
        scoped = apply_request(scoped, {**request, "limit": None, "sort": None})

    if scoped.empty:
        return {"function": function, "field": field, "value": None, "rows": scoped}

    if function == "count_distinct":
        values = scoped[field].dropna().astype(str).str.strip()
        values = values[values.ne("")]
        return {"function": function, "field": field, "value": int(values.nunique()), "rows": scoped}

    numeric = pd.to_numeric(scoped[field], errors="coerce").dropna()
    if numeric.empty:
        return {"function": function, "field": field, "value": None, "rows": scoped}

    if function == "max":
        value = float(numeric.max())
    elif function == "min":
        value = float(numeric.min())
    elif function == "avg":
        value = float(numeric.mean())
    elif function == "sum":
        value = float(numeric.sum())
    else:
        return None
    return {"function": function, "field": field, "value": value, "rows": scoped}


def interpretation_label(request: dict) -> str:
    labels = request.get("recognised", [])
    if not labels:
        return "Nessun filtro: intero listino pubblicato"
    return " · ".join(labels)


def response_text(request: dict, results: pd.DataFrame) -> str:
    if request.get("blocking_ambiguity"):
        fields = ", ".join(request.get("unresolved", []))
        message = f"Non riesco a identificare con certezza: **{fields}**. Nessuna estrazione è stata eseguita."
        if "Fornitore" in request.get("unresolved", []):
            suppliers = request.get("available_suppliers", [])
            if suppliers:
                preview = ", ".join(suppliers[:8])
                if len(suppliers) > 8:
                    preview += ", …"
                message += f"\n\nFornitori disponibili nel listino: **{preview}**."
        if any(x in request.get("unresolved", []) for x in ("Filtro di estrazione", "Filtro o criterio di estrazione")):
            message += "\n\nPer esportare tutto il catalogo scrivi esplicitamente **«scarica tutto il listino»**."
        return message

    n = len(results)
    action = request.get("action")
    if n == 0:
        return "Non ho trovato prodotti pubblicati che rispettino i filtri interpretati."
    if action == "count":
        products = results["product_id"].nunique() if "product_id" in results.columns else n
        suppliers = results["Fornitore"].nunique() if "Fornitore" in results.columns else 0
        return f"Ho trovato **{n} righe di listino**, relative a **{products} prodotti** e **{suppliers} fornitori**."
    if action == "history":
        return f"Ho trovato **{n} offerte** compatibili. Sotto trovi lo storico prezzi disponibile."
    if action == "aggregate":
        return f"Ho analizzato **{n} righe** compatibili con la richiesta."
    if action == "export":
        return f"Ho trovato **{n} righe**. Ho preparato l'estrazione Excel sui criteri interpretati."
    return f"Ho trovato **{n} righe** nel listino pubblicato."


def export_filename(request: dict) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d")
    parts = ["Listino"]
    if request.get("query_version") == 2:
        for item in request.get("generic_filters", []):
            if item.get("operator") != "eq":
                continue
            if item.get("field") in {"Fornitore", "Principio Attivo", "AIC", "ATC7", "Nome Commerciale"}:
                safe = re.sub(r"[^A-Za-z0-9]+", "_", str(item.get("value", ""))).strip("_")[:35]
                if safe:
                    parts.append(safe)
        sort_spec = request.get("sort") or {}
        if sort_spec.get("field") and request.get("limit"):
            safe = re.sub(r"[^A-Za-z0-9]+", "_", sort_spec["field"]).strip("_")[:25]
            parts.append(("Top" if sort_spec.get("direction") == "desc" else "Bottom") + f"_{request['limit']}_{safe}")
    else:
        filters = request.get("filters", {})
        for key in ("Fornitore", "Principio Attivo", "AIC", "ATC7"):
            if filters.get(key):
                safe = re.sub(r"[^A-Za-z0-9]+", "_", str(filters[key])).strip("_")[:35]
                if safe:
                    parts.append(safe)
    return "_".join(parts) + f"_{stamp}.xlsx"

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.assistant import aggregate_request, apply_request, interpret_request
from modules.llm_assistant import ground_llm_payload


def catalogue() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "product_id": 1, "offer_id": 11, "Fornitore": "ANGELINI PHARMA S.P.A.", "AIC": "012745055",
            "Codice Fornitore": "A001", "Nome Commerciale": "TACHIPIRINA", "Principio Attivo": "PARACETAMOLO",
            "Materiale Pericoloso": "N", "Stupefacente": "No", "ATC7": "N02BE01", "ATC9": "",
            "Fala / Lasa": "N", "Gruppo di Stivaggio": "STD_Standard", "Temperatura di Stivaggio": "da + 15 a 25°C",
            "Prezzo Unitario": 0.08, "Prezzo Confezione": 0.80, "UPC": 10, "Minimo Movimentabile": 1,
            "IVA": 0.10, "Note": "prodotto standard", "Ultimo aggiornamento": "2026-09-20T10:00:00", "Batch corrente": "PUB1"
        },
        {
            "product_id": 2, "offer_id": 12, "Fornitore": "JANSSEN-CILAG S.P.A.", "AIC": "043693050",
            "Codice Fornitore": "J900", "Nome Commerciale": "IMBRUVICA", "Principio Attivo": "IBRUTINIB",
            "Materiale Pericoloso": "Y", "Stupefacente": "No", "ATC7": "L01EL01", "ATC9": "",
            "Fala / Lasa": "Y", "Gruppo di Stivaggio": "FRIGO_Frigo", "Temperatura di Stivaggio": "da + 2 a 8°C",
            "Prezzo Unitario": 108.04, "Prezzo Confezione": 9723.60, "UPC": 90, "Minimo Movimentabile": 3,
            "IVA": 0.10, "Note": "URGENTE controllo dedicato", "Ultimo aggiornamento": "2026-09-21T09:00:00", "Batch corrente": "PUB2"
        },
        {
            "product_id": 3, "offer_id": 13, "Fornitore": "THEA FARMA S.P.A.", "AIC": "009324029",
            "Codice Fornitore": "T010", "Nome Commerciale": "NOVESINA", "Principio Attivo": "OSSIBUPROCAINA",
            "Materiale Pericoloso": "N", "Stupefacente": "S7 NARCOTICI", "ATC7": "S01HA02", "ATC9": "",
            "Fala / Lasa": "N", "Gruppo di Stivaggio": "STUPEF_Stupefacenti", "Temperatura di Stivaggio": "da + 15 a 25°C",
            "Prezzo Unitario": 12.50, "Prezzo Confezione": 62.50, "UPC": 5, "Minimo Movimentabile": 1,
            "IVA": 0.22, "Note": "stupefacente", "Ultimo aggiornamento": "2026-09-19T12:00:00", "Batch corrente": "PUB3"
        },
    ])


def grounded(payload: dict, text: str) -> dict:
    return ground_llm_payload(payload, catalogue(), text, model="test-model")


def test_highest_unit_price_export():
    req = grounded({
        "action": "export", "filters": [],
        "sort": {"field": "Prezzo Unitario", "direction": "desc"}, "limit": 1,
        "aggregation": {"function": None, "field": None}, "needs_clarification": False,
    }, "estrai il prodotto con il prezzo unitario più alto")
    assert not req["blocking_ambiguity"], req
    result = apply_request(catalogue(), req)
    assert len(result) == 1
    assert result.iloc[0]["Nome Commerciale"] == "IMBRUVICA"



def test_lowest_unit_price_with_empty_llm_filter_is_not_blocked():
    # Regression: some LLMs emit the ranking field as an empty eq filter.
    req = grounded({
        "action": "export",
        "filters": [{"field": "Prezzo Unitario", "operator": "eq", "value": None, "value2": None}],
        "sort": {"field": None, "direction": "asc"},
        "limit": None,
        "aggregation": {"function": None, "field": None},
        "needs_clarification": False,
    }, "estrai il prodotto con prezzo unitario più basso")
    assert not req["blocking_ambiguity"], req
    assert req["sort"] == {"field": "Prezzo Unitario", "direction": "asc"}
    assert req["limit"] == 1
    result = apply_request(catalogue(), req)
    assert len(result) == 1
    assert result.iloc[0]["Nome Commerciale"] == "TACHIPIRINA"

def test_unknown_supplier_is_blocked():
    req = grounded({
        "action": "export",
        "filters": [{"field": "Fornitore", "operator": "eq", "value": "Pfizer", "value2": None}],
        "sort": {"field": None, "direction": "asc"}, "limit": None,
        "aggregation": {"function": None, "field": None}, "needs_clarification": False,
    }, "scarica listino Pfizer")
    assert req["blocking_ambiguity"]
    assert "Fornitore" in req["unresolved"]


def test_all_field_style_filters():
    req = grounded({
        "action": "search",
        "filters": [
            {"field": "Materiale Pericoloso", "operator": "eq", "value": "Y", "value2": None},
            {"field": "Fala / Lasa", "operator": "eq", "value": "Y", "value2": None},
            {"field": "UPC", "operator": "gt", "value": 20, "value2": None},
            {"field": "Temperatura di Stivaggio", "operator": "eq", "value": "da + 2 a 8°C", "value2": None},
        ],
        "sort": {"field": None, "direction": "asc"}, "limit": None,
        "aggregation": {"function": None, "field": None}, "needs_clarification": False,
    }, "prodotti pericolosi fala lasa con UPC sopra 20 a temperatura da +2 a 8")
    assert not req["blocking_ambiguity"], req
    result = apply_request(catalogue(), req)
    assert result["AIC"].tolist() == ["043693050"]


def test_notes_contains():
    req = grounded({
        "action": "search",
        "filters": [{"field": "Note", "operator": "contains", "value": "urgente", "value2": None}],
        "sort": {"field": None, "direction": "asc"}, "limit": None,
        "aggregation": {"function": None, "field": None}, "needs_clarification": False,
    }, "note contengono urgente")
    assert not req["blocking_ambiguity"], req
    result = apply_request(catalogue(), req)
    assert result["Nome Commerciale"].tolist() == ["IMBRUVICA"]


def test_average_pack_price():
    req = grounded({
        "action": "aggregate", "filters": [],
        "sort": {"field": None, "direction": "asc"}, "limit": None,
        "aggregation": {"function": "avg", "field": "Prezzo Confezione"}, "needs_clarification": False,
    }, "qual è il prezzo confezione medio")
    assert not req["blocking_ambiguity"], req
    agg = aggregate_request(catalogue(), req)
    expected = catalogue()["Prezzo Confezione"].mean()
    assert abs(agg["value"] - expected) < 1e-9


def test_deterministic_ranking_fallback():
    req = interpret_request("estrai il prodotto con il prezzo unitario più alto", catalogue())
    assert not req["blocking_ambiguity"], req
    assert req["sort"] == {"field": "Prezzo Unitario", "direction": "desc"}
    assert req["limit"] == 1


def test_count_distinct_active_ingredients():
    req = grounded({
        "action": "aggregate", "filters": [],
        "sort": {"field": None, "direction": "asc"}, "limit": None,
        "aggregation": {"function": "count_distinct", "field": "Principio Attivo"}, "needs_clarification": False,
    }, "quanti principi attivi distinti ci sono")
    assert not req["blocking_ambiguity"], req
    assert req["action"] == "aggregate"
    agg = aggregate_request(catalogue(), req)
    assert agg["value"] == 3


if __name__ == "__main__":
    tests = [
        test_highest_unit_price_export,
        test_lowest_unit_price_with_empty_llm_filter_is_not_blocked,
        test_unknown_supplier_is_blocked,
        test_all_field_style_filters,
        test_notes_contains,
        test_average_pack_price,
        test_count_distinct_active_ingredients,
        test_deterministic_ranking_fallback,
    ]
    for test in tests:
        test()
        print(f"OK  {test.__name__}")
    print("Tutti i test R6 sono passati.")

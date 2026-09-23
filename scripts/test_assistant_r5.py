from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.assistant import apply_request, interpret_request
from modules.llm_assistant import ground_llm_payload


CATALOGUE = pd.DataFrame([
    {
        "product_id": 1,
        "offer_id": 10,
        "AIC": "012745055",
        "Nome Commerciale": "TACHIPIRINA",
        "Principio Attivo": "PARACETAMOLO",
        "ATC7": "N02BE01",
        "ATC9": "",
        "Fornitore": "ANGELINI PHARMA S.P.A.",
        "Codice Fornitore": "ANG001",
        "Gruppo di Stivaggio": "STD_Standard",
        "Stupefacente": "No",
        "IVA": 0.10,
        "Prezzo Unitario": 0.08,
        "Prezzo Confezione": 0.80,
        "UPC": 10,
    },
    {
        "product_id": 2,
        "offer_id": 20,
        "AIC": "043693050",
        "Nome Commerciale": "IMBRUVICA",
        "Principio Attivo": "IBRUTINIB",
        "ATC7": "L01EL01",
        "ATC9": "",
        "Fornitore": "JANSSEN-CILAG S.P.A.",
        "Codice Fornitore": "JAN001",
        "Gruppo di Stivaggio": "STD_Standard",
        "Stupefacente": "No",
        "IVA": 0.10,
        "Prezzo Unitario": 10.0,
        "Prezzo Confezione": 30.0,
        "UPC": 3,
    },
])


def check_unknown_supplier_is_blocked():
    payload = {
        "action": "export",
        "filters": {"supplier": "Pfizer"},
        "needs_clarification": False,
    }
    request = ground_llm_payload(payload, CATALOGUE, "scaricami listino Pfizer", model="fake")
    assert request["blocking_ambiguity"] is True
    assert "Fornitore" in request["unresolved"]
    assert not request["filters"].get("Fornitore")


def check_known_supplier_resolves():
    payload = {
        "action": "export",
        "filters": {"supplier": "Angelini"},
        "needs_clarification": False,
    }
    request = ground_llm_payload(payload, CATALOGUE, "scaricami listino Angelini", model="fake")
    assert request["blocking_ambiguity"] is False
    assert request["filters"]["Fornitore"] == "ANGELINI PHARMA S.P.A."
    result = apply_request(CATALOGUE, request)
    assert len(result) == 1


def check_empty_export_is_blocked():
    payload = {"action": "export", "filters": {}, "needs_clarification": False}
    request = ground_llm_payload(payload, CATALOGUE, "scaricami qualcosa", model="fake")
    assert request["blocking_ambiguity"] is True
    assert "Filtro di estrazione" in request["unresolved"]


def check_explicit_full_export_is_allowed():
    payload = {"action": "export", "filters": {}, "needs_clarification": False}
    request = ground_llm_payload(payload, CATALOGUE, "scarica tutto il listino", model="fake")
    assert request["blocking_ambiguity"] is False
    assert request["explicit_full_catalogue"] is True
    result = apply_request(CATALOGUE, request)
    assert len(result) == 2


def check_r41_fallback_is_safe():
    request = interpret_request("scarica listino Pfizer", CATALOGUE)
    assert request["blocking_ambiguity"] is True
    assert "Fornitore" in request["unresolved"]


if __name__ == "__main__":
    checks = [
        check_unknown_supplier_is_blocked,
        check_known_supplier_resolves,
        check_empty_export_is_blocked,
        check_explicit_full_export_is_allowed,
        check_r41_fallback_is_safe,
    ]
    for check in checks:
        check()
        print(f"OK - {check.__name__}")
    print("Tutti i test R5 sono passati.")

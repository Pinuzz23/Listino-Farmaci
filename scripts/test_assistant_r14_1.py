from __future__ import annotations

import pandas as pd

from modules.assistant import apply_request, interpret_request
from modules.llm_assistant import ground_llm_payload


def _catalogue() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "product_id": 1,
                "Fornitore": "ALFA",
                "AIC": "000000001",
                "Nome Commerciale": "Critico",
                "Principio Attivo": "A",
                "Stato Validità": "CRITICO",
                "Giorni Residui": 20,
                "Vita Consumata %": 80.0,
            },
            {
                "product_id": 2,
                "Fornitore": "BETA",
                "AIC": "000000002",
                "Nome Commerciale": "Regolare",
                "Principio Attivo": "B",
                "Stato Validità": "REGOLARE",
                "Giorni Residui": 120,
                "Vita Consumata %": 20.0,
            },
        ]
    )


def test_deterministic_validity_queries():
    df = _catalogue()

    request = interpret_request("mostrami i farmaci critici", df)
    result = apply_request(df, request)
    assert result["AIC"].tolist() == ["000000001"]

    request = interpret_request(
        "quali farmaci scadono entro 30 giorni?",
        df,
    )
    result = apply_request(df, request)
    assert result["AIC"].tolist() == ["000000001"]


def test_llm_grounding_validity_queries():
    df = _catalogue()
    payload = {
        "action": "search",
        "filters": [
            {
                "field": "Giorni Residui",
                "operator": "between",
                "value": 0,
                "value2": 60,
            }
        ],
        "sort": {"field": "Giorni Residui", "direction": "asc"},
        "limit": None,
        "aggregation": {"function": None, "field": None},
        "needs_clarification": False,
        "clarification": "",
    }
    request = ground_llm_payload(
        payload,
        df,
        "quali farmaci scadono entro 60 giorni?",
    )
    result = apply_request(df, request)
    assert result["AIC"].tolist() == ["000000001"]

    payload["filters"] = [
        {
            "field": "Stato Validità",
            "operator": "eq",
            "value": "CRITICO",
            "value2": None,
        }
    ]
    payload["sort"] = {"field": "Giorni Residui", "direction": "asc"}
    request = ground_llm_payload(
        payload,
        df,
        "mostrami i farmaci critici",
    )
    result = apply_request(df, request)
    assert result["AIC"].tolist() == ["000000001"]


if __name__ == "__main__":
    test_deterministic_validity_queries()
    test_llm_grounding_validity_queries()
    print("R14.1 assistant tests: OK")

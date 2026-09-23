from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.validation_agent import plan_validation_request, run_validation_copilot
from modules.validation_tools import build_validation_registry


CONTEXT = {
    "records": [
        {
            "Fornitore": "TEST S.P.A.",
            "AIC": "012345678",
            "Nome Commerciale": "FARMACO TEST",
            "Prezzo Unitario": 10.0,
            "Prezzo Confezione": 90.0,
            "UPC": 10,
            "Gruppo di Stivaggio": "STD_Standard",
            "Temperatura di Stivaggio": "da + 15 a 25°C",
        },
        {
            "Fornitore": "TEST S.P.A.",
            "AIC": "",
            "Nome Commerciale": "ALTRO FARMACO",
            "Prezzo Unitario": 5.0,
            "Prezzo Confezione": 5.0,
            "UPC": 1,
        },
    ],
    "source_rows": [3, 4],
    "aifa_candidates": [],
    "result": {
        "is_valid": False,
        "blocking_count": 2,
        "warning_count": 1,
        "info_count": 0,
        "issues": [
            {
                "Riga Excel": 3,
                "Campo": "Prezzo Confezione",
                "Valore ricevuto": "90",
                "Codice Errore": "COERENZA_PREZZO_CONFEZIONE",
                "Livello": "BLOCCANTE",
                "Descrizione": "Prezzo confezione non coerente.",
                "Valori ammessi / Regola": "Prezzo Unitario × UPC = 100",
            },
            {
                "Riga Excel": 4,
                "Campo": "Principio Attivo",
                "Valore ricevuto": "",
                "Codice Errore": "CAMPO_OBBLIGATORIO",
                "Livello": "BLOCCANTE",
                "Descrizione": "Campo obbligatorio.",
                "Valori ammessi / Regola": "Valorizzare il campo.",
            },
            {
                "Riga Excel": 3,
                "Campo": "UPC",
                "Valore ricevuto": "10",
                "Codice Errore": "UPC_DIVERSO_DA_AIFA",
                "Livello": "WARNING",
                "Descrizione": "UPC diverso da AIFA.",
                "Valori ammessi / Regola": "Verificare.",
            },
        ],
        "summary": {
            "rows_count": 2,
            "aifa_exact_matches": 1,
            "aifa_missing_matches": 1,
            "aifa_enrichment_count": 0,
            "aifa_upc_warning": 1,
        },
        "transformations": [],
        "aifa_enrichments": [],
        "aifa_upc_checks": [{"Riga Excel": 3, "AIC": "012345678", "Esito": "DA VERIFICARE"}],
        "supplier_proposals": [],
        "operator_changes": [],
    },
}


def main():
    registry = build_validation_registry()
    assert all(item["access"] == "read" for item in registry.manifest(access={"read"}))
    assert any(item["access"] == "generate" for item in registry.manifest())
    assert any(item["access"] == "write" for item in registry.manifest())
    assert "validation_summary" in registry.names()
    assert "validation_correction_plan" in registry.names()

    summary = registry.execute("validation_summary", CONTEXT, {}, allowed_access={"read"})
    assert summary["blocking"] == 2
    assert summary["warnings"] == 1

    grouped = registry.execute(
        "validation_group_issues", CONTEXT, {"by": "code"}, allowed_access={"read"}
    )
    assert grouped["items"][0]["count"] >= 1

    row = registry.execute("validation_show_row", CONTEXT, {"row": 3}, allowed_access={"read"})
    assert row["found"] is True
    assert len(row["issues"]) == 2

    explanation = registry.execute(
        "validation_explain_issue",
        CONTEXT,
        {"code": "COERENZA_PREZZO_CONFEZIONE"},
        allowed_access={"read"},
    )
    assert explanation["count"] == 1
    assert "Prezzo" in explanation["meaning"]

    plan = plan_validation_request(
        "Perché questo file non passa la validazione?", registry, CONTEXT, model=""
    )
    names = [call["tool"] for call in plan["calls"]]
    assert "validation_summary" in names
    assert "validation_correction_plan" in names

    response = run_validation_copilot(
        "Da dove comincio a correggere?", registry, CONTEXT, model=""
    )
    assert "2 errori bloccanti" in response["answer"]
    assert "COERENZA_PREZZO_CONFEZIONE" in response["answer"] or "CAMPO_OBBLIGATORIO" in response["answer"]

    response_price = run_validation_copilot(
        "Mostrami i problemi di prezzo", registry, CONTEXT, model=""
    )
    assert response_price["tool_outputs"]

    print("OK - Tool Registry e Validation Copilot R8")


if __name__ == "__main__":
    main()

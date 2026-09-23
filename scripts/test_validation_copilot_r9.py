from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.change_set import simulate_change_set
from modules.excel_reader import WorkbookData
from modules.pipeline import process_workbook
from modules.schema_loader import load_schema
from modules.validation_agent import run_validation_copilot
from modules.validation_tools import build_validation_registry


def build_workbook(schema):
    fields = [c["name"] for c in schema["columns"]]
    record = {
        "Fornitore": "TEST S.P.A.",
        "AIC": "",
        "Codice Fornitore": "TEST-001",
        "Nome Commerciale": "FARMACO TEST",
        "Principio Attivo": "PRINCIPIO TEST",
        "Materiale Pericoloso": "N",
        "Stupefacente": "No",
        "ATC7": "",
        "ATC9": "",
        "Fala / Lasa": "N",
        "Gruppo di Stivaggio": "STD_Standard",  # errato per 2-8
        "Temperatura di Stivaggio": "da + 2 a 8°C",
        "Prezzo Unitario": 2.0,
        "Prezzo Confezione": 15.0,  # atteso 20
        "UPC": 10,
        "Minimo Movimentabile": 1,
        "IVA": 0.10,
        "Note": "",
    }
    return WorkbookData(
        filename="test_r9.xlsx",
        headers=fields,
        header_row=1,
        records=[record],
        source_rows=[2],
        sheet_names=[schema["sheet_name"]],
        extra_header_cells=[],
        leading_rows_present=0,
    )


def main():
    schema = load_schema(str(ROOT / "config" / "schema.json"))
    workbook = build_workbook(schema)
    bundle = process_workbook(workbook, schema, str(ROOT / "data" / "aifa"))
    result = bundle["result"]
    assert result["blocking_count"] == 2, result["issues"]

    registry = build_validation_registry()
    context = {
        "result": result,
        "records": bundle["data"].records,
        "source_rows": bundle["data"].source_rows,
        "schema": schema,
        "workbook": bundle["data"],
        "aifa_dir": str(ROOT / "data" / "aifa"),
        "aifa_candidates": bundle["aifa_candidates"],
    }

    # Il planner fallback deve generare, non applicare.
    response = run_validation_copilot(
        "Correggi automaticamente tutto quello che puoi in modo deterministico",
        registry,
        context,
        model="",
    )
    generated = [x["result"] for x in response["tool_outputs"] if x.get("ok") and isinstance(x.get("result"), dict) and x["result"].get("change_set_id")]
    assert generated, response
    cs = generated[0]
    assert cs["count"] == 2, cs
    fields = {x["Campo"] for x in cs["changes"]}
    assert fields == {"Prezzo Confezione", "Gruppo di Stivaggio"}, fields

    simulation = simulate_change_set(
        bundle["data"], cs, schema, str(ROOT / "data" / "aifa")
    )
    assert simulation["after"]["blocking"] == 0, simulation["after"]
    assert simulation["after"]["is_valid"] is True

    # I tool WRITE devono essere bloccati senza conferma.
    blocked = False
    try:
        registry.execute(
            "validation_apply_change_set",
            context,
            {"change_set": cs},
            allowed_access={"write"},
            confirmed=False,
        )
    except PermissionError:
        blocked = True
    assert blocked

    applied = registry.execute(
        "validation_apply_change_set",
        context,
        {"change_set": cs},
        allowed_access={"write"},
        confirmed=True,
    )
    assert applied["ok"] is True
    assert len(applied["applied"]) == 2

    print("OK - Validation Copilot R9: generate, simulate, confirm, apply")


if __name__ == "__main__":
    main()

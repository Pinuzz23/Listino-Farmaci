from __future__ import annotations

import os
import tempfile
from io import BytesIO
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.catalog_db import catalogue_dataframe, publish_records
from modules.excel_reader import WorkbookData, read_workbook
from modules.schema_loader import load_schema
from modules.validator import validate_workbook


class Upload(BytesIO):
    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


def sample_record():
    return {
        "Fornitore": "TEST SRL",
        "AIC": "012745055",
        "Codice Fornitore": "CF1",
        "Nome Commerciale": "PRODOTTO TEST",
        "Principio Attivo": "PRINCIPIO TEST",
        "Forma Farmaceutica": "Compressa",
        "Materiale Pericoloso": "N",
        "Stupefacente": "No",
        "ATC7": "N02BE01",
        "ATC9": "",
        "Fala / Lasa": "N",
        "Gruppo di Stivaggio": "STD_Standard",
        "Temperatura di Stivaggio": "inferiore a + 25°C",
        "Prezzo Unitario": 1.5,
        "Prezzo Confezione": 15.0,
        "UPC": 10,
        "Minimo Movimentabile": 1,
        "IVA": 0.1,
        "Note": "",
        "X": 12.5,
        "Y": 8.0,
        "Z": 4.2,
    }


def main():
    schema = load_schema(str(ROOT / "config" / "schema.json"))
    fields = [c["name"] for c in schema["columns"]]
    assert len(fields) == 22
    assert fields[5] == "Forma Farmaceutica"
    assert fields[-3:] == ["X", "Y", "Z"]

    template = ROOT / schema["template_reference"]["bundled_name"]
    workbook = read_workbook(Upload(template.read_bytes(), template.name), schema)
    assert workbook.header_row == 2
    assert workbook.headers == fields

    record = sample_record()
    data = WorkbookData(
        filename="master22_test.xlsx",
        headers=fields,
        header_row=1,
        records=[record],
        source_rows=[2],
        sheet_names=["Tracciato"],
        extra_header_cells=[],
        leading_rows_present=0,
    )
    result = validate_workbook(data, schema)
    assert result["is_valid"], result["issues"]

    invalid = sample_record()
    invalid["X"] = 0
    invalid_data = WorkbookData(
        filename="master22_invalid.xlsx",
        headers=fields,
        header_row=1,
        records=[invalid],
        source_rows=[2],
        sheet_names=["Tracciato"],
        extra_header_cells=[],
        leading_rows_present=0,
    )
    invalid_result = validate_workbook(invalid_data, schema)
    assert any(
        issue["Codice Errore"] == "VALORE_NON_POSITIVO"
        and issue["Campo"] == "X"
        for issue in invalid_result["issues"]
    )

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    try:
        publish_records([record], "master22_test.xlsx", db_path, schema["app_version"])
        catalogue = catalogue_dataframe(db_path)
        assert catalogue.loc[0, "Forma Farmaceutica"] == "Compressa"
        assert float(catalogue.loc[0, "X"]) == 12.5
        assert float(catalogue.loc[0, "Y"]) == 8.0
        assert float(catalogue.loc[0, "Z"]) == 4.2
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

    print("OK - Master definitivo 22 campi")


if __name__ == "__main__":
    main()

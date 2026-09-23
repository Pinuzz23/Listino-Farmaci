from dataclasses import dataclass
from io import BytesIO
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


@dataclass
class WorkbookData:
    filename: str
    headers: list[str]
    header_row: int
    records: list[dict[str, Any]]
    source_rows: list[int]
    sheet_names: list[str]
    extra_header_cells: list[tuple[str, Any]]
    leading_rows_present: int
    header_start_col: int = 1


def _blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def _norm_header(value):
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _detect_header_position(ws, expected_headers, search_rows):
    """Restituisce (riga, colonna iniziale) della sequenza completa di intestazioni.

    Il template di riferimento può avere una colonna tecnica iniziale (es. "Campo"),
    mentre i file operativi possono iniziare direttamente da colonna A. La ricerca
    è quindi indipendente dalla colonna di partenza.
    """
    expected = [_norm_header(value) for value in expected_headers]
    expected_count = len(expected)
    max_row = min(ws.max_row, search_rows)

    for row_idx in range(1, max_row + 1):
        values = [_norm_header(ws.cell(row_idx, c).value) for c in range(1, ws.max_column + 1)]
        if len(values) < expected_count:
            continue
        for start_zero in range(0, len(values) - expected_count + 1):
            if values[start_zero:start_zero + expected_count] == expected:
                return row_idx, start_zero + 1
    return None, None


def read_workbook(uploaded_file, schema) -> WorkbookData:
    raw = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    workbook = load_workbook(BytesIO(raw), data_only=True, read_only=False)

    sheet_name = schema["sheet_name"]
    if sheet_name not in workbook.sheetnames:
        raise ValueError(
            f"Foglio obbligatorio '{sheet_name}' non trovato. "
            f"Fogli presenti: {', '.join(workbook.sheetnames)}"
        )

    ws = workbook[sheet_name]
    expected_headers = [column["name"] for column in schema["columns"]]
    header_row, start_col = _detect_header_position(
        ws,
        expected_headers,
        int(schema.get("header_search_rows", 10)),
    )

    if header_row is None or start_col is None:
        raise ValueError(
            "Non è stata trovata la sequenza completa delle intestazioni del tracciato nelle prime "
            f"{schema.get('header_search_rows', 10)} righe. Verificare nomi e ordine delle colonne."
        )

    end_col = start_col + len(expected_headers) - 1
    headers = [
        _norm_header(ws.cell(header_row, col_idx).value)
        for col_idx in range(start_col, end_col + 1)
    ]

    # Qualsiasi cella valorizzata sulla riga header al di fuori del blocco dati viene
    # conservata come metadato. Nel template ufficiale, per esempio, A3 = "Campo".
    extra_header_cells = []
    for col_idx in range(1, ws.max_column + 1):
        if start_col <= col_idx <= end_col:
            continue
        value = ws.cell(header_row, col_idx).value
        technical_prefix = schema.get("template_reference", {}).get("technical_prefix_column")
        if col_idx < start_col and technical_prefix and _norm_header(value) == _norm_header(technical_prefix):
            continue
        if not _blank(value):
            extra_header_cells.append((get_column_letter(col_idx), value))

    records = []
    source_rows = []
    first_data_row = header_row + 1

    for row_idx in range(first_data_row, ws.max_row + 1):
        values = [ws.cell(row_idx, col_idx).value for col_idx in range(start_col, end_col + 1)]
        if all(_blank(value) for value in values):
            continue

        record = {expected_headers[i]: values[i] for i in range(len(expected_headers))}
        records.append(record)
        source_rows.append(row_idx)

    return WorkbookData(
        filename=getattr(uploaded_file, "name", "file.xlsx"),
        headers=headers,
        header_row=header_row,
        records=records,
        source_rows=source_rows,
        sheet_names=workbook.sheetnames,
        extra_header_cells=extra_header_cells,
        leading_rows_present=max(0, header_row - 1),
        header_start_col=start_col,
    )

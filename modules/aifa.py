from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import unicodedata
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from modules.excel_reader import WorkbookData
from modules.validator import is_semantic_blank, normalize_identifier, normalize_text, parse_decimal

AIFA_MAIN_URL = "https://drive.aifa.gov.it/farmaci/confezioni_fornitura.csv"
AIFA_PA_URL = "https://drive.aifa.gov.it/farmaci/PA_confezioni.csv"
AIFA_ATC_URL = "https://drive.aifa.gov.it/farmaci/atc.csv"
AIFA_DB_SCHEMA_VERSION = "2"


def _norm_key(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")


def _find_col(columns, aliases, required=False):
    by_norm = {_norm_key(c): c for c in columns}
    for alias in aliases:
        key = _norm_key(alias)
        if key in by_norm:
            return by_norm[key]
    if required:
        raise ValueError(
            "Colonna AIFA non trovata. Cercavo una tra: " + ", ".join(aliases)
            + ". Colonne disponibili: " + ", ".join(map(str, columns))
        )
    return None


def _find_semantic_col(columns, required_tokens, excluded_tokens=()):
    """Trova una colonna anche quando AIFA ne cambia leggermente l'intestazione."""
    ranked = []
    for col in columns:
        key = _norm_key(col)
        if any(token in key for token in excluded_tokens):
            continue
        if all(token in key for token in required_tokens):
            ranked.append((len(key), col))
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1] if ranked else None


def _find_packaging_col(columns):
    exact = _find_col(
        columns,
        [
            "CONFEZIONE",
            "DESCRIZIONE_CONFEZIONE",
            "DESCR_CONFEZIONE",
            "CONFEZIONE_DESCRIZIONE",
            "DESCRIZIONE FORNITURA",
            "DESCRIZIONE_FORNITURA",
            "FORMA_CONFEZIONE",
        ],
    )
    if exact:
        return exact
    # Evita campi di quantità contabile e privilegia descrizioni testuali della confezione.
    candidates = []
    for col in columns:
        key = _norm_key(col)
        if "CONFEZ" not in key:
            continue
        if any(x in key for x in ("QUANT", "PREZZ", "CODICE", "COD_", "NUMERO")):
            continue
        score = 0
        if "DESCR" in key:
            score += 4
        if key == "CONFEZIONE":
            score += 5
        candidates.append((score, -len(key), col))
    candidates.sort(reverse=True)
    return candidates[0][2] if candidates else None


def _read_csv_flexible(path: Path) -> pd.DataFrame:
    raw = path.read_bytes()
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            sample = raw[:10000].decode(encoding)
            try:
                sep = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
            except Exception:
                sep = ";"
            return pd.read_csv(io.BytesIO(raw), sep=sep, dtype=str, encoding=encoding, low_memory=False)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Impossibile leggere il CSV AIFA: {path.name}: {last_error}")


def clean_aic(value: Any) -> str:
    if value is None:
        return ""
    text = normalize_identifier(value)
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    return digits.zfill(9) if len(digits) <= 9 else digits


def _download(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with requests.get(url, timeout=120, stream=True) as response:
        response.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    tmp.replace(dest)


_UNIT_TOKENS = (
    "CPR", "COMPRESS", "CAPS", "CPS", "FLAC", "FL ", "FL.", "FIAL", "FIALE",
    "SIRING", "BUST", "SUPPOST", "CEROTT", "OVUL", "SACCH", "PEN", "PENNE",
    "AMP", "DOSE", "DOSI", "UNIT", "BLISTER", "CP ", "CPR.",
)


def _decimal_from_text(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = normalize_text(value)
    if not text:
        return None
    # Prima prova: valore puramente numerico.
    simple = text.replace(" ", "").replace(",", ".")
    try:
        return Decimal(simple)
    except (InvalidOperation, ValueError):
        pass

    # Valore del tipo "112 compresse" o "30 CPR".
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*[A-Za-zÀ-ÿ]", text)
    if m:
        try:
            return Decimal(m.group(1).replace(",", "."))
        except InvalidOperation:
            return None
    return None


def _derive_units_from_packaging(text: Any) -> tuple[Decimal | None, str]:
    """Ricava con prudenza il numero di unità dalla descrizione confezione.

    Non interpreta numeri seguiti da MG/ML per evitare di confondere dosaggio e quantità.
    Gestisce esempi come 112 CPR, 30X1 COMPRESSE, 2X14 CPS, 1 FL 15ML.
    """
    raw = normalize_text(text).upper()
    if not raw:
        return None, ""

    # 30X1 CPR, 4 X 28 COMPRESSE -> prodotto totale.
    patterns_mult = [
        r"(?<![A-Z0-9])(?P<a>\d{1,4})\s*[X×]\s*(?P<b>\d{1,4})\s*(?P<unit>CPR|COMPRESSE?|COMPRESSA|CAPSULE?|CPS|FIALE?|FLACONCIN[OI]|FLACON[EI]|BUSTE?|SIRINGHE?|DOSI?|UNIT[ÀA]?)\b",
    ]
    for pattern in patterns_mult:
        m = re.search(pattern, raw)
        if m:
            value = Decimal(m.group("a")) * Decimal(m.group("b"))
            return value, m.group(0)

    # 112 CPR, 1 FL, 30 compresse, ecc.
    pattern_single = (
        r"(?<![A-Z0-9])(?P<n>\d{1,5})\s*"
        r"(?P<unit>CPR|CP\.?|COMPRESSE?|COMPRESSA|CAPSULE?|CPS|FIALE?|FL\.?|FLACONCIN[OI]|FLACON[EI]|"
        r"BUSTE?|SIRINGHE?|SUPPOSTE?|CEROTTI?|OVULI?|SACCHETTI?|PENNE?|PEN|AMPO?LLE?|DOSI?|UNIT[ÀA]?|BLISTER)\b"
    )
    matches = list(re.finditer(pattern_single, raw))
    if matches:
        # La prima quantità legata esplicitamente a una forma/contenitore è la scelta più conservativa.
        m = matches[0]
        return Decimal(m.group("n")), m.group(0)

    return None, ""


def update_aifa_database(data_dir="data/aifa") -> dict:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    main_path = data_dir / "confezioni_fornitura.csv"
    pa_path = data_dir / "PA_confezioni.csv"
    atc_path = data_dir / "atc.csv"
    db_path = data_dir / "aifa.db"

    _download(AIFA_MAIN_URL, main_path)
    _download(AIFA_PA_URL, pa_path)
    _download(AIFA_ATC_URL, atc_path)

    main_df = _read_csv_flexible(main_path)
    pa_df = _read_csv_flexible(pa_path)

    main_aic = _find_col(main_df.columns, ["CODICE_AIC", "AIC", "COD_AIC"], required=True)
    main_name = _find_col(main_df.columns, ["DENOMINAZIONE", "NOME_COMMERCIALE", "FARMACO"])
    main_company = _find_col(main_df.columns, ["RAGIONE_SOCIALE", "AZIENDA_TITOLARE", "AZIENDA", "TITOLARE"])
    main_atc = _find_col(main_df.columns, ["CODICE_ATC", "ATC", "COD_ATC"])
    main_active = _find_col(main_df.columns, ["PRINCIPIO_ATTIVO", "PRINCIPIO_ATTIVO_COMPATTO", "PRINCIPIOATTIVO"])
    main_state = _find_col(main_df.columns, ["STATO_AMMINISTRATIVO", "STATO", "STATO_AUTORIZZATIVO"])

    main_units = _find_col(
        main_df.columns,
        [
            "UNITA_POSOLOGICHE", "UNITÀ_POSOLOGICHE", "NUMERO_UNITA_POSOLOGICHE",
            "N_UNITA_POSOLOGICHE", "UNITA POSOLOGICHE", "NUM_UNITA_POSOLOGICHE",
            "UNITA_POSOLOGICA", "UNITÀ POSOLOGICHE",
        ],
    )
    if main_units is None:
        main_units = _find_semantic_col(main_df.columns, ("UNIT", "POSOLOG"))

    main_pack = _find_packaging_col(main_df.columns)

    pa_aic = _find_col(pa_df.columns, ["CODICE_AIC", "AIC", "COD_AIC"], required=True)
    pa_active = _find_col(pa_df.columns, ["PRINCIPIO_ATTIVO", "PRINCIPIOATTIVO", "SOSTANZA_ATTIVA"], required=True)
    pa_qty = _find_col(pa_df.columns, ["QUANTITA", "DOSAGGIO"])
    pa_unit = _find_col(pa_df.columns, ["UNITA_MISURA", "UNITA_DI_MISURA", "UNITA"])

    farmaci = pd.DataFrame({
        "aic": main_df[main_aic].map(clean_aic),
        "nome": main_df[main_name].fillna("") if main_name else "",
        "azienda": main_df[main_company].fillna("") if main_company else "",
        "atc": main_df[main_atc].fillna("") if main_atc else "",
        "principio_compatto": main_df[main_active].fillna("") if main_active else "",
        "stato": main_df[main_state].fillna("") if main_state else "",
        "unita_posologiche": main_df[main_units].fillna("") if main_units else "",
        "confezione": main_df[main_pack].fillna("") if main_pack else "",
    })
    farmaci = farmaci[farmaci["aic"] != ""].drop_duplicates()
    farmaci["nome_norm"] = farmaci["nome"].astype(str).map(lambda x: normalize_text(x).casefold())
    farmaci["azienda_norm"] = farmaci["azienda"].astype(str).map(lambda x: normalize_text(x).casefold())

    pa = pd.DataFrame({
        "aic": pa_df[pa_aic].map(clean_aic),
        "principio_attivo": pa_df[pa_active].fillna(""),
        "quantita": pa_df[pa_qty].fillna("") if pa_qty else "",
        "unita": pa_df[pa_unit].fillna("") if pa_unit else "",
    })
    pa = pa[(pa["aic"] != "") & (pa["principio_attivo"].astype(str).str.strip() != "")].drop_duplicates()

    with sqlite3.connect(db_path) as con:
        farmaci.to_sql("farmaci", con, if_exists="replace", index=False)
        pa.to_sql("principi_attivi", con, if_exists="replace", index=False)
        con.execute("CREATE INDEX IF NOT EXISTS idx_farmaci_aic ON farmaci(aic)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_pa_aic ON principi_attivi(aic)")
        con.execute("DROP TABLE IF EXISTS metadata")
        con.execute("CREATE TABLE metadata (chiave TEXT PRIMARY KEY, valore TEXT)")
        con.executemany(
            "INSERT INTO metadata(chiave, valore) VALUES (?, ?)",
            [
                ("schema_version", AIFA_DB_SCHEMA_VERSION),
                ("updated_at", datetime.now().isoformat(timespec="seconds")),
                ("source_main", AIFA_MAIN_URL),
                ("source_pa", AIFA_PA_URL),
                ("source_atc", AIFA_ATC_URL),
                ("farmaci_rows", str(len(farmaci))),
                ("principi_rows", str(len(pa))),
                ("units_column", "" if main_units is None else str(main_units)),
                ("packaging_column", "" if main_pack is None else str(main_pack)),
                ("main_columns", json.dumps([str(c) for c in main_df.columns], ensure_ascii=False)),
            ],
        )
        con.commit()

    return get_aifa_status(data_dir)


def get_aifa_status(data_dir="data/aifa") -> dict:
    data_dir = Path(data_dir)
    db_path = data_dir / "aifa.db"
    if not db_path.exists():
        return {"available": False, "db_path": str(db_path), "updated_at": None, "needs_refresh": True}
    result = {"available": True, "db_path": str(db_path), "updated_at": None}
    try:
        with sqlite3.connect(db_path) as con:
            rows = dict(con.execute("SELECT chiave, valore FROM metadata").fetchall())
        result.update(rows)
    except Exception:
        pass
    result["needs_refresh"] = result.get("schema_version") != AIFA_DB_SCHEMA_VERSION
    return result


def lookup_by_aic(aic: Any, data_dir="data/aifa") -> dict | None:
    clean = clean_aic(aic)
    if not clean:
        return None
    db_path = Path(data_dir) / "aifa.db"
    if not db_path.exists():
        return None

    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        farmaci_cols = {row[1] for row in con.execute("PRAGMA table_info(farmaci)").fetchall()}
        def expr(name):
            return name if name in farmaci_cols else f"'' AS {name}"
        farmaco = con.execute(
            "SELECT aic, nome, azienda, atc, principio_compatto, stato, "
            f"{expr('unita_posologiche')}, {expr('confezione')} "
            "FROM farmaci WHERE aic=? LIMIT 1",
            (clean,),
        ).fetchone()
        pa_rows = con.execute(
            "SELECT principio_attivo, quantita, unita FROM principi_attivi WHERE aic=? ORDER BY principio_attivo",
            (clean,),
        ).fetchall()

    if farmaco is None and not pa_rows:
        return None

    principles = []
    for row in pa_rows:
        value = normalize_text(row["principio_attivo"])
        if value and value.casefold() not in {p.casefold() for p in principles}:
            principles.append(value)
    if not principles and farmaco is not None:
        compact = normalize_text(farmaco["principio_compatto"])
        if compact:
            principles = [compact]

    structured_units = "" if farmaco is None else farmaco["unita_posologiche"]
    packaging = "" if farmaco is None else farmaco["confezione"]
    units_number = _decimal_from_text(structured_units)
    units_source = "Unità Posologiche AIFA"
    units_raw = structured_units
    derived_fragment = ""

    if units_number is None and packaging:
        units_number, derived_fragment = _derive_units_from_packaging(packaging)
        if units_number is not None:
            units_source = "Derivato dalla descrizione confezione AIFA"
            units_raw = str(units_number)

    return {
        "aic": clean,
        "nome": "" if farmaco is None else farmaco["nome"],
        "azienda": "" if farmaco is None else farmaco["azienda"],
        "atc": "" if farmaco is None else farmaco["atc"],
        "stato": "" if farmaco is None else farmaco["stato"],
        "confezione": packaging,
        "unita_posologiche": structured_units,
        "unita_numero": units_number,
        "unita_display": units_raw,
        "unita_fonte": units_source if units_number is not None else "Non disponibile",
        "unita_match_testo": derived_fragment,
        "principi_attivi": principles,
        "principio_attivo": " / ".join(principles),
    }


def search_candidates(nome: Any, fornitore: Any = "", data_dir="data/aifa", limit=10) -> list[dict]:
    db_path = Path(data_dir) / "aifa.db"
    if not db_path.exists():
        return []
    nome_norm = normalize_text(nome).casefold()
    if not nome_norm:
        return []
    first_token = nome_norm.split()[0][:12]
    pattern = f"%{first_token}%"
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        farmaci_cols = {row[1] for row in con.execute("PRAGMA table_info(farmaci)").fetchall()}
        def expr(name):
            return name if name in farmaci_cols else f"'' AS {name}"
        rows = con.execute(
            "SELECT aic, nome, azienda, atc, stato, "
            f"{expr('unita_posologiche')}, {expr('confezione')} "
            "FROM farmaci WHERE nome_norm LIKE ? LIMIT 250",
            (pattern,),
        ).fetchall()

    supplier_norm = normalize_text(fornitore).casefold()
    scored = []
    for row in rows:
        name_score = SequenceMatcher(None, nome_norm, normalize_text(row["nome"]).casefold()).ratio()
        supplier_score = 0.0
        if supplier_norm:
            supplier_score = SequenceMatcher(None, supplier_norm, normalize_text(row["azienda"]).casefold()).ratio()
        score = name_score * (0.85 if supplier_norm else 1.0) + supplier_score * (0.15 if supplier_norm else 0.0)
        scored.append((score, dict(row)))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{**row, "score": round(score, 3)} for score, row in scored[:limit]]


def _field_state(received: Any, aifa: Any, blank_tokens) -> str:
    if is_semantic_blank(aifa, blank_tokens):
        return "AIFA NON DISPONIBILE"
    if is_semantic_blank(received, blank_tokens):
        return "COMPLETATO DA AIFA"
    return "PRESENTE"


def _values_equal(field: str, received: Any, aifa: Any) -> bool:
    left = normalize_text(received).casefold()
    right = normalize_text(aifa).casefold()
    if field == "Nome Commerciale":
        if not left or not right:
            return True
        # I nomi fornitori spesso includono confezione/dosaggio mentre AIFA può esporre solo il brand.
        if left.startswith(right) or right.startswith(left):
            return True
        return SequenceMatcher(None, left, right).ratio() >= 0.55
    return left == right


def reconcile_with_aifa(
    workbook_data: WorkbookData,
    schema: dict,
    data_dir="data/aifa",
    use_holder_as_supplier: bool = False,
):
    """Riconcilia i campi anagrafici con AIFA tramite match AIC esatto.

    Con AIC esatto può completare Nome Commerciale, Principio Attivo e ATC7 nel dataset
    operativo quando i campi sono vuoti. I campi compilati da AIFA restano parte del tracciato
    validato quando sono previsti dallo schema corrente.
    L'Azienda titolare AIFA è distinta semanticamente dal Fornitore: può essere applicata
    al campo Fornitore solo tramite opzione esplicita dell'operatore.
    """
    status = get_aifa_status(data_dir)
    if not status.get("available"):
        return workbook_data, [], [], [], [], []

    blank_tokens = schema.get("quality", {}).get("semantic_blank_tokens", [])
    records = deepcopy(workbook_data.records)
    enrichments = []
    issues = []
    candidates = []
    checks = []
    supplier_proposals = []

    auto_fields = [
        ("Nome Commerciale", "nome"),
        ("Principio Attivo", "principio_attivo"),
        ("ATC7", "atc"),
    ]

    for record, excel_row in zip(records, workbook_data.source_rows):
        aic = record.get("AIC")
        if is_semantic_blank(aic, blank_tokens):
            if is_semantic_blank(record.get("Principio Attivo"), blank_tokens) or is_semantic_blank(record.get("Nome Commerciale"), blank_tokens):
                found = search_candidates(record.get("Nome Commerciale"), record.get("Fornitore"), data_dir)
                for item in found:
                    candidates.append({
                        "Riga Excel": excel_row,
                        "Nome ricevuto": record.get("Nome Commerciale", ""),
                        "Fornitore ricevuto": record.get("Fornitore", ""),
                        "AIC candidato": item.get("aic", ""),
                        "Nome AIFA": item.get("nome", ""),
                        "Azienda AIFA": item.get("azienda", ""),
                        "ATC AIFA": item.get("atc", ""),
                        "Confezione AIFA": item.get("confezione", ""),
                        "Score": item.get("score", 0),
                    })
                if found:
                    issues.append({
                        "Riga Excel": excel_row,
                        "Campo": "AIC",
                        "Valore ricevuto": "",
                        "Codice Errore": "RICERCA_AIFA_ASSISTITA_RICHIESTA",
                        "Livello": "WARNING",
                        "Descrizione": "AIC assente: sono stati cercati candidati AIFA usando il nome commerciale e il fornitore.",
                        "Valori ammessi / Regola": "Confermare manualmente il candidato corretto prima della pubblicazione.",
                    })
            continue

        match = lookup_by_aic(aic, data_dir)
        clean = clean_aic(aic)
        if not match:
            checks.append({
                "Riga Excel": excel_row,
                "AIC": clean,
                "Match AIFA": "NON TROVATO",
                "Nome ricevuto": record.get("Nome Commerciale", ""),
                "Nome AIFA": "",
                "Principio ricevuto": record.get("Principio Attivo", ""),
                "Principio AIFA": "",
                "ATC7 ricevuto": record.get("ATC7", ""),
                "ATC AIFA": "",
                "Fornitore ricevuto": record.get("Fornitore", ""),
                "Azienda titolare AIFA": "",
                "Stato amministrativo": "",
                "Azione": "Verificare AIC / aggiornamento banca AIFA",
            })
            issues.append({
                "Riga Excel": excel_row,
                "Campo": "AIC",
                "Valore ricevuto": aic,
                "Codice Errore": "AIC_NON_TROVATO_AIFA",
                "Livello": "WARNING",
                "Descrizione": "L'AIC non è stato trovato nella banca dati AIFA locale.",
                "Valori ammessi / Regola": "Verificare il codice AIC o aggiornare la banca AIFA.",
            })
            continue

        original_values = {
            "Nome Commerciale": record.get("Nome Commerciale"),
            "Principio Attivo": record.get("Principio Attivo"),
            "ATC7": record.get("ATC7"),
            "Fornitore": record.get("Fornitore"),
        }

        # Mantiene l'AIC canonico a 9 cifre nel dataset normalizzato.
        record["AIC"] = match["aic"]
        actions = []

        for field, key in auto_fields:
            received = record.get(field)
            aifa_value = match.get(key, "")
            if is_semantic_blank(aifa_value, blank_tokens):
                continue

            if is_semantic_blank(received, blank_tokens):
                record[field] = aifa_value
                actions.append(f"{field}: completato")
                enrichments.append({
                    "Riga Excel": excel_row,
                    "Campo": field,
                    "Valore originale": "",
                    "Valore AIFA": aifa_value,
                    "AIC": match["aic"],
                    "Nome AIFA": match.get("nome", ""),
                    "Azienda AIFA": match.get("azienda", ""),
                    "ATC AIFA": match.get("atc", ""),
                    "Fonte": "AIFA Open Data",
                    "Metodo": "Match esatto AIC",
                    "Confidence": "100%",
                })
                input_fields = {c.get("name") for c in schema.get("columns", [])}
                is_derived = field not in input_fields
                issues.append({
                    "Riga Excel": excel_row,
                    "Campo": field,
                    "Valore ricevuto": "",
                    "Codice Errore": (
                        f"{_norm_key(field)}_DERIVATO_AIFA" if is_derived
                        else f"{_norm_key(field)}_COMPLETATO_AIFA"
                    ),
                    "Livello": "INFO",
                    "Descrizione": (
                        f"Il dato '{field}' è stato acquisito da AIFA come informazione derivata interna al listino e non viene aggiunto al tracciato validato."
                        if is_derived
                        else f"Il campo '{field}' mancante è stato completato nel dataset normalizzato tramite match esatto AIC."
                    ),
                    "Valori ammessi / Regola": aifa_value,
                })
            elif field in {"Principio Attivo", "ATC7", "Nome Commerciale"} and not _values_equal(field, received, aifa_value):
                issues.append({
                    "Riga Excel": excel_row,
                    "Campo": field,
                    "Valore ricevuto": received,
                    "Codice Errore": f"{_norm_key(field)}_DIVERSO_DA_AIFA",
                    "Livello": "WARNING",
                    "Descrizione": f"Il valore ricevuto per '{field}' non coincide con il riferimento AIFA per lo stesso AIC.",
                    "Valori ammessi / Regola": f"AIFA: {aifa_value}",
                })

        supplier = record.get("Fornitore")
        holder = match.get("azienda", "")
        supplier_action = ""
        if is_semantic_blank(supplier, blank_tokens) and not is_semantic_blank(holder, blank_tokens):
            supplier_proposals.append({
                "Riga Excel": excel_row,
                "AIC": match["aic"],
                "Nome AIFA": match.get("nome", ""),
                "Fornitore ricevuto": "",
                "Azienda titolare AIFA": holder,
                "Azione": "APPLICATO" if use_holder_as_supplier else "DA CONFERMARE",
                "Nota": "Azienda titolare AIC e fornitore commerciale possono non coincidere.",
            })
            if use_holder_as_supplier:
                record["Fornitore"] = holder
                supplier_action = "Fornitore completato con Azienda titolare AIFA (scelta operatore)"
                actions.append("Fornitore: completato su conferma")
                enrichments.append({
                    "Riga Excel": excel_row,
                    "Campo": "Fornitore",
                    "Valore originale": "",
                    "Valore AIFA": holder,
                    "AIC": match["aic"],
                    "Nome AIFA": match.get("nome", ""),
                    "Azienda AIFA": holder,
                    "ATC AIFA": match.get("atc", ""),
                    "Fonte": "AIFA Open Data",
                    "Metodo": "Azienda titolare applicata su conferma operatore",
                    "Confidence": "CONFERMATO OPERATORE",
                })
                issues.append({
                    "Riga Excel": excel_row,
                    "Campo": "Fornitore",
                    "Valore ricevuto": "",
                    "Codice Errore": "FORNITORE_COMPLETATO_DA_TITOLARE_AIFA",
                    "Livello": "INFO",
                    "Descrizione": "Il campo Fornitore vuoto è stato valorizzato con l'Azienda titolare AIFA su scelta esplicita dell'operatore.",
                    "Valori ammessi / Regola": holder,
                })
            else:
                supplier_action = "Azienda titolare disponibile: conferma richiesta"
                issues.append({
                    "Riga Excel": excel_row,
                    "Campo": "Fornitore",
                    "Valore ricevuto": "",
                    "Codice Errore": "FORNITORE_PROPONIBILE_DA_AIFA",
                    "Livello": "INFO",
                    "Descrizione": "AIFA espone l'Azienda titolare dell'AIC. Può essere usata come proposta di Fornitore, ma non viene applicata automaticamente.",
                    "Valori ammessi / Regola": holder,
                })

        checks.append({
            "Riga Excel": excel_row,
            "AIC": match["aic"],
            "Match AIFA": "ESATTO",
            "Nome ricevuto": original_values.get("Nome Commerciale") or "",
            "Nome AIFA": match.get("nome", ""),
            "Principio ricevuto": original_values.get("Principio Attivo") or "",
            "Principio AIFA": match.get("principio_attivo", ""),
            "ATC7 ricevuto": original_values.get("ATC7") or "",
            "ATC AIFA": match.get("atc", ""),
            "Fornitore ricevuto": original_values.get("Fornitore") or "",
            "Azienda titolare AIFA": holder,
            "Stato amministrativo": match.get("stato", ""),
            "Azione": "; ".join(actions) if actions else (supplier_action or "Confronto completato"),
        })

    enriched = replace(workbook_data, records=records)
    return enriched, enrichments, issues, candidates, checks, supplier_proposals


def compare_upc_with_aifa(workbook_data: WorkbookData, schema: dict, data_dir="data/aifa"):
    status = get_aifa_status(data_dir)
    if not status.get("available"):
        return [], []

    cfg = schema.get("aifa", {}).get("upc_check", {})
    if cfg.get("enabled", True) is False:
        return [], []

    blank_tokens = schema.get("quality", {}).get("semantic_blank_tokens", [])
    checks = []
    issues = []

    for record, excel_row in zip(workbook_data.records, workbook_data.source_rows):
        aic = record.get("AIC")
        upc = record.get("UPC")
        if is_semantic_blank(aic, blank_tokens):
            continue

        match = lookup_by_aic(aic, data_dir)
        if not match:
            checks.append({
                "Riga Excel": excel_row,
                "AIC": clean_aic(aic),
                "Nome Commerciale": record.get("Nome Commerciale", ""),
                "UPC ricevuto": upc,
                "Unità Posologiche AIFA": "",
                "Fonte unità": "",
                "Rapporto UPC/AIFA": "",
                "Esito": "AIC NON TROVATO",
                "Nota": "Confronto UPC non eseguibile.",
            })
            continue

        aifa_number = match.get("unita_numero")
        if aifa_number is None:
            checks.append({
                "Riga Excel": excel_row,
                "AIC": match["aic"],
                "Nome Commerciale": record.get("Nome Commerciale", ""),
                "UPC ricevuto": upc,
                "Unità Posologiche AIFA": match.get("unita_posologiche", ""),
                "Fonte unità": match.get("unita_fonte", "Non disponibile"),
                "Rapporto UPC/AIFA": "",
                "Esito": "NON DISPONIBILE",
                "Nota": "Né il campo strutturato né la descrizione confezione AIFA consentono un confronto affidabile.",
            })
            continue

        upc_number = parse_decimal(upc)
        if upc_number is None:
            checks.append({
                "Riga Excel": excel_row,
                "AIC": match["aic"],
                "Nome Commerciale": record.get("Nome Commerciale", ""),
                "UPC ricevuto": upc,
                "Unità Posologiche AIFA": str(aifa_number),
                "Fonte unità": match.get("unita_fonte", ""),
                "Rapporto UPC/AIFA": "",
                "Esito": "NON CONFRONTABILE",
                "Nota": "UPC ricevuto non numerico.",
            })
            continue

        coherent = upc_number == aifa_number
        ratio = ""
        if aifa_number != 0:
            q = upc_number / aifa_number
            if q == q.to_integral_value():
                ratio = f"{int(q)}×"
            else:
                ratio = f"{q.quantize(Decimal('0.01'))}×"

        source = match.get("unita_fonte", "")
        note = "Match esatto AIC."
        if source.startswith("Derivato"):
            note += f" Quantità ricavata dalla descrizione confezione AIFA ({match.get('unita_match_testo', '')})."

        checks.append({
            "Riga Excel": excel_row,
            "AIC": match["aic"],
            "Nome Commerciale": record.get("Nome Commerciale", ""),
            "UPC ricevuto": int(upc_number) if upc_number == upc_number.to_integral_value() else str(upc_number),
            "Unità Posologiche AIFA": int(aifa_number) if aifa_number == aifa_number.to_integral_value() else str(aifa_number),
            "Fonte unità": source,
            "Rapporto UPC/AIFA": ratio,
            "Esito": "COERENTE" if coherent else "DA VERIFICARE",
            "Nota": note,
        })

        if not coherent:
            issues.append({
                "Riga Excel": excel_row,
                "Campo": "UPC",
                "Valore ricevuto": upc,
                "Codice Errore": cfg.get("code", "UPC_DIVERSO_DA_AIFA"),
                "Livello": cfg.get("level", "WARNING"),
                "Descrizione": cfg.get(
                    "message",
                    "L'UPC ricevuto non coincide con le Unità Posologiche AIFA per lo stesso AIC.",
                ),
                "Valori ammessi / Regola": (
                    f"Unità AIFA: {aifa_number}; rapporto UPC/AIFA: {ratio or '-'}; fonte: {source}. "
                    "Verificare la semantica logistica dell'UPC prima della pubblicazione."
                ),
            })

    return checks, issues

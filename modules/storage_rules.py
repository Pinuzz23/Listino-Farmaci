from __future__ import annotations

import re
from typing import Any


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").strip().casefold()
    text = " ".join(text.split())
    # Uniforma piccoli scostamenti tipografici senza perdere il significato.
    text = text.replace("º", "°")
    return text


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(_norm(keyword) in text for keyword in keywords if _norm(keyword))


def infer_storage_group(stupefacente: Any, temperature: Any, schema: dict) -> dict:
    """Restituisce il gruppo di stivaggio atteso quando la regola è deterministica.

    Priorità:
    1. S7/S8 -> STUPEF, indipendentemente dalla temperatura.
    2. Temperatura/conservazione -> FREEZER / FRIGO / STD.
    3. Se la temperatura non rientra nelle casistiche concordate, nessuna inferenza.

    La funzione supporta sia i valori codificati del tracciato sia, in previsione
    dell'arricchimento RCP AIFA, testo libero di conservazione.
    """
    cfg = schema.get("storage_group_rules", {})
    if not cfg.get("enabled", False):
        return {"expected": None, "source": None, "reason": None}

    stupef_text = _norm(stupefacente)
    for category, group in cfg.get("stupefacente_overrides", {}).items():
        if stupef_text and stupef_text == _norm(category):
            return {
                "expected": group,
                "source": "Stupefacente",
                "reason": f"La categoria '{category}' richiede il gruppo '{group}'.",
            }

    temp_text = _norm(temperature)
    if not temp_text:
        return {"expected": None, "source": None, "reason": None}

    for group, values in cfg.get("temperature_exact", {}).items():
        if any(temp_text == _norm(value) for value in values):
            return {
                "expected": group,
                "source": "Temperatura di Stivaggio",
                "reason": f"La temperatura/conservazione indicata ricade nel gruppo '{group}'.",
            }

    # Se compare esplicitamente "non congelare" non deve essere interpretato
    # come richiesta di freezer. Le altre condizioni presenti nel testo possono
    # comunque determinare FRIGO o STD.
    text_for_freezer = re.sub(r"\bnon\s+congel\w*", "", temp_text)

    keyword_cfg = cfg.get("temperature_keywords", {})
    if _contains_any(text_for_freezer, keyword_cfg.get("FREEZER_Freezer", [])):
        return {
            "expected": "FREEZER_Freezer",
            "source": "Conservazione",
            "reason": "Il testo di conservazione indica congelatore/freezer o temperature intorno a -20°C.",
        }
    if _contains_any(temp_text, keyword_cfg.get("FRIGO_Frigo", [])):
        return {
            "expected": "FRIGO_Frigo",
            "source": "Conservazione",
            "reason": "Il testo di conservazione indica refrigerazione, fresco o temperature inferiori a 15°C / 2-8°C.",
        }
    if _contains_any(temp_text, keyword_cfg.get("STD_Standard", [])):
        return {
            "expected": "STD_Standard",
            "source": "Conservazione",
            "reason": "Il testo di conservazione indica condizioni standard/ambiente o protezione da luce/calore.",
        }

    return {"expected": None, "source": None, "reason": None}

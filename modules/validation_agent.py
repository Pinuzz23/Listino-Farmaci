from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from modules.tool_registry import ToolRegistry


PLANNER_SYSTEM_PROMPT = """
Sei il planner di un Copilot di validazione per un tracciato farmaceutico.
Il tuo compito NON è rispondere direttamente e NON è applicare modifiche.
Puoi scegliere da 1 a 4 tool READ oppure GENERATE dal registro fornito e restituire SOLO JSON valido.

Regole:
- usa solo tool presenti nel manifest;
- non inventare nomi di tool o parametri;
- i tool GENERATE producono Change Set di PROPOSTA: non modificano il dataset;
- NON usare mai tool WRITE: l'applicazione avviene solo dopo conferma esplicita nell'interfaccia;
- se l'utente chiede perché il file non passa, usa summary + correction_plan;
- se chiede errori specifici, usa list_issues e/o explain_issue;
- se chiede la riga X, usa show_row;
- se chiede cosa può fare AIFA, usa aifa_findings;
- se chiede di correggere prezzi confezione, usa validation_propose_package_price_fix;
- se chiede di correggere stivaggio, usa validation_propose_storage_group_fix;
- se chiede di allineare/completare dati AIFA, usa validation_propose_aifa_alignment;
- se chiede di correggere automaticamente tutto ciò che è deterministico, usa validation_propose_all_deterministic;
- se chiede duplicati identici, usa validation_propose_identical_duplicate_deletion;
- se chiede di usare Azienda AIFA come Fornitore, usa validation_propose_supplier_from_aifa;
- se chiede il significato di un campo o una regola, usa describe_field/business_rules;
- se la domanda è ambigua e non può essere risolta con i tool, imposta needs_clarification=true;
- massimo 4 chiamate.

Formato:
{
  "calls": [
    {"tool": "nome_tool", "arguments": {}}
  ],
  "needs_clarification": false,
  "clarification": ""
}
""".strip()


ANSWER_SYSTEM_PROMPT = """
Sei un assistente operativo per la validazione di listini farmaceutici.
Rispondi in italiano usando ESCLUSIVAMENTE i risultati dei tool forniti.
Non inventare dati, righe, codici o valori.

I tool READ analizzano. I tool GENERATE possono creare Change Set di proposta ma NON applicano modifiche.
Se è stato generato un Change Set, spiega quante modifiche contiene, il rischio e invita l'operatore a controllare l'anteprima e la simulazione nell'interfaccia prima di approvarlo.
Non affermare mai che una modifica è stata applicata se non c'è un tool WRITE già confermato (il planner non lo invoca).

Obiettivo:
- stato del file;
- problemi principali con conteggi;
- righe coinvolte quando disponibili;
- azione consigliata;
- proposte deterministiche quando richieste;
- distinguere modifiche controlled/sensitive;
- se pertinente, indicare AIFA e la necessità di verifica umana.

Se non ci sono errori bloccanti, dillo chiaramente.
""".strip()


def _base_url(url: str) -> str:
    return (url or "http://127.0.0.1:11434").rstrip("/")


def _extract_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        payload = json.loads(text[start : end + 1])
        if isinstance(payload, dict):
            return payload
    raise ValueError("Il modello non ha restituito JSON valido.")


def _ollama_chat(
    messages: list[dict[str, str]],
    *,
    model: str,
    base_url: str,
    timeout: float,
    json_mode: bool = False,
) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0},
        "messages": messages,
    }
    if json_mode:
        payload["format"] = "json"
    response = requests.post(
        f"{_base_url(base_url)}/api/chat",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return str((data.get("message") or {}).get("content") or "")


def _known_issue_codes(context: dict[str, Any]) -> list[str]:
    issues = ((context.get("result") or {}).get("issues") or [])
    return sorted({str(item.get("Codice Errore", "")).upper() for item in issues if item.get("Codice Errore")})


def _fallback_plan(user_text: str, context: dict[str, Any]) -> dict[str, Any]:
    text = (user_text or "").strip()
    norm = text.casefold()
    calls: list[dict[str, Any]] = []

    row_match = re.search(r"\briga\s+(\d+)\b", norm)
    row_arg = {"row": int(row_match.group(1))} if row_match else {}

    code_found = None
    upper = text.upper()
    for code in _known_issue_codes(context):
        if code and code in upper:
            code_found = code
            break

    wants_fix = any(token in norm for token in [
        "correggi", "correggere", "sistema", "sistemare", "risolvi", "aggiusta",
        "proponi correz", "puoi correg", "correggilo", "correggili",
    ])

    if wants_fix:
        if any(token in norm for token in ["tutto quello che puoi", "tutto ciò che puoi", "tutto cio che puoi", "automaticamente tutto", "correggi tutto"]):
            calls.append({"tool": "validation_propose_all_deterministic", "arguments": row_arg})
        elif "prezzo" in norm and ("confezione" in norm or "prezzi" in norm):
            calls.append({"tool": "validation_propose_package_price_fix", "arguments": row_arg})
        elif "stivaggio" in norm or "temperatura" in norm:
            calls.append({"tool": "validation_propose_storage_group_fix", "arguments": row_arg})
        elif "fornitore" in norm and "aifa" in norm:
            calls.append({"tool": "validation_propose_supplier_from_aifa", "arguments": row_arg})
        elif "duplic" in norm:
            calls.append({"tool": "validation_propose_identical_duplicate_deletion", "arguments": row_arg})
        elif "aifa" in norm or "principio attivo" in norm or "atc" in norm or "nome commerciale" in norm:
            calls.append({"tool": "validation_propose_aifa_alignment", "arguments": row_arg})

    if row_match and not wants_fix:
        calls.append({"tool": "validation_show_row", "arguments": {"row": int(row_match.group(1))}})

    if code_found:
        calls.append({"tool": "validation_explain_issue", "arguments": {"code": code_found}})
        if not wants_fix:
            calls.append({"tool": "validation_list_issues", "arguments": {"code": code_found, "limit": 25}})

    if any(token in norm for token in ["perché non passa", "perche non passa", "perché non è valido", "perche non e valido", "stato validazione", "stato del file"]):
        calls.extend([
            {"tool": "validation_summary", "arguments": {}},
            {"tool": "validation_correction_plan", "arguments": {}},
        ])
    elif any(token in norm for token in ["da dove comincio", "piano di correzione", "ordine di correzione", "cosa correggere prima"]):
        calls.extend([
            {"tool": "validation_summary", "arguments": {}},
            {"tool": "validation_correction_plan", "arguments": {}},
        ])
    elif any(token in norm for token in ["più frequente", "piu frequente", "errore principale", "problema principale"]):
        calls.append({"tool": "validation_group_issues", "arguments": {"by": "code", "limit": 10}})
    elif "significa" in norm or "cos'è" in norm or "cosa è" in norm or "campo" in norm and "spieg" in norm:
        field_names = [c.get("name", "") for c in (context.get("schema") or {}).get("columns", [])]
        found = next((f for f in field_names if f.casefold() in norm), "")
        calls.append({"tool": "validation_describe_field", "arguments": {"field": found}})
    elif "regola" in norm and not wants_fix:
        calls.append({"tool": "validation_business_rules", "arguments": {}})
    elif "aifa" in norm and not wants_fix:
        calls.append({"tool": "validation_aifa_findings", "arguments": {}})
    elif "normalizz" in norm:
        calls.append({"tool": "validation_normalizations", "arguments": {"limit": 40}})
    elif "modific" in norm and ("operatore" in norm or "ai" in norm):
        calls.append({"tool": "validation_operator_changes", "arguments": {"limit": 40}})
    elif "bloccant" in norm:
        calls.append({"tool": "validation_list_issues", "arguments": {"level": "BLOCCANTE", "limit": 50}})
    elif "warning" in norm:
        calls.append({"tool": "validation_list_issues", "arguments": {"level": "WARNING", "limit": 50}})
    elif "prezzo" in norm and not wants_fix:
        calls.append({"tool": "validation_list_issues", "arguments": {"field": "Prezzo", "limit": 50}})
    elif ("stivaggio" in norm or "temperatura" in norm) and not wants_fix:
        calls.append({"tool": "validation_list_issues", "arguments": {"field": "Stivaggio", "limit": 50}})
    elif "aic" in norm and not wants_fix:
        calls.append({"tool": "validation_list_issues", "arguments": {"field": "AIC", "limit": 50}})

    if not calls:
        calls = [
            {"tool": "validation_summary", "arguments": {}},
            {"tool": "validation_correction_plan", "arguments": {}},
        ]

    deduped = []
    seen = set()
    for call in calls:
        key = json.dumps(call, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call)
    return {"calls": deduped[:4], "needs_clarification": False, "clarification": "", "engine": "fallback"}

def plan_validation_request(
    user_text: str,
    registry: ToolRegistry,
    context: dict[str, Any],
    *,
    history: list[dict[str, str]] | None = None,
    model: str = "",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
) -> dict[str, Any]:
    if not model:
        return _fallback_plan(user_text, context)

    manifest = registry.manifest(access={"read", "generate"})
    planner_input = {
        "tool_manifest": manifest,
        "current_issue_codes": _known_issue_codes(context),
        "conversation_history": list(history or [])[-8:],
        "user_request": user_text,
    }
    try:
        content = _ollama_chat(
            [
                {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(planner_input, ensure_ascii=False)},
            ],
            model=model,
            base_url=base_url,
            timeout=timeout,
            json_mode=True,
        )
        payload = _extract_json(content)
        calls = payload.get("calls") or []
        safe_calls = []
        allowed_names = set(registry.names())
        for call in calls[:4]:
            if not isinstance(call, dict):
                continue
            name = str(call.get("tool") or "")
            if name not in allowed_names:
                continue
            spec = registry.get(name)
            if spec.access not in {"read", "generate"}:
                continue
            args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            safe_calls.append({"tool": name, "arguments": args})
        if not safe_calls and not payload.get("needs_clarification"):
            return _fallback_plan(user_text, context)
        return {
            "calls": safe_calls,
            "needs_clarification": bool(payload.get("needs_clarification")),
            "clarification": str(payload.get("clarification") or ""),
            "engine": "ollama",
            "model": model,
        }
    except Exception as exc:
        fallback = _fallback_plan(user_text, context)
        fallback["planner_error"] = str(exc)
        return fallback


def execute_plan(plan: dict[str, Any], registry: ToolRegistry, context: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = []
    for call in (plan.get("calls") or [])[:4]:
        name = call.get("tool")
        arguments = call.get("arguments") or {}
        try:
            result = registry.execute(
                name,
                context,
                arguments,
                allowed_access={"read", "generate"},
            )
            outputs.append({"tool": name, "arguments": arguments, "ok": True, "result": result})
        except Exception as exc:
            outputs.append({"tool": name, "arguments": arguments, "ok": False, "error": str(exc)})
    return outputs


def _fallback_answer(user_text: str, outputs: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in outputs:
        if not item.get("ok"):
            chunks.append(f"Il tool {item.get('tool')} non è riuscito: {item.get('error')}.")
            continue
        tool = item.get("tool")
        result = item.get("result") or {}
        if tool == "validation_summary":
            if result.get("valid"):
                chunks.append(
                    f"Il file è validato: 0 errori bloccanti e {result.get('warnings', 0)} warning."
                )
            else:
                chunks.append(
                    f"Il file non è ancora validabile: {result.get('blocking', 0)} errori bloccanti, "
                    f"{result.get('warnings', 0)} warning su {result.get('rows', 0)} articoli."
                )
        elif tool == "validation_correction_plan":
            steps = result.get("steps") or []
            if not steps:
                chunks.append("Non risultano correzioni residue.")
            else:
                text = ["Ordine di correzione consigliato:"]
                for step in steps[:6]:
                    rows = ", ".join(str(v) for v in step.get("rows", [])[:8])
                    text.append(
                        f"{step.get('priority')}. {step.get('code')} ({step.get('count')} casi" +
                        (f", righe {rows}" if rows else "") + f"): {step.get('action')}"
                    )
                chunks.append("\n".join(text))
        elif tool == "validation_list_issues":
            rows = result.get("items") or []
            if not rows:
                chunks.append("Non risultano anomalie corrispondenti alla richiesta.")
            else:
                text = [f"Ho trovato {result.get('count', len(rows))} anomalie corrispondenti:"]
                for row in rows[:10]:
                    text.append(
                        f"- riga {row.get('Riga Excel')}, {row.get('Campo')}: "
                        f"{row.get('Codice Errore')} — {row.get('Descrizione')}"
                    )
                chunks.append("\n".join(text))
        elif tool == "validation_group_issues":
            items = result.get("items") or []
            if not items:
                chunks.append("Non risultano anomalie da raggruppare.")
            else:
                chunks.append("Problemi più frequenti: " + "; ".join(f"{i['value']} ({i['count']})" for i in items[:8]) + ".")
        elif tool == "validation_explain_issue":
            chunks.append(
                f"{result.get('code')}: {result.get('meaning')} "
                f"Azione consigliata: {result.get('recommended_action')} "
                f"Nel file compare {result.get('count', 0)} volte."
            )
        elif tool == "validation_show_row":
            if not result.get("found"):
                chunks.append(result.get("message") or "Riga non trovata.")
            else:
                issues = result.get("issues") or []
                chunks.append(
                    f"La riga {result.get('row')} ha {len(issues)} anomalie. "
                    + ("; ".join(f"{i.get('Campo')}: {i.get('Codice Errore')}" for i in issues) if issues else "Non risultano anomalie su questa riga.")
                )
        elif tool == "validation_aifa_findings":
            chunks.append(
                f"AIFA: {result.get('exact_matches', 0)} match esatti, {result.get('missing_matches', 0)} AIC non trovati, "
                f"{len(result.get('enrichments') or [])} arricchimenti e "
                f"{sum(1 for x in (result.get('upc_checks') or []) if str(x.get('Esito', '')).upper() not in {'COERENTE', 'OK'})} controlli UPC da verificare."
            )
        elif tool == "validation_normalizations":
            chunks.append(f"Sono registrate {result.get('count', 0)} normalizzazioni nel filtro richiesto.")
        elif tool == "validation_operator_changes":
            chunks.append(f"Sono registrate {result.get('count', 0)} modifiche manuali/AI nella sessione.")
        elif tool == "validation_describe_field":
            if not result.get("found", True):
                chunks.append(result.get("message") or "Campo non trovato nel dizionario dati.")
            else:
                items = result.get("items") or []
                if items:
                    chunks.append(" ".join(
                        f"{x.get('field')}: {x.get('description')}" for x in items[:5]
                    ))
                else:
                    chunks.append("Dizionario dati disponibile per tutti i campi del tracciato.")
        elif tool == "validation_business_rules":
            rules = result.get("rules") or {}
            if not rules:
                chunks.append("Nessuna regola di business corrispondente trovata.")
            else:
                chunks.append("Regole: " + "; ".join(
                    f"{key}: {value.get('description', '')}" for key, value in list(rules.items())[:8]
                ))
        elif tool.startswith("validation_propose_"):
            count = int(result.get("count", 0))
            if count == 0:
                chunks.append(f"Non ho trovato modifiche proponibili con {tool}.")
            else:
                risks = result.get("risk_counts") or {}
                chunks.append(
                    f"Ho preparato il Change Set {result.get('change_set_id')} con {count} modifiche proposte "
                    f"({risks.get('controlled', 0)} controllate, {risks.get('sensitive', 0)} sensibili). "
                    "Nessuna modifica è stata applicata: controlla anteprima e simulazione, poi conferma le righe desiderate."
                )
    return "\n\n".join(chunks) if chunks else "Non ho ottenuto elementi sufficienti per rispondere."


def compose_answer(
    user_text: str,
    outputs: list[dict[str, Any]],
    *,
    history: list[dict[str, str]] | None = None,
    model: str = "",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
) -> tuple[str, str | None]:
    if not model:
        return _fallback_answer(user_text, outputs), None
    try:
        safe_payload = {
            "conversation_history": list(history or [])[-8:],
            "user_request": user_text,
            "tool_outputs": outputs,
        }
        content = _ollama_chat(
            [
                {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(safe_payload, ensure_ascii=False, default=str)},
            ],
            model=model,
            base_url=base_url,
            timeout=timeout,
            json_mode=False,
        )
        if content.strip():
            return content.strip(), None
    except Exception as exc:
        return _fallback_answer(user_text, outputs), str(exc)
    return _fallback_answer(user_text, outputs), None


def run_validation_copilot(
    user_text: str,
    registry: ToolRegistry,
    context: dict[str, Any],
    *,
    history: list[dict[str, str]] | None = None,
    model: str = "",
    base_url: str = "http://127.0.0.1:11434",
    timeout: float = 60.0,
) -> dict[str, Any]:
    plan = plan_validation_request(
        user_text,
        registry,
        context,
        history=history,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )
    if plan.get("needs_clarification"):
        return {
            "answer": plan.get("clarification") or "Puoi specificare meglio cosa vuoi verificare?",
            "plan": plan,
            "tool_outputs": [],
            "answer_error": None,
        }
    outputs = execute_plan(plan, registry, context)
    answer, answer_error = compose_answer(
        user_text,
        outputs,
        history=history,
        model=model if plan.get("engine") == "ollama" else "",
        base_url=base_url,
        timeout=timeout,
    )
    return {
        "answer": answer,
        "plan": plan,
        "tool_outputs": outputs,
        "answer_error": answer_error,
    }


def append_audit_log(path: str | Path, payload: dict[str, Any]) -> None:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now().isoformat(timespec="seconds"), **payload}
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        # L'audit non deve bloccare il flusso operativo.
        pass

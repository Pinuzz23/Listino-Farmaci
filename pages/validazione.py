from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.aifa import get_aifa_status, update_aifa_database
from modules.editor import (
    META_DELETE,
    META_ROW,
    META_STATUS,
    diff_editor_frames,
    issue_rows,
    merge_editor_dataframe,
    to_editor_dataframe,
)
from modules.excel_reader import read_workbook
from modules.pipeline import process_workbook
from modules.catalog_db import init_db
from modules.publisher import preview as preview_catalogue_publication, publish as publish_to_catalogue
from modules.reporter import build_normalized_workbook, build_validation_report
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles, status_banner
from modules.llm_assistant import ollama_status
from modules.validation_agent import append_audit_log, run_validation_copilot
from modules.change_set import change_set_to_audit_rows, simulate_change_set
from modules.validation_tools import build_validation_registry


inject_styles()

schema = load_schema()
aifa_dir = schema.get("aifa", {}).get("data_dir", "data/aifa")
catalogue_cfg = schema.get("catalogue", {})
db_path = catalogue_cfg.get("db_path", "data/listino.db")
archive_dir = catalogue_cfg.get("archive_dir", "archive")
backup_dir = catalogue_cfg.get("backup_dir", "backup")
assistant_cfg = schema.get("assistant", {})
ollama_url = assistant_cfg.get("ollama_url", "http://127.0.0.1:11434")
llm_timeout = float(assistant_cfg.get("timeout_seconds", 60))
validation_registry = build_validation_registry()
init_db(db_path)
status = get_aifa_status(aifa_dir)

hero(
    "Validatore Listino Farmaci",
    "Wave 2 Demo R9 · Validation Copilot WRITE controllati, Tool Registry 2.0, AIFA e pubblicazione locale",
)


def _file_id(uploaded_file) -> str:
    return sha256(uploaded_file.getvalue()).hexdigest()[:20]


def _dedupe_records(records: list[dict], keys: tuple[str, ...]) -> list[dict]:
    seen = set()
    result = []
    for item in records:
        key = tuple(str(item.get(k, "")) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _set_session_from_bundle(file_id: str, bundle: dict, reset_audit: bool = True):
    data = bundle["data"]
    result = bundle["result"]
    st.session_state["v10_file_id"] = file_id
    st.session_state["v10_working_records"] = deepcopy(data.records)
    st.session_state["v10_working_source_rows"] = list(data.source_rows)
    st.session_state["v10_revision"] = st.session_state.get("v10_revision", 0) + 1

    if reset_audit:
        st.session_state["v10_operator_changes"] = []
        st.session_state["v10_normalization_history"] = list(result.get("transformations", []))
        st.session_state["v10_aifa_enrichment_history"] = list(result.get("aifa_enrichments", []))
        st.session_state["v10_undo_stack"] = []
        st.session_state.pop("v10_pending_change_set", None)
        st.session_state.pop("v10_pending_simulation", None)
    else:
        st.session_state.setdefault("v10_operator_changes", [])
        st.session_state.setdefault("v10_normalization_history", [])
        st.session_state.setdefault("v10_aifa_enrichment_history", [])
        st.session_state.setdefault("v10_undo_stack", [])


def _push_ai_undo_snapshot(change_set_id: str):
    stack = list(st.session_state.get("v10_undo_stack", []))
    stack.append({
        "change_set_id": change_set_id,
        "working_records": deepcopy(st.session_state.get("v10_working_records", [])),
        "working_source_rows": list(st.session_state.get("v10_working_source_rows", [])),
        "operator_changes": deepcopy(st.session_state.get("v10_operator_changes", [])),
        "normalization_history": deepcopy(st.session_state.get("v10_normalization_history", [])),
        "aifa_enrichment_history": deepcopy(st.session_state.get("v10_aifa_enrichment_history", [])),
        "revision": st.session_state.get("v10_revision", 0),
    })
    st.session_state["v10_undo_stack"] = stack[-10:]


def _undo_last_ai_change() -> str | None:
    stack = list(st.session_state.get("v10_undo_stack", []))
    if not stack:
        return None
    snapshot = stack.pop()
    st.session_state["v10_undo_stack"] = stack
    st.session_state["v10_working_records"] = deepcopy(snapshot["working_records"])
    st.session_state["v10_working_source_rows"] = list(snapshot["working_source_rows"])
    st.session_state["v10_operator_changes"] = deepcopy(snapshot["operator_changes"])
    st.session_state["v10_normalization_history"] = deepcopy(snapshot["normalization_history"])
    st.session_state["v10_aifa_enrichment_history"] = deepcopy(snapshot["aifa_enrichment_history"])
    st.session_state["v10_revision"] = int(snapshot.get("revision", 0)) + 1
    st.session_state.pop("v10_pending_change_set", None)
    st.session_state.pop("v10_pending_simulation", None)
    return snapshot.get("change_set_id")


def _build_editor_config(schema: dict):
    config = {
        META_ROW: st.column_config.NumberColumn("Riga Excel", width="small", format="%d"),
        META_STATUS: st.column_config.TextColumn("Stato controlli", width="medium"),
        META_DELETE: st.column_config.CheckboxColumn(
            "Elimina riga",
            help="Spunta solo se la riga deve essere esclusa dal file validato.",
            default=False,
            width="small",
        ),
    }

    lists = schema.get("lists", {})
    for column in schema["columns"]:
        name = column["name"]
        kind = column.get("type", "string")
        help_text = "Campo obbligatorio" if column.get("required") else "Campo facoltativo"

        if kind == "list":
            options = list(lists.get(column.get("list_name"), []))
            if not column.get("required", False):
                options = [""] + options
            config[name] = st.column_config.SelectboxColumn(
                name,
                options=options,
                help=help_text,
                width="large" if name == "Stupefacente" else "medium",
            )
        elif kind == "percentage_list":
            options = [f"{float(v) * 100:g}%" for v in lists.get(column.get("list_name"), [])]
            config[name] = st.column_config.SelectboxColumn(
                name,
                options=options,
                help=help_text,
                width="small",
            )
        elif kind == "integer":
            config[name] = st.column_config.NumberColumn(
                name,
                min_value=column.get("min"),
                step=1,
                format="%d",
                help=help_text,
                width="small",
            )
        elif kind == "number":
            fmt = "%.2f" if name == "Prezzo Confezione" else "%.4f"
            config[name] = st.column_config.NumberColumn(
                name,
                min_value=column.get("min"),
                format=fmt,
                help=help_text,
                width="small",
            )
        elif kind == "identifier":
            config[name] = st.column_config.TextColumn(
                name,
                help="Inserire l'AIC a 9 cifre; gli zeri iniziali vengono preservati.",
                max_chars=9 if column.get("format") == "aic" else None,
                width="small",
            )
        else:
            width = "large" if name in {"Nome Commerciale", "Principio Attivo", "Note", "Fornitore"} else "medium"
            config[name] = st.column_config.TextColumn(name, help=help_text, width=width)

    return config


# --------------------------- SIDEBAR ---------------------------
with st.sidebar:
    st.subheader("Caricamento")
    template_path = Path(schema.get("template_reference", {}).get("bundled_name", "templates/Template_Tracciato_Master_R3.xlsx"))
    if template_path.exists():
        st.download_button(
            "⬇️ Scarica template master",
            data=template_path.read_bytes(),
            file_name="Template_Tracciato_Master_R3.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            help="Template ufficiale: riga 1 linee guida, riga 2 intestazioni, dati dalla riga 3.",
        )
    uploaded_file = st.file_uploader(
        "Tracciato Excel",
        type=["xlsx", "xlsm"],
        help="L'originale non viene mai modificato. Le correzioni restano in una copia di lavoro interna all'app.",
    )

    st.divider()
    st.subheader("Banca dati AIFA")
    if status.get("available"):
        if status.get("needs_refresh"):
            st.warning("Indice AIFA creato con una versione precedente: aggiornalo.")
        else:
            st.success("Indice locale disponibile")
        updated = (status.get("updated_at") or "-").replace("T", " ")
        st.caption(f"Aggiornato localmente: {updated}")
        st.caption(f"Farmaci indicizzati: {status.get('farmaci_rows', '-')}")
        if status.get("units_column"):
            st.caption(f"Campo unità rilevato: {status.get('units_column')}")
        elif status.get("packaging_column"):
            st.caption(f"Unità: fallback da confezione ({status.get('packaging_column')})")
    else:
        st.warning("Indice locale non ancora disponibile")

    if st.button("🔄 Aggiorna AIFA", use_container_width=True):
        with st.spinner("Scarico gli Open Data AIFA e aggiorno l'indice locale..."):
            try:
                update_aifa_database(aifa_dir)
                st.success("Banca dati aggiornata.")
                st.rerun()
            except Exception as exc:
                st.error("Aggiornamento non riuscito. Verificare internet o proxy aziendale.")
                st.exception(exc)

    st.divider()
    st.subheader("Opzioni AIFA")
    use_holder_as_supplier = st.toggle(
        "Usa Azienda titolare AIFA come Fornitore quando il campo è vuoto",
        value=False,
        help=(
            "L'Azienda titolare dell'AIC può essere diversa dal fornitore commerciale. "
            "Attiva questa opzione solo quando vuoi confermare esplicitamente questa equivalenza."
        ),
    )
    if use_holder_as_supplier:
        st.warning("Conferma operatore attiva per i Fornitori vuoti.")

    with st.expander("Regole del tracciato"):
        st.caption(
            "18 campi operativi attesi · controlli obbligatorietà · prezzi · AIC/Codice Fornitore · "
            "arricchimento AIFA · confronto UPC · correzione assistita con audit."
        )


# --------------------------- EMPTY STATE ---------------------------
if uploaded_file is None:
    st.subheader("Come funziona")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("### 1 · Carica")
        st.write("Importa il file Excel ricevuto. L'originale resta intatto.")
    with c2:
        st.markdown("### 2 · Controlla")
        st.write("Il tool normalizza, valida e riconcilia i campi disponibili con AIFA.")
    with c3:
        st.markdown("### 3 · Correggi")
        st.write("Modifica le celle direttamente nell'app con campi guidati e rivalidazione.")
    with c4:
        st.markdown("### 4 · Esporta")
        st.write("Il file validato diventa scaricabile solo quando non restano errori bloccanti.")
    st.info("Formato master: riga 1 linee guida, riga 2 intestazioni, dati dalla riga 3. È accettato anche il formato operativo con intestazioni direttamente in riga 1. Per i controlli AIFA aggiorna prima la banca dati locale dalla sidebar.")
    st.stop()


# --------------------------- INITIALIZATION ---------------------------
try:
    original_data = read_workbook(uploaded_file, schema)
    current_file_id = _file_id(uploaded_file)

    if st.session_state.get("v10_file_id") != current_file_id:
        initial_bundle = process_workbook(
            original_data,
            schema,
            aifa_dir,
            use_holder_as_supplier=use_holder_as_supplier,
        )
        _set_session_from_bundle(current_file_id, initial_bundle, reset_audit=True)
        st.session_state["v10_flash"] = "File caricato: è stata creata la copia di lavoro modificabile."

    working_data = replace(
        original_data,
        records=deepcopy(st.session_state.get("v10_working_records", [])),
        source_rows=list(st.session_state.get("v10_working_source_rows", original_data.source_rows)),
    )

    bundle = process_workbook(
        working_data,
        schema,
        aifa_dir,
        use_holder_as_supplier=use_holder_as_supplier,
    )
    result = bundle["result"]
    enriched_data = bundle["data"]
    aifa_candidates = bundle["aifa_candidates"]

    # Mantiene la cronologia di normalizzazioni e arricchimenti anche dopo le correzioni.
    current_transforms = list(result.get("transformations", []))
    current_enrichments = list(result.get("aifa_enrichments", []))
    transformation_history = _dedupe_records(
        list(st.session_state.get("v10_normalization_history", [])) + current_transforms,
        ("Riga Excel", "Campo", "Valore originale", "Valore normalizzato", "Motivo"),
    )
    enrichment_history = _dedupe_records(
        list(st.session_state.get("v10_aifa_enrichment_history", [])) + current_enrichments,
        ("Riga Excel", "Campo", "Valore originale", "Valore AIFA", "AIC", "Metodo"),
    )
    result["transformations"] = transformation_history
    result["aifa_enrichments"] = enrichment_history
    result["operator_changes"] = list(st.session_state.get("v10_operator_changes", []))
    result["normalized_records"] = enriched_data.records

    summary = result["summary"]
    summary["normalization_count"] = len(transformation_history)
    summary["aifa_enrichment_count"] = len(enrichment_history)
    by_field = {}
    for item in enrichment_history:
        field = item.get("Campo", "")
        by_field[field] = by_field.get(field, 0) + 1
    summary["aifa_enrichment_by_field"] = by_field

except Exception as exc:
    st.error("Il file non può essere interpretato secondo il tracciato atteso.")
    st.exception(exc)
    st.stop()


if st.session_state.get("v10_flash"):
    st.success(st.session_state.pop("v10_flash"))

status_banner(result["is_valid"], result["blocking_count"], result["warning_count"])

# --------------------------- TOP KPIs ---------------------------
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Articoli", summary["rows_count"])
k2.metric("Match AIFA", summary["aifa_exact_matches"])
k3.metric("Modifiche operatore", len(result.get("operator_changes", [])))
k4.metric("Errori bloccanti", result["blocking_count"])
k5.metric("Warning", result["warning_count"])
k6.metric("UPC da verificare", summary["aifa_upc_warning"])

# --------------------------- MAIN NAV ---------------------------
tab_overview, tab_copilot, tab_aifa, tab_edit, tab_issues, tab_data, tab_export = st.tabs([
    "📊 Panoramica",
    "🤖 Copilot Validazione",
    "🏛️ Verifica AIFA",
    "✏️ Correggi",
    "⚠️ Anomalie",
    "🧾 Dati",
    "⬇️ Esporta",
])

with tab_overview:
    st.subheader("Sintesi del caricamento")
    left, right = st.columns([1.3, 1])

    with left:
        a, b, c, d = st.columns(4)
        a.metric("Con AIC", summary["aic_count"])
        b.metric("Principi attivi", summary["active_count"])
        c.metric("ATC7 presenti", summary["atc_count"])
        d.metric("Normalizzazioni", summary["normalization_count"])

        e, f, g, h = st.columns(4)
        e.metric("Nome recuperati", summary["aifa_enrichment_by_field"].get("Nome Commerciale", 0))
        f.metric("Principi recuperati", summary["aifa_enrichment_by_field"].get("Principio Attivo", 0))
        g.metric("ATC7 recuperati", summary["aifa_enrichment_by_field"].get("ATC7", 0))
        h.metric("Correzioni manuali", len(result.get("operator_changes", [])))

        st.markdown("#### Stato controlli")
        checks = [
            {
                "Controllo": "Struttura e campi obbligatori",
                "Esito": "OK" if result["blocking_count"] == 0 else "DA CORREGGERE",
                "Dettaglio": f"{result['blocking_count']} errori bloccanti",
            },
            {
                "Controllo": "Match anagrafico AIFA",
                "Esito": "OK" if summary["aifa_missing_matches"] == 0 else "DA VERIFICARE",
                "Dettaglio": f"{summary['aifa_exact_matches']} AIC riconosciuti · {summary['aifa_missing_matches']} non trovati",
            },
            {
                "Controllo": "Arricchimento anagrafico AIFA",
                "Esito": "OK" if summary["without_name_count"] == 0 else "DA COMPLETARE",
                "Dettaglio": f"{summary['aifa_enrichment_count']} arricchimenti AIFA registrati; Principio Attivo incluso nel tracciato",
            },
            {
                "Controllo": "Coerenza gruppo di stivaggio",
                "Esito": "OK" if not any(i.get("Codice Errore") == "GRUPPO_STIVAGGIO_NON_COHERENTE" for i in result["issues"]) else "DA CORREGGERE",
                "Dettaglio": f"{sum(1 for i in result['issues'] if i.get('Codice Errore') == 'GRUPPO_STIVAGGIO_NON_COHERENTE')} incoerenze rilevate",
            },
            {
                "Controllo": "UPC vs Unità AIFA",
                "Esito": "OK" if summary["aifa_upc_warning"] == 0 else "DA VERIFICARE",
                "Dettaglio": f"{summary['aifa_upc_ok']} coerenti · {summary['aifa_upc_warning']} difformi · {summary['aifa_upc_unchecked']} non confrontabili",
            },
        ]
        st.dataframe(pd.DataFrame(checks), hide_index=True, use_container_width=True)

    with right:
        st.markdown("#### Identificazione fornitore")
        if summary["supplier_count"] == 1:
            st.success(summary["supplier"])
        elif summary["supplier_count"] > 1:
            st.warning("Sono presenti più fornitori nel dataset finale")
            st.write(" · ".join(summary["suppliers"]))
        else:
            st.error("Fornitore non rilevato")

        if result.get("supplier_proposals") and not use_holder_as_supplier:
            st.info(
                f"AIFA ha individuato {len(result['supplier_proposals'])} Aziende titolari utilizzabili come proposta."
            )

        st.markdown("#### Qualità dataset finale")
        st.write(f"**Nomi commerciali mancanti:** {summary['without_name_count']}")
        st.write(f"**Principi attivi AIFA non disponibili:** {summary['without_active_count']}")
        st.write(f"**ATC7 mancanti:** {summary['without_atc_count']}")
        st.write(f"**Articoli senza AIC:** {summary['without_aic_count']}")

        if result["is_valid"]:
            st.success("Il dataset è esportabile come tracciato validato.")
        else:
            st.warning("Apri la scheda Correggi per risolvere gli errori bloccanti.")

with tab_copilot:
    st.subheader("Copilot di validazione")
    st.caption(
        "Il Copilot usa il Tool Registry 2.0 per analizzare il file e generare Change Set di correzione. "
        "Le proposte non modificano il dataset finché l'operatore non le simula, seleziona e conferma esplicitamente."
    )

    @st.cache_data(ttl=10, show_spinner=False)
    def _validation_ollama_status(url: str):
        return ollama_status(url, timeout=2.0)

    llm_status = _validation_ollama_status(ollama_url)
    llm_models = llm_status.get("models", [])

    controls_left, controls_right = st.columns([1, 2])
    with controls_left:
        if llm_status.get("available") and llm_models:
            validation_model = st.selectbox(
                "Modello locale",
                llm_models,
                key="validation_copilot_model",
                help="Il modello pianifica quali tool di validazione chiamare e formula la risposta sui risultati ottenuti.",
            )
            use_llm_validation = st.toggle(
                "Usa LLM locale",
                value=True,
                key="validation_copilot_use_llm",
            )
        else:
            validation_model = ""
            use_llm_validation = False
            st.warning("Ollama non disponibile: il Copilot usa il planner deterministico di fallback.")
    with controls_right:
        st.info(
            "🔒 **Guardrail R9:** il modello può scegliere tool READ e GENERATE, ma non può invocare tool WRITE. "
            "I tool GENERATE producono solo Change Set. L'applicazione avviene esclusivamente dopo anteprima, simulazione e conferma dell'operatore."
        )

    validation_context = {
        "result": result,
        "records": enriched_data.records,
        "source_rows": enriched_data.source_rows,
        "schema": schema,
        "aifa_candidates": aifa_candidates,
        "workbook": enriched_data,
        "aifa_dir": aifa_dir,
        "use_holder_as_supplier": use_holder_as_supplier,
    }

    st.markdown("#### Domande rapide")
    qa1, qa2, qa3, qa4, qa5 = st.columns(5)
    quick_prompts = [
        (qa1, "Perché non passa?", "Perché questo file non passa la validazione?"),
        (qa2, "Da dove comincio?", "Da dove comincio a correggere? Fammi un piano in ordine di priorità."),
        (qa3, "Errori bloccanti", "Mostrami gli errori bloccanti e le righe coinvolte."),
        (qa4, "Verifica AIFA", "Cosa ha trovato AIFA e quali elementi devo ancora verificare?"),
        (qa5, "Correggi ciò che puoi", "Correggi automaticamente tutto ciò che puoi in modo deterministico e prepara una proposta."),
    ]

    copilot_key = f"validation_copilot_messages_{current_file_id}"
    st.session_state.setdefault(copilot_key, [])

    def _run_copilot_prompt(prompt_text: str):
        prompt_text = (prompt_text or "").strip()
        if not prompt_text:
            return
        chosen_model = validation_model if use_llm_validation else ""
        recent_history = []
        for message in st.session_state.get(copilot_key, [])[-8:]:
            if message.get("role") == "user":
                recent_history.append({"role": "user", "content": str(message.get("text", ""))})
            else:
                payload = message.get("payload") or {}
                recent_history.append({"role": "assistant", "content": str(payload.get("answer", ""))})
        response = run_validation_copilot(
            prompt_text,
            validation_registry,
            validation_context,
            history=recent_history,
            model=chosen_model,
            base_url=ollama_url,
            timeout=llm_timeout,
        )
        st.session_state[copilot_key].append({"role": "user", "text": prompt_text})
        st.session_state[copilot_key].append({"role": "assistant", "payload": response})
        for tool_output in response.get("tool_outputs", []):
            generated = tool_output.get("result") if tool_output.get("ok") else None
            if isinstance(generated, dict) and generated.get("change_set_id") and int(generated.get("count", 0)) > 0:
                st.session_state["v10_pending_change_set"] = generated
                st.session_state.pop("v10_pending_simulation", None)
        append_audit_log(
            "logs/validation_ai_audit.jsonl",
            {
                "file_id": current_file_id,
                "question": prompt_text,
                "model": chosen_model or "fallback",
                "plan": response.get("plan"),
                "tools": [
                    {"tool": item.get("tool"), "arguments": item.get("arguments"), "ok": item.get("ok")}
                    for item in response.get("tool_outputs", [])
                ],
            },
        )

    for col, label, quick_prompt in quick_prompts:
        with col:
            if st.button(label, use_container_width=True, key=f"validation_quick_{current_file_id}_{label}"):
                _run_copilot_prompt(quick_prompt)
                st.rerun()

    st.divider()

    for idx, message in enumerate(st.session_state[copilot_key]):
        if message.get("role") == "user":
            with st.chat_message("user"):
                st.write(message.get("text", ""))
            continue
        payload = message.get("payload") or {}
        with st.chat_message("assistant"):
            st.markdown(payload.get("answer") or "Nessuna risposta disponibile.")
            with st.expander("Tool utilizzati", expanded=False):
                plan = payload.get("plan") or {}
                st.caption(f"Planner: {plan.get('engine', '-')}; modello: {plan.get('model', 'fallback')}")
                if plan.get("planner_error"):
                    st.warning(f"Planner LLM non disponibile, usato fallback: {plan['planner_error']}")
                for tool_idx, tool_output in enumerate(payload.get("tool_outputs", []), start=1):
                    tool_name = tool_output.get("tool", "-")
                    st.markdown(f"**{tool_idx}. {tool_name}**")
                    st.json({
                        "arguments": tool_output.get("arguments", {}),
                        "ok": tool_output.get("ok", False),
                        "result": tool_output.get("result") if tool_output.get("ok") else tool_output.get("error"),
                    })
                if payload.get("answer_error"):
                    st.caption(f"Risposta LLM non disponibile; usata risposta deterministica. Dettaglio: {payload['answer_error']}")

    pending_change_set = st.session_state.get("v10_pending_change_set")
    if pending_change_set:
        st.divider()
        st.markdown("#### 🧩 Change Set in attesa di approvazione")
        risk_counts = pending_change_set.get("risk_counts") or {}
        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("Modifiche proposte", pending_change_set.get("count", 0))
        pc2.metric("Safe", risk_counts.get("safe", 0))
        pc3.metric("Controllate", risk_counts.get("controlled", 0))
        pc4.metric("Sensibili", risk_counts.get("sensitive", 0))
        st.caption(
            f"{pending_change_set.get('change_set_id')} · {pending_change_set.get('title', '')}. "
            "Nessuna modifica è ancora stata applicata."
        )

        changes = pending_change_set.get("changes", []) or []
        selection_rows = []
        for item in changes:
            selection_rows.append({
                "Applica": item.get("Rischio") != "sensitive",
                "change_id": item.get("change_id"),
                "Riga Excel": item.get("Riga Excel"),
                "Campo": item.get("Campo"),
                "Prima": item.get("Valore precedente"),
                "Dopo": item.get("Valore nuovo"),
                "Fonte": item.get("Fonte"),
                "Confidence": item.get("Confidence"),
                "Rischio": item.get("Rischio"),
                "Motivo": item.get("Motivo"),
            })
        selection_df = pd.DataFrame(selection_rows)
        reviewed_df = st.data_editor(
            selection_df,
            hide_index=True,
            use_container_width=True,
            disabled=["change_id", "Riga Excel", "Campo", "Prima", "Dopo", "Fonte", "Confidence", "Rischio", "Motivo"],
            column_config={
                "Applica": st.column_config.CheckboxColumn("Applica", help="Seleziona le modifiche da includere nel Change Set applicato."),
                "change_id": st.column_config.TextColumn("ID", width="small"),
                "Riga Excel": st.column_config.NumberColumn("Riga", format="%d", width="small"),
                "Campo": st.column_config.TextColumn("Campo", width="medium"),
                "Prima": st.column_config.TextColumn("Prima", width="medium"),
                "Dopo": st.column_config.TextColumn("Dopo", width="medium"),
                "Rischio": st.column_config.TextColumn("Rischio", width="small"),
                "Motivo": st.column_config.TextColumn("Motivo", width="large"),
            },
            key=f"pending_cs_editor_{current_file_id}_{pending_change_set.get('change_set_id')}",
        )
        selected_ids = set(reviewed_df.loc[reviewed_df["Applica"] == True, "change_id"].astype(str).tolist())
        selected_changes = [item for item in changes if str(item.get("change_id")) in selected_ids]
        has_sensitive = any(item.get("Rischio") == "sensitive" for item in selected_changes)

        sensitive_confirmed = True
        if has_sensitive:
            sensitive_confirmed = st.checkbox(
                "Confermo di aver verificato le modifiche sensibili selezionate",
                value=False,
                key=f"confirm_sensitive_{current_file_id}_{pending_change_set.get('change_set_id')}",
            )

        bsim, bapply, bdiscard, bund = st.columns([1.1, 1.3, 1, 1.2])
        with bsim:
            simulate_clicked = st.button(
                "🧪 Simula impatto",
                use_container_width=True,
                disabled=not selected_ids,
                key=f"simulate_cs_{current_file_id}_{pending_change_set.get('change_set_id')}",
            )
        with bapply:
            apply_ai_clicked = st.button(
                "✅ Applica selezionate",
                type="primary",
                use_container_width=True,
                disabled=(not selected_ids or not sensitive_confirmed),
                key=f"apply_cs_{current_file_id}_{pending_change_set.get('change_set_id')}",
            )
        with bdiscard:
            discard_clicked = st.button(
                "🗑️ Scarta",
                use_container_width=True,
                key=f"discard_cs_{current_file_id}_{pending_change_set.get('change_set_id')}",
            )
        with bund:
            undo_stack = st.session_state.get("v10_undo_stack", [])
            undo_clicked = st.button(
                "↩️ Annulla ultimo AI",
                use_container_width=True,
                disabled=not undo_stack,
                key=f"undo_ai_{current_file_id}_{pending_change_set.get('change_set_id')}",
            )

        if simulate_clicked:
            simulation = simulate_change_set(
                enriched_data,
                pending_change_set,
                schema,
                aifa_dir,
                use_holder_as_supplier=use_holder_as_supplier,
                selected_ids=selected_ids,
            )
            st.session_state["v10_pending_simulation"] = {
                "change_set_id": pending_change_set.get("change_set_id"),
                "selected_ids": sorted(selected_ids),
                "after": simulation.get("after"),
                "applied_count": simulation.get("applied_count"),
            }

        simulation_view = st.session_state.get("v10_pending_simulation")
        if simulation_view and simulation_view.get("change_set_id") == pending_change_set.get("change_set_id"):
            after = simulation_view.get("after") or {}
            st.markdown("##### Impatto simulato")
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Errori bloccanti", after.get("blocking", 0), delta=after.get("blocking", 0) - result.get("blocking_count", 0), delta_color="inverse")
            s2.metric("Warning", after.get("warnings", 0), delta=after.get("warnings", 0) - result.get("warning_count", 0), delta_color="inverse")
            s3.metric("Modifiche simulate", simulation_view.get("applied_count", 0))
            s4.metric("Esito", "VALIDATO" if after.get("is_valid") else "NON VALIDATO")

        if apply_ai_clicked:
            apply_result = validation_registry.execute(
                "validation_apply_change_set",
                validation_context,
                {"change_set": pending_change_set, "selected_ids": sorted(selected_ids)},
                allowed_access={"write"},
                confirmed=True,
            )
            if not apply_result.get("ok"):
                st.error("Il Change Set non può essere applicato perché il dataset è cambiato dopo la proposta.")
                st.json(apply_result.get("errors", []))
            else:
                _push_ai_undo_snapshot(pending_change_set.get("change_set_id", ""))
                updated_workbook = apply_result["workbook"]
                applied_changes = apply_result.get("applied", [])
                new_bundle = process_workbook(
                    updated_workbook,
                    schema,
                    aifa_dir,
                    use_holder_as_supplier=use_holder_as_supplier,
                )
                st.session_state["v10_working_records"] = deepcopy(new_bundle["data"].records)
                st.session_state["v10_working_source_rows"] = list(new_bundle["data"].source_rows)
                ai_audit_rows = change_set_to_audit_rows(pending_change_set, applied_changes)
                st.session_state["v10_operator_changes"] = (
                    list(st.session_state.get("v10_operator_changes", [])) + ai_audit_rows
                )
                st.session_state["v10_normalization_history"] = _dedupe_records(
                    list(st.session_state.get("v10_normalization_history", []))
                    + list(new_bundle["result"].get("transformations", [])),
                    ("Riga Excel", "Campo", "Valore originale", "Valore normalizzato", "Motivo"),
                )
                st.session_state["v10_aifa_enrichment_history"] = _dedupe_records(
                    list(st.session_state.get("v10_aifa_enrichment_history", []))
                    + list(new_bundle["result"].get("aifa_enrichments", [])),
                    ("Riga Excel", "Campo", "Valore originale", "Valore AIFA", "AIC", "Metodo"),
                )
                st.session_state["v10_revision"] = st.session_state.get("v10_revision", 0) + 1
                st.session_state.pop("v10_pending_change_set", None)
                st.session_state.pop("v10_pending_simulation", None)
                append_audit_log(
                    "logs/validation_ai_audit.jsonl",
                    {
                        "file_id": current_file_id,
                        "event": "APPLY_CHANGE_SET",
                        "change_set_id": pending_change_set.get("change_set_id"),
                        "selected_ids": sorted(selected_ids),
                        "applied_count": len(applied_changes),
                    },
                )
                st.session_state["v10_flash"] = (
                    f"Change Set {pending_change_set.get('change_set_id')} applicato: "
                    f"{len(applied_changes)} modifiche. Errori bloccanti residui: {new_bundle['result']['blocking_count']}."
                )
                st.rerun()

        if discard_clicked:
            append_audit_log(
                "logs/validation_ai_audit.jsonl",
                {"file_id": current_file_id, "event": "DISCARD_CHANGE_SET", "change_set_id": pending_change_set.get("change_set_id")},
            )
            st.session_state.pop("v10_pending_change_set", None)
            st.session_state.pop("v10_pending_simulation", None)
            st.rerun()

        if undo_clicked:
            undone = _undo_last_ai_change()
            if undone:
                append_audit_log(
                    "logs/validation_ai_audit.jsonl",
                    {"file_id": current_file_id, "event": "UNDO_CHANGE_SET", "change_set_id": undone},
                )
                st.session_state["v10_flash"] = f"Ripristinato lo stato precedente al Change Set {undone}."
                st.rerun()

    elif st.session_state.get("v10_undo_stack"):
        st.divider()
        if st.button("↩️ Annulla ultima modifica AI", key=f"undo_ai_no_pending_{current_file_id}"):
            undone = _undo_last_ai_change()
            if undone:
                st.session_state["v10_flash"] = f"Ripristinato lo stato precedente al Change Set {undone}."
                st.rerun()

    copilot_prompt = st.chat_input(
        "Chiedi al Copilot di analizzare o proporre correzioni...",
        key=f"validation_copilot_input_{current_file_id}",
    )
    if copilot_prompt:
        _run_copilot_prompt(copilot_prompt)
        st.rerun()

    if st.session_state[copilot_key]:
        if st.button("🗑️ Pulisci conversazione Copilot", key=f"clear_validation_copilot_{current_file_id}"):
            st.session_state[copilot_key] = []
            st.rerun()

    with st.expander("Registro tool disponibili", expanded=False):
        st.dataframe(
            pd.DataFrame(validation_registry.manifest()),
            hide_index=True,
            use_container_width=True,
        )

with tab_aifa:
    st.subheader("Riconciliazione anagrafica AIFA")
    st.caption(
        "Con AIC esatto il tool può completare Nome Commerciale, Principio Attivo e ATC7 quando mancanti e confrontare i valori già presenti. "
        "L'Azienda titolare AIFA resta distinta dal Fornitore commerciale, salvo conferma esplicita."
    )

    if not status.get("available"):
        st.warning("Aggiorna la banca dati AIFA dalla sidebar per abilitare i confronti.")
    else:
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("AIC riconosciuti", summary["aifa_exact_matches"])
        m2.metric("Arricchimenti registrati", summary["aifa_enrichment_count"])
        m3.metric("Proposte Fornitore", summary["supplier_proposal_count"])
        m4.metric("UPC coerenti", summary["aifa_upc_ok"])
        m5.metric("UPC da verificare", summary["aifa_upc_warning"])

    st.markdown("#### Anagrafica AIFA per AIC")
    if result.get("aifa_checks"):
        st.dataframe(pd.DataFrame(result["aifa_checks"]), hide_index=True, use_container_width=True)
    else:
        st.info("Nessun match AIFA eseguibile per questo file.")

    st.markdown("#### UPC vs Unità Posologiche / Confezione")
    if result.get("aifa_upc_checks"):
        st.dataframe(pd.DataFrame(result["aifa_upc_checks"]), hide_index=True, use_container_width=True)
    else:
        st.info("Nessun confronto UPC/AIFA disponibile per questo file.")

    with st.expander(f"Arricchimenti AIFA registrati ({len(result.get('aifa_enrichments', []))})", expanded=False):
        if result.get("aifa_enrichments"):
            st.dataframe(pd.DataFrame(result["aifa_enrichments"]), hide_index=True, use_container_width=True)
        else:
            st.write("Nessun campo è stato completato automaticamente.")

    with st.expander(f"Proposte Fornitore ({len(result.get('supplier_proposals', []))})"):
        if result.get("supplier_proposals"):
            st.dataframe(pd.DataFrame(result["supplier_proposals"]), hide_index=True, use_container_width=True)
        else:
            st.write("Nessuna proposta fornitore necessaria.")

    with st.expander(f"Candidati AIFA da verificare senza AIC ({len(aifa_candidates)})"):
        if aifa_candidates:
            st.dataframe(pd.DataFrame(aifa_candidates), hide_index=True, use_container_width=True)
            st.warning("I candidati non vengono applicati automaticamente in assenza di un match AIC esatto.")
        else:
            st.write("Nessuna ricerca assistita aperta.")

with tab_edit:
    st.subheader("Correzione assistita del tracciato")
    st.caption(
        "Modifica la copia di lavoro direttamente nella griglia. Il file originale non viene toccato. "
        "Le modifiche diventano effettive solo dopo aver premuto “Applica modifiche e rivalida”."
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Errori bloccanti", result["blocking_count"])
    c2.metric("Warning", result["warning_count"])
    c3.metric("Righe attive", len(enriched_data.records))
    c4.metric("Modifiche registrate", len(result.get("operator_changes", [])))

    filter_mode = st.radio(
        "Righe da mostrare",
        options=["Tutte", "Solo errori bloccanti", "Errori + warning"],
        index=1 if result["blocking_count"] else 0,
        horizontal=True,
        key=f"v10_filter_{current_file_id}",
    )

    only_rows = None
    if filter_mode == "Solo errori bloccanti":
        only_rows = issue_rows(result["issues"], {"BLOCCANTE"})
    elif filter_mode == "Errori + warning":
        only_rows = issue_rows(result["issues"], {"BLOCCANTE", "WARNING"})

    before_editor_df = to_editor_dataframe(
        enriched_data,
        schema,
        issues=result["issues"],
        only_rows=only_rows,
    )

    if before_editor_df.empty:
        st.success("Nessuna riga corrisponde al filtro selezionato.")
        edited_df = before_editor_df
    else:
        st.info(
            "Suggerimento: usa i menu a tendina per i campi codificati. Per eliminare una riga errata, "
            "spunta “Elimina riga” e poi rivalida. Regole stivaggio: S7 NARCOTICI/S8 CANNABINOIDI → "
            "STUPEF_Stupefacenti; temperature fredde → FRIGO_Frigo; circa -20°C/congelatore → FREEZER_Freezer; "
            "condizioni standard 15-25°C/ambiente → STD_Standard."
        )
        revision = st.session_state.get("v10_revision", 0)
        edited_df = st.data_editor(
            before_editor_df,
            column_config=_build_editor_config(schema),
            disabled=[META_ROW, META_STATUS],
            hide_index=True,
            use_container_width=True,
            height=min(760, max(260, 72 + 36 * len(before_editor_df))),
            num_rows="fixed",
            key=f"v10_editor_{current_file_id}_{revision}_{filter_mode}",
        )

    action_left, action_mid, action_right = st.columns([1.3, 1, 1])
    with action_left:
        apply_clicked = st.button(
            "✅ Applica modifiche e rivalida",
            type="primary",
            use_container_width=True,
            disabled=before_editor_df.empty,
        )
    with action_mid:
        reset_clicked = st.button(
            "↩️ Ripristina file iniziale",
            use_container_width=True,
        )
    with action_right:
        if result["is_valid"]:
            st.success("Pronto per l'esportazione")
        else:
            st.warning("Correzioni ancora necessarie")

    if apply_clicked:
        changes = diff_editor_frames(before_editor_df, edited_df, schema)
        if not changes:
            st.session_state["v10_flash"] = "Nessuna modifica rilevata nella griglia."
            st.rerun()

        draft_data = merge_editor_dataframe(edited_df, enriched_data, schema)
        new_bundle = process_workbook(
            draft_data,
            schema,
            aifa_dir,
            use_holder_as_supplier=use_holder_as_supplier,
        )

        st.session_state["v10_working_records"] = deepcopy(new_bundle["data"].records)
        st.session_state["v10_working_source_rows"] = list(new_bundle["data"].source_rows)
        st.session_state["v10_operator_changes"] = (
            list(st.session_state.get("v10_operator_changes", [])) + changes
        )
        st.session_state["v10_normalization_history"] = _dedupe_records(
            list(st.session_state.get("v10_normalization_history", []))
            + list(new_bundle["result"].get("transformations", [])),
            ("Riga Excel", "Campo", "Valore originale", "Valore normalizzato", "Motivo"),
        )
        st.session_state["v10_aifa_enrichment_history"] = _dedupe_records(
            list(st.session_state.get("v10_aifa_enrichment_history", []))
            + list(new_bundle["result"].get("aifa_enrichments", [])),
            ("Riga Excel", "Campo", "Valore originale", "Valore AIFA", "AIC", "Metodo"),
        )
        st.session_state["v10_revision"] = st.session_state.get("v10_revision", 0) + 1
        new_blocking = new_bundle["result"]["blocking_count"]
        st.session_state["v10_flash"] = (
            f"Modifiche applicate e controlli ricalcolati. Errori bloccanti residui: {new_blocking}."
        )
        st.rerun()

    if reset_clicked:
        reset_bundle = process_workbook(
            original_data,
            schema,
            aifa_dir,
            use_holder_as_supplier=use_holder_as_supplier,
        )
        _set_session_from_bundle(current_file_id, reset_bundle, reset_audit=True)
        st.session_state["v10_flash"] = "Copia di lavoro ripristinata dal file iniziale."
        st.rerun()

    with st.expander(f"Cronologia modifiche operatore ({len(result.get('operator_changes', []))})"):
        if result.get("operator_changes"):
            st.dataframe(pd.DataFrame(result["operator_changes"]), hide_index=True, use_container_width=True)
        else:
            st.write("Nessuna modifica manuale applicata.")

with tab_issues:
    st.subheader("Anomalie e segnalazioni")
    issues_df = pd.DataFrame(result["issues"])
    if issues_df.empty:
        st.success("Nessuna anomalia rilevata.")
    else:
        f1, f2 = st.columns([1, 2])
        with f1:
            selected_levels = st.multiselect(
                "Livello",
                ["BLOCCANTE", "WARNING", "INFO"],
                default=["BLOCCANTE", "WARNING"],
            )
        with f2:
            search = st.text_input(
                "Cerca in campo, codice o descrizione",
                placeholder="es. UPC, AIC, ATC7, fornitore...",
            )

        filtered = issues_df[issues_df["Livello"].isin(selected_levels)]
        if search.strip():
            needle = search.strip().casefold()
            mask = filtered.astype(str).apply(
                lambda row: row.str.casefold().str.contains(needle, regex=False).any(), axis=1
            )
            filtered = filtered[mask]
        st.dataframe(filtered, hide_index=True, use_container_width=True)

with tab_data:
    st.subheader("Originale, copia di lavoro e dataset finale")
    t1, t2, t3, t4 = st.tabs([
        "File ricevuto",
        "Dataset interno arricchito",
        f"Normalizzazioni ({len(result.get('transformations', []))})",
        f"Modifiche operatore ({len(result.get('operator_changes', []))})",
    ])

    with t1:
        original_preview = pd.DataFrame(original_data.records)
        if not original_preview.empty:
            original_preview.insert(0, "Riga Excel", original_data.source_rows)
        st.dataframe(original_preview, hide_index=True, use_container_width=True)

    with t2:
        final_preview = pd.DataFrame(enriched_data.records)
        if not final_preview.empty:
            final_preview.insert(0, "Riga Excel", enriched_data.source_rows)
        st.dataframe(final_preview, hide_index=True, use_container_width=True)
        st.caption("Questa vista rappresenta il dataset finale dopo normalizzazione e arricchimento AIFA. L’export validato mantiene tutte le 18 colonne del master, incluso Principio Attivo.")

    with t3:
        if result.get("transformations"):
            st.dataframe(pd.DataFrame(result["transformations"]), hide_index=True, use_container_width=True)
        else:
            st.info("Nessuna normalizzazione registrata.")

    with t4:
        if result.get("operator_changes"):
            st.dataframe(pd.DataFrame(result["operator_changes"]), hide_index=True, use_container_width=True)
        else:
            st.info("Nessuna modifica manuale applicata.")

with tab_export:
    st.subheader("Esportazione")
    st.write(
        "Il report mantiene la tracciabilità di anomalie, AIFA, normalizzazioni e modifiche operatore. "
        "Il tracciato corretto diventa scaricabile solo quando non ci sono errori bloccanti."
    )

    report_bytes = build_validation_report(uploaded_file.name, result)
    st.download_button(
        "⬇️ Scarica report di validazione",
        data=report_bytes,
        file_name=f"Report_Validazione_{Path(uploaded_file.name).stem}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    if result["is_valid"]:
        validated_bytes = build_normalized_workbook(enriched_data, schema)
        st.success("✅ Validazione completata: il file corretto è pronto per la pubblicazione/archiviazione.")
        st.download_button(
            "⬇️ Scarica tracciato validato",
            data=validated_bytes,
            file_name=f"Tracciato_Validato_{Path(uploaded_file.name).stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

        st.divider()
        st.subheader("📤 Pubblica a listino")
        st.caption(
            "Prima della scrittura nel database locale viene mostrato il confronto con il listino corrente. "
            "La pubblicazione crea un batch tracciabile e mantiene lo storico prezzi."
        )

        pub_preview = preview_catalogue_publication(enriched_data.records, db_path)
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Nuovi prodotti", pub_preview["new_products"])
        p2.metric("Nuove offerte", pub_preview["new_offers"])
        p3.metric("Prezzi variati", pub_preview["price_changes"])
        p4.metric("Dati variati", pub_preview["data_changes"])
        p5.metric("Invariati", pub_preview["unchanged"])

        with st.expander("Anteprima righe da pubblicare", expanded=False):
            st.dataframe(pd.DataFrame(pub_preview["details"]), hide_index=True, use_container_width=True)

        if pub_preview.get("already_published"):
            previous = pub_preview["already_published"]
            st.warning(
                f"Questo dataset risulta già pubblicato nel batch **{previous['batch_id']}** "
                f"({previous['published_at']}). La demo impedisce una pubblicazione identica duplicata."
            )
        else:
            confirm_key = f"publish_confirm_{current_file_id}_{st.session_state.get('v10_revision', 0)}"
            confirmed = st.checkbox(
                "Confermo di voler pubblicare questo tracciato nel listino locale",
                key=confirm_key,
            )
            if st.button(
                "📤 Pubblica a listino",
                type="primary",
                use_container_width=True,
                disabled=not confirmed,
            ):
                try:
                    published = publish_to_catalogue(
                        records=enriched_data.records,
                        source_name=uploaded_file.name,
                        source_bytes=uploaded_file.getvalue(),
                        validated_bytes=validated_bytes,
                        report_bytes=report_bytes,
                        db_path=db_path,
                        archive_dir=archive_dir,
                        backup_dir=backup_dir,
                        app_version=schema.get("app_version", "demo"),
                    )
                    st.success(
                        f"Pubblicazione completata: **{published['batch_id']}** · "
                        f"{published['row_count']} righe caricate nel listino."
                    )
                    if published.get("archive_error"):
                        st.warning(
                            "I dati sono stati pubblicati, ma l'archiviazione dei file non è riuscita: "
                            + published["archive_error"]
                        )
                    else:
                        st.caption(f"Archivio batch: {published.get('archive_path', '-')}")
                    st.info("Apri la pagina **📦 Listino prodotti** dal menu laterale per vedere il risultato.")
                except Exception as exc:
                    st.error("Pubblicazione non completata.")
                    st.exception(exc)
    else:
        st.warning(
            f"Il tracciato validato non è ancora esportabile: restano {result['blocking_count']} errori bloccanti. "
            "Correggili nella scheda ✏️ Correggi."
        )

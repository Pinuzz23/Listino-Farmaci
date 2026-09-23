from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.assistant import (
    aggregate_request,
    apply_request,
    export_filename,
    interpretation_label,
    interpret_request,
    response_text,
)
from modules.catalog_db import (
    catalogue_dataframe,
    export_catalogue_excel,
    init_db,
    price_history_dataframe,
)
from modules.llm_assistant import interpret_request_llm, ollama_status
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles


inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
assistant_cfg = schema.get("assistant", {})
ollama_url = assistant_cfg.get("ollama_url", "http://127.0.0.1:11434")
llm_timeout = float(assistant_cfg.get("timeout_seconds", 60))
fallback_to_rules = bool(assistant_cfg.get("fallback_to_rules", True))
init_db(db_path)

hero(
    "Assistente Listino AI",
    "Interpreta richieste in linguaggio naturale con un LLM locale e le esegue solo dopo validazione sul listino pubblicato",
)

catalogue = catalogue_dataframe(db_path)
if catalogue.empty:
    st.info("Il listino è vuoto. Pubblica almeno un tracciato dalla pagina Validazione prima di usare l'assistente.")
    st.stop()


@st.cache_data(ttl=10, show_spinner=False)
def cached_ollama_status(url: str):
    return ollama_status(url, timeout=2.0)


status = cached_ollama_status(ollama_url)
models = status.get("models", [])

with st.sidebar:
    st.markdown("### Assistente AI")
    if status.get("available"):
        st.success("Ollama locale connesso")
        if models:
            selected_model = st.selectbox("Modello locale", models, key="assistant_llm_model")
        else:
            selected_model = ""
            st.warning("Ollama è attivo, ma non risultano modelli installati.")
    else:
        selected_model = ""
        st.warning("Ollama non raggiungibile: sarà disponibile il parser controllato di fallback.")

    engine_options = ["LLM locale (Ollama)", "Parser controllato"]
    default_engine = 0 if status.get("available") and models else 1
    engine = st.radio("Motore", engine_options, index=default_engine, key="assistant_engine_r5")

    with st.expander("Diagnostica", expanded=False):
        st.caption(f"Endpoint Ollama: {ollama_url}")
        if status.get("error"):
            st.caption(f"Dettaglio: {status['error']}")
        st.caption("Il modello riceve solo la frase dell'utente. Le righe del listino non vengono inviate al modello.")

st.info(
    "🔒 **Fail-safe attivo:** l'LLM interpreta la richiesta, ma i valori vengono poi verificati contro il database. "
    "Un fornitore o principio attivo inesistente blocca l'operazione. L'intero listino può essere esportato solo se richiesto esplicitamente."
)

with st.expander("Cosa puoi chiedere", expanded=False):
    st.markdown(
        """
        - `Scaricami il listino Pfizer`
        - `Scaricami il listino del fornitore Pfizer`
        - `Fornitore: Pfizer, esporta in Excel`
        - `Scaricami i prodotti con principio attivo Paracetamolo`
        - `Mostrami i prodotti in frigo con IVA 10% sotto 50 euro`
        - `Quanti prodotti ci sono di Pfizer?`
        - `Mostrami i prodotti ATC N02BE01`
        - `Storico prezzi dell'AIC 012745055`
        - `Estrai il prodotto con il prezzo unitario più alto`
        - `Mostrami i 10 prodotti con UPC più alto`
        - `Prodotti con minimo movimentabile maggiore di 5`
        - `Prodotti con Materiale Pericoloso = Y`
        - `Prodotti Fala / Lasa = Y`
        - `Scarica i prodotti con temperatura da + 2 a 8°C`
        - `Note contengono urgente`
        - `Qual è il prezzo confezione medio di Angelini?`
        - `Scarica tutto il listino` *(unico caso in cui un export senza criteri è ammesso)*
        """
    )

if "assistant_messages_r5" not in st.session_state:
    st.session_state.assistant_messages_r5 = []


def submit_prompt(prompt: str):
    prompt = (prompt or "").strip()
    if not prompt:
        return

    if engine == "LLM locale (Ollama)" and selected_model:
        request = interpret_request_llm(
            prompt,
            catalogue,
            model=selected_model,
            base_url=ollama_url,
            timeout=llm_timeout,
            fallback_to_rules=fallback_to_rules,
        )
    else:
        request = interpret_request(prompt, catalogue)
        request["engine"] = "rules"

    st.session_state.assistant_messages_r5.append({"role": "user", "text": prompt})
    st.session_state.assistant_messages_r5.append({"role": "assistant", "request": request})


st.markdown("#### Prova una richiesta")
q1, q2, q3, q4 = st.columns(4)
examples = [
    (q1, "Listino fornitore", "Scaricami il listino del fornitore Angelini"),
    (q2, "Prezzo unitario max", "Estrai il prodotto con il prezzo unitario più alto"),
    (q3, "Prodotti in frigo", "Mostrami i prodotti in frigo con IVA 10%"),
    (q4, "Conteggio", "Quanti prodotti sono presenti nel listino?"),
]
for col, label, prompt in examples:
    with col:
        if st.button(label, use_container_width=True, key=f"example_r5_{label}"):
            submit_prompt(prompt)
            st.rerun()

st.divider()

for idx, message in enumerate(st.session_state.assistant_messages_r5):
    if message["role"] == "user":
        with st.chat_message("user"):
            st.write(message["text"])
        continue

    request = message["request"]
    with st.chat_message("assistant"):
        if request.get("blocking_ambiguity"):
            st.markdown(response_text(request, pd.DataFrame()))

            suggestions = request.get("suggestions", {}) or {}
            for field, values in suggestions.items():
                if values:
                    st.caption(f"Possibili valori per {field}: " + " · ".join(values[:6]))

            clarification = request.get("clarification")
            if clarification:
                st.caption(f"Indicazione del modello: {clarification}")

            with st.expander("Interpretazione della richiesta"):
                st.write(f"**Motore:** {request.get('engine', 'rules')}")
                if request.get("llm_model"):
                    st.write(f"**Modello:** {request['llm_model']}")
                st.write(f"**Azione:** {request.get('action', 'search').upper()}")
                st.write(f"**Non risolto:** {', '.join(request.get('unresolved', []))}")
                if request.get("llm_error"):
                    st.warning(f"LLM non disponibile; usato fallback. Dettaglio: {request['llm_error']}")
            continue

        results = apply_request(catalogue, request)
        st.markdown(response_text(request, results))

        with st.expander("Interpretazione della richiesta", expanded=False):
            st.write(f"**Motore:** {request.get('engine', 'rules')}")
            if request.get("llm_model"):
                st.write(f"**Modello:** {request['llm_model']}")
            st.write(f"**Azione:** {request.get('action', 'search').upper()}")
            st.write(f"**Filtri:** {interpretation_label(request)}")
            if request.get("llm_error"):
                st.warning(f"LLM non disponibile; usato fallback. Dettaglio: {request['llm_error']}")

        action = request.get("action")
        if action == "aggregate":
            agg = aggregate_request(catalogue, request)
            if not agg or agg.get("value") is None:
                st.info("Non ci sono valori numerici disponibili per l'aggregazione richiesta.")
                continue
            field = agg["field"]
            function = agg["function"]
            value = agg["value"]
            label_map = {"max": "Massimo", "min": "Minimo", "avg": "Media", "sum": "Somma", "count_distinct": "Valori distinti"}
            if field == "IVA" and function != "count_distinct":
                display_value = f"{float(value) * 100:.2f}%"
            elif field in {"Prezzo Unitario", "Prezzo Confezione"} and function != "count_distinct":
                display_value = f"€ {float(value):,.4f}" if field == "Prezzo Unitario" else f"€ {float(value):,.2f}"
            elif function == "count_distinct":
                display_value = str(int(value))
            else:
                display_value = f"{float(value):,.2f}"
            st.metric(f"{label_map.get(function, function.upper())} — {field}", display_value)

            if function in {"max", "min"}:
                scoped = agg.get("rows", pd.DataFrame())
                numeric = pd.to_numeric(scoped[field], errors="coerce")
                extreme_rows = scoped[(numeric - float(value)).abs() < 1e-9]
                if not extreme_rows.empty:
                    cols = [c for c in ["AIC", "Nome Commerciale", "Principio Attivo", "Fornitore", field] if c in extreme_rows.columns]
                    st.dataframe(extreme_rows[cols], hide_index=True, use_container_width=True)
            continue

        if results.empty:
            continue

        if action == "history":
            max_offers = 8
            if len(results) > max_offers:
                st.warning(
                    f"La richiesta individua {len(results)} offerte. Mostro le prime {max_offers}; "
                    "aggiungi Fornitore o AIC per restringere la ricerca."
                )
            for _, row in results.head(max_offers).iterrows():
                title = f"{row.get('Nome Commerciale') or row.get('AIC') or 'Prodotto'} — {row.get('Fornitore') or '-'}"
                with st.expander(title, expanded=len(results) == 1):
                    hist = price_history_dataframe(db_path, int(row["offer_id"]))
                    if hist.empty:
                        st.caption("Nessuno storico prezzi disponibile per questa offerta.")
                    else:
                        st.dataframe(
                            hist,
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "Prezzo Unitario": st.column_config.NumberColumn(format="€ %.4f"),
                                "Prezzo Unitario": st.column_config.NumberColumn(format="€ %.4f"),
                    "Prezzo Confezione": st.column_config.NumberColumn(format="€ %.2f"),
                    "IVA": st.column_config.NumberColumn(format="%.2f"),
                                "IVA": st.column_config.NumberColumn(format="%.2f"),
                            },
                        )
            continue

        if action != "count":
            preview_cols = [
                "Fornitore", "AIC", "Codice Fornitore", "Nome Commerciale", "Principio Attivo",
                "Materiale Pericoloso", "Stupefacente", "ATC7", "ATC9", "Fala / Lasa",
                "Gruppo di Stivaggio", "Temperatura di Stivaggio", "Prezzo Unitario",
                "Prezzo Confezione", "UPC", "Minimo Movimentabile", "IVA", "Note",
            ]
            preview_cols = [c for c in preview_cols if c in results.columns]
            st.dataframe(
                results[preview_cols].head(100),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Prezzo Unitario": st.column_config.NumberColumn(format="€ %.4f"),
                    "Prezzo Confezione": st.column_config.NumberColumn(format="€ %.2f"),
                    "IVA": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            if len(results) > 100:
                st.caption(f"Anteprima limitata a 100 righe su {len(results)}.")

        # Never expose a broad download after a mere count/history request.
        # Export is always downloadable; filtered search results can also be downloaded.
        has_scope = bool(request.get("generic_filters") or request.get("filters") or request.get("sort"))
        allow_download = action == "export" or (action == "search" and has_scope)
        if allow_download:
            export_bytes = export_catalogue_excel(db_path, results)
            st.download_button(
                "⬇️ Scarica Excel",
                data=export_bytes,
                file_name=export_filename(request),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"assistant_export_r5_{idx}",
            )

prompt = st.chat_input("Scrivi una richiesta sul listino...")
if prompt:
    submit_prompt(prompt)
    st.rerun()

if st.session_state.assistant_messages_r5:
    if st.button("🗑️ Pulisci conversazione"):
        st.session_state.assistant_messages_r5 = []
        st.rerun()

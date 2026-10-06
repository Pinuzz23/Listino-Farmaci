from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from modules.auth import current_user, require_permission
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles
from modules.validity_r14 import (
    STATUS_ATTENTION,
    STATUS_CRITICAL,
    STATUS_EXPIRED,
    STATUS_MISSING,
    STATUS_REGULAR,
    ValidityError,
    parse_date,
    renew_product_validity,
    validity_history_dataframe,
    validity_monitor_dataframe,
)


require_permission("view_catalogue")

user = current_user() or {}
role_id = str(user.get("role_id") or "").upper()
if role_id not in {"BUYER", "ADMIN", "ORDER_MANAGEMENT"}:
    st.error("Il monitor validità è riservato agli utenti interni autorizzati.")
    st.stop()

inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")

hero(
    "Monitor validità farmaci",
    "Controllo dinamico della vita residua degli AIC e rinnovo della finestra di validità",
)

st.info(
    "La soglia critica scatta quando è stato consumato almeno **2/3 della vita residua** "
    "calcolata dalla prima immissione della specifica Data Validità Farmaco. "
    "Una ripubblicazione con la stessa data non azzera il conteggio; un vero rinnovo "
    "con data successiva apre una nuova finestra."
)

try:
    df = validity_monitor_dataframe(db_path)
except Exception as exc:
    st.error("Non è stato possibile caricare il monitor validità.")
    with st.expander("Dettaglio tecnico"):
        st.code(f"{type(exc).__name__}: {exc}")
    st.stop()

if df.empty:
    st.info("Il catalogo non contiene prodotti da monitorare.")
    st.stop()

status_series = df["Stato validità"].fillna(STATUS_MISSING).astype(str)
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Regolari", int(status_series.eq(STATUS_REGULAR).sum()))
m2.metric("In attenzione", int(status_series.eq(STATUS_ATTENTION).sum()))
m3.metric("Critici ≥ 2/3", int(status_series.eq(STATUS_CRITICAL).sum()))
m4.metric("Scaduti", int(status_series.eq(STATUS_EXPIRED).sum()))
m5.metric("Senza validità", int(status_series.eq(STATUS_MISSING).sum()))

status_icon = {
    STATUS_REGULAR: "🟢",
    STATUS_ATTENTION: "🟠",
    STATUS_CRITICAL: "🔴",
    STATUS_EXPIRED: "⚫",
    STATUS_MISSING: "⚪",
}

st.subheader("Ricerca e filtri")
f1, f2, f3 = st.columns([2.2, 1.2, 1.2])
with f1:
    search = st.text_input(
        "Cerca",
        placeholder="AIC, nome commerciale o fornitore...",
    )
with f2:
    status_options = [
        "Tutti",
        STATUS_CRITICAL,
        STATUS_EXPIRED,
        STATUS_ATTENTION,
        STATUS_REGULAR,
        STATUS_MISSING,
    ]
    status_filter = st.selectbox("Stato validità", status_options)
with f3:
    day_filter = st.selectbox(
        "Giorni residui",
        ["Tutti", "< 30 giorni", "< 60 giorni", "< 90 giorni"],
    )

filtered = df.copy()
if status_filter != "Tutti":
    filtered = filtered[filtered["Stato validità"] == status_filter]

if day_filter != "Tutti":
    limit = int(day_filter.split()[1])
    days = pd.to_numeric(filtered["Giorni residui"], errors="coerce")
    filtered = filtered[days.notna() & days.lt(limit)]

if search.strip():
    needle = search.strip().casefold()
    searchable = ["AIC", "Nome Commerciale", "Fornitori"]
    mask = (
        filtered[searchable]
        .fillna("")
        .astype(str)
        .apply(
            lambda row: row.str.casefold().str.contains(
                needle,
                regex=False,
            ).any(),
            axis=1,
        )
    )
    filtered = filtered[mask]

filtered = filtered.reset_index(drop=True)
filtered["Stato"] = filtered["Stato validità"].map(
    lambda value: f"{status_icon.get(str(value), '•')} {value}"
)

st.caption(f"Prodotti nel filtro corrente: **{len(filtered)}**")

show_cols = [
    "AIC",
    "Nome Commerciale",
    "Fornitori",
    "Data Validità Farmaco",
    "Giorni residui",
    "% vita consumata",
    "Soglia 2/3",
    "Stato",
]

selection = st.dataframe(
    filtered[show_cols],
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Giorni residui": st.column_config.NumberColumn(format="%d"),
        "% vita consumata": st.column_config.NumberColumn(format="%.1f%%"),
    },
)

selected_rows = (
    selection.selection.rows
    if selection and selection.selection
    else []
)

if not selected_rows:
    st.caption("Seleziona un prodotto per vedere il dettaglio e lo storico.")
    st.stop()

row = filtered.iloc[selected_rows[0]]
product_id = int(row["ID"])
current_validity = parse_date(row.get("Data Validità Farmaco"))
current_status = str(row.get("Stato validità") or STATUS_MISSING)

st.divider()
st.subheader(row.get("Nome Commerciale") or "Scheda prodotto")

a, b, c, d = st.columns(4)
with a:
    st.write(f"**AIC:** {row.get('AIC') or '-'}")
    st.write(f"**Fornitore/i:** {row.get('Fornitori') or '-'}")
with b:
    st.write(f"**Validità:** {current_validity.strftime('%d/%m/%Y') if current_validity else '-'}")
    st.write(f"**Data riferimento:** {row.get('Data riferimento') or '-'}")
with c:
    remaining = row.get("Giorni residui")
    pct = row.get("% vita consumata")
    st.write(f"**Giorni residui:** {int(remaining) if pd.notna(remaining) else '-'}")
    st.write(f"**Vita consumata:** {float(pct):.1f}%" if pd.notna(pct) else "**Vita consumata:** -")
with d:
    st.write(
        f"**Stato:** {status_icon.get(current_status, '•')} {current_status}"
    )
    st.write(f"**Soglia 2/3:** {row.get('Soglia 2/3') or '-'}")

if current_status == STATUS_CRITICAL:
    st.error(
        "Il prodotto ha superato la soglia dei 2/3. Se viene ripresentato con la stessa "
        "Data Validità Farmaco, la validazione lo considera bloccante."
    )
elif current_status == STATUS_ATTENTION:
    st.warning(
        "Il prodotto ha superato il 50% della finestra iniziale ed è in avvicinamento "
        "alla soglia critica."
    )
elif current_status == STATUS_EXPIRED:
    st.error("La Data Validità Farmaco è scaduta.")

if role_id in {"BUYER", "ADMIN"}:
    st.markdown("#### 🔄 Rinnovo validità")
    st.caption(
        "Il rinnovo richiede una data successiva a quella corrente. Alla conferma il "
        "conteggio riparte da zero, viene salvato lo storico e viene generato un delta "
        "ERP di tipo UPDATE."
    )

    minimum_date = date.today() + timedelta(days=1)
    if current_validity is not None:
        minimum_date = max(minimum_date, current_validity + timedelta(days=1))

    with st.form(f"renew_validity_{product_id}"):
        new_validity = st.date_input(
            "Nuova Data Validità Farmaco",
            value=minimum_date,
            min_value=minimum_date,
            format="DD/MM/YYYY",
        )
        reason = st.text_area(
            "Motivazione rinnovo",
            max_chars=1000,
            placeholder="Es. rinnovo autorizzazione / estensione validità commerciale.",
        )
        confirm = st.checkbox(
            "Confermo di voler aprire una nuova finestra di validità per questo prodotto."
        )
        submitted = st.form_submit_button(
            "🔄 Conferma rinnovo",
            type="primary",
            use_container_width=True,
            disabled=not confirm,
        )

    if submitted:
        try:
            result = renew_product_validity(
                product_id,
                new_validity,
                reason,
                db_path,
            )
        except ValidityError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error("Rinnovo non riuscito.")
            with st.expander("Dettaglio tecnico rinnovo"):
                st.code(f"{type(exc).__name__}: {exc}")
        else:
            st.success(
                f"Validità rinnovata al {new_validity.strftime('%d/%m/%Y')}. "
                f"Nuova finestra: {result['initial_days']} giorni. "
                f"Delta ERP: {result['source_ref']}."
            )
            st.rerun()
else:
    st.info("Il rinnovo è riservato ai profili Buyer e Admin.")

st.markdown("#### Storico validità")
try:
    history = validity_history_dataframe(product_id, db_path)
except Exception as exc:
    st.warning("Storico validità temporaneamente non disponibile.")
    with st.expander("Dettaglio tecnico storico"):
        st.code(f"{type(exc).__name__}: {exc}")
else:
    if history.empty:
        st.caption("Nessun evento di validità ancora registrato per questo prodotto.")
    else:
        labels = {
            "FIRST_LOAD": "Prima immissione",
            "TRACE_RENEWAL": "Rinnovo da tracciato",
            "BUYER_RENEWAL": "Rinnovo Buyer",
        }
        history["Evento"] = history["Evento"].map(
            lambda value: labels.get(str(value), str(value))
        )
        st.dataframe(history, hide_index=True, use_container_width=True)

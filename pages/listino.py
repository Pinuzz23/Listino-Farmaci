from datetime import datetime

import pandas as pd
import streamlit as st

from modules.catalog_db import (
    catalogue_dataframe,
    export_catalogue_excel,
    init_db,
    price_history_dataframe,
)
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles

inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
init_db(db_path)

hero(
    "Listino prodotti",
    "Vista consolidata delle offerte pubblicate — ricerca, filtri, dettaglio prodotto e storico prezzi",
)

df = catalogue_dataframe(db_path)
if df.empty:
    st.info("Il listino è vuoto. Pubblica il primo tracciato dalla pagina Validazione.")
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Righe listino", len(df))
m2.metric("Prodotti", df["product_id"].nunique())
m3.metric("Fornitori", df["Fornitore"].nunique())
m4.metric("Con AIC", int(df["AIC"].fillna("").astype(str).str.strip().ne("").sum()))

st.subheader("Ricerca e filtri")
f1, f2, f3, f4 = st.columns([2.2, 1.1, 1.1, 1.1])
with f1:
    search = st.text_input(
        "Cerca",
        placeholder="AIC, nome commerciale, principio attivo, ATC, codice fornitore...",
    )
with f2:
    suppliers = ["Tutti"] + sorted(df["Fornitore"].dropna().astype(str).unique().tolist())
    supplier = st.selectbox("Fornitore", suppliers)
with f3:
    groups = ["Tutti"] + sorted(df["Gruppo di Stivaggio"].dropna().astype(str).unique().tolist())
    group = st.selectbox("Stivaggio", groups)
with f4:
    narcotics = ["Tutti"] + sorted(df["Stupefacente"].dropna().astype(str).unique().tolist())
    narcotic = st.selectbox("Classificazione", narcotics)

filtered = df.copy()
if supplier != "Tutti":
    filtered = filtered[filtered["Fornitore"] == supplier]
if group != "Tutti":
    filtered = filtered[filtered["Gruppo di Stivaggio"] == group]
if narcotic != "Tutti":
    filtered = filtered[filtered["Stupefacente"] == narcotic]
if search.strip():
    needle = search.strip().casefold()
    searchable = [
        "AIC", "Nome Commerciale", "Principio Attivo", "ATC7", "ATC9",
        "Codice Fornitore", "Fornitore"
    ]
    mask = filtered[searchable].fillna("").astype(str).apply(
        lambda row: row.str.casefold().str.contains(needle, regex=False).any(), axis=1
    )
    filtered = filtered[mask]

st.caption(f"Risultati: **{len(filtered)}**")

show_cols = [
    "AIC", "Nome Commerciale", "Principio Attivo", "ATC7", "Fornitore",
    "Prezzo Confezione", "UPC", "Gruppo di Stivaggio", "Ultimo aggiornamento"
]
view = filtered[show_cols].copy()
view["Prezzo Confezione"] = pd.to_numeric(view["Prezzo Confezione"], errors="coerce")

selection = st.dataframe(
    view,
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Prezzo Confezione": st.column_config.NumberColumn(format="€ %.2f"),
    },
)

export_bytes = export_catalogue_excel(db_path, filtered)
st.download_button(
    "⬇️ Esporta listino filtrato",
    data=export_bytes,
    file_name=f"Listino_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

selected_rows = selection.selection.rows if selection and selection.selection else []
if selected_rows:
    selected_index = filtered.index[selected_rows[0]]
    row = filtered.loc[selected_index]
    st.divider()
    st.subheader(str(row["Nome Commerciale"] or "Scheda prodotto"))

    a, b, c = st.columns(3)
    with a:
        st.markdown("#### Anagrafica")
        st.write(f"**AIC:** {row['AIC'] or '-'}")
        st.write(f"**Principio Attivo:** {row['Principio Attivo'] or '-'}")
        st.write(f"**ATC7:** {row['ATC7'] or '-'}")
        st.write(f"**ATC9:** {row['ATC9'] or '-'}")
    with b:
        st.markdown("#### Commerciale")
        st.write(f"**Fornitore:** {row['Fornitore'] or '-'}")
        st.write(f"**Codice Fornitore:** {row['Codice Fornitore'] or '-'}")
        st.write(f"**Prezzo Unitario:** € {float(row['Prezzo Unitario'] or 0):,.4f}")
        st.write(f"**Prezzo Confezione:** € {float(row['Prezzo Confezione'] or 0):,.2f}")
        st.write(f"**IVA:** {float(row['IVA'] or 0) * 100:g}%")
    with c:
        st.markdown("#### Logistica")
        st.write(f"**UPC:** {row['UPC'] or '-'}")
        st.write(f"**Minimo movimentabile:** {row['Minimo Movimentabile'] or '-'}")
        st.write(f"**Gruppo:** {row['Gruppo di Stivaggio'] or '-'}")
        st.write(f"**Temperatura:** {row['Temperatura di Stivaggio'] or '-'}")
        st.write(f"**Stupefacente:** {row['Stupefacente'] or '-'}")

    st.markdown("#### Storico prezzi")
    history = price_history_dataframe(db_path, int(row["offer_id"]))
    if history.empty:
        st.caption("Nessuno storico disponibile.")
    else:
        st.dataframe(
            history,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Prezzo Unitario": st.column_config.NumberColumn(format="€ %.4f"),
                "Prezzo Confezione": st.column_config.NumberColumn(format="€ %.2f"),
                "IVA": st.column_config.NumberColumn(format="%.2f"),
            },
        )
else:
    st.caption("Seleziona una riga del listino per aprire la scheda prodotto e lo storico prezzi.")

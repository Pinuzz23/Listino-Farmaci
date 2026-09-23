from pathlib import Path

import pandas as pd
import streamlit as st

from modules.catalog_db import dashboard_stats, init_db, publications_dataframe
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles

inject_styles()
schema = load_schema()
cfg = schema.get("catalogue", {})
db_path = cfg.get("db_path", "data/listino.db")
init_db(db_path)
stats = dashboard_stats(db_path)

hero(
    "Listino Farmaci · Demo locale",
    "Wave 2 / R7 — listino consolidato, Assistente AI e Dashboard Builder self-service",
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Prodotti", stats["products"])
c2.metric("Offerte attive", stats["offers"])
c3.metric("Fornitori", stats["suppliers"])
c4.metric("Pubblicazioni", stats["publications"])

left, right = st.columns([1.25, 1])
with left:
    st.subheader("Flusso operativo")
    st.markdown(
        """
        1. **Validazione** — carica, controlla e correggi il tracciato.
        2. **Anteprima pubblicazione** — confronta il file validato con il listino esistente.
        3. **Pubblica a listino** — salva il batch nel database e archivia originale, validato e report.
        4. **Listino prodotti** — ricerca e filtra il catalogo consolidato.
        5. **Le mie Dashboard** — crea KPI, grafici, Top N, tabelle e trend personalizzati.
        6. **Pubblicazioni** — consulta la provenienza di ogni caricamento.
        """
    )

    if stats["latest"]:
        latest = stats["latest"]
        st.success(
            f"Ultima pubblicazione: **{latest['batch_id']}** · {latest['supplier'] or 'fornitore non indicato'} · "
            f"{latest['row_count']} righe"
        )
    else:
        st.info("Il listino è ancora vuoto. Vai in **Validazione**, completa un file e pubblicalo.")

with right:
    st.subheader("Architettura demo")
    st.code(
        """Tracciato Excel\n    ↓\nValidazione + AIFA\n    ↓\nTracciato validato\n    ↓\nAnteprima pubblicazione\n    ↓\nSQLite locale\n    ↓\nListino + storico prezzi""",
        language="text",
    )
    st.caption(f"Database locale: `{Path(db_path)}`")

st.subheader("Ultime pubblicazioni")
pubs = publications_dataframe(db_path)
if pubs.empty:
    st.caption("Nessuna pubblicazione registrata.")
else:
    st.dataframe(pubs.head(10), hide_index=True, use_container_width=True)

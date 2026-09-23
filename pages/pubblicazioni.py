import streamlit as st

from modules.catalog_db import init_db, publication_rows_dataframe, publications_dataframe
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles

inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
init_db(db_path)

hero(
    "Pubblicazioni",
    "Registro dei batch pubblicati e tracciabilità delle righe che hanno alimentato il listino",
)

pubs = publications_dataframe(db_path)
if pubs.empty:
    st.info("Nessuna pubblicazione registrata.")
    st.stop()

st.dataframe(pubs, hide_index=True, use_container_width=True)

batch = st.selectbox("Apri batch", pubs["Batch"].tolist())
selected = pubs[pubs["Batch"] == batch].iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Articoli", int(selected["Articoli"]))
c2.metric("Nuovi prodotti", int(selected["Nuovi prodotti"]))
c3.metric("Aggiornamenti", int(selected["Aggiornamenti"]))
c4.metric("Invariati", int(selected["Invariati"]))

st.write(f"**Fornitore:** {selected['Fornitore'] or '-'}")
st.write(f"**File sorgente:** {selected['File']}")
st.write(f"**Data:** {selected['Data']}")

st.subheader("Righe del batch")
rows = publication_rows_dataframe(db_path, batch)
if rows.empty:
    st.caption("Nessuna riga trovata.")
else:
    st.dataframe(rows, hide_index=True, use_container_width=True)

st.info(
    "La demo mantiene lo storico delle pubblicazioni e dei prezzi. "
    "La funzione di annullamento/rollback batch verrà aggiunta dopo aver validato il modello di pubblicazione."
)

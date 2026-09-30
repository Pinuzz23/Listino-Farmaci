from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.auth import require_permission
from modules.catalogue_visibility import (
    STATUS_ARCHIVED,
    STATUS_HIDDEN,
    STATUS_ICONS,
    STATUS_LABELS,
    STATUS_VISIBLE,
    CatalogueVisibilityError,
    admin_catalogue_dataframe,
    init_db,
    set_product_status,
)
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles


require_permission("manage_catalogue")

inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
init_db(db_path)

hero(
    "Gestione catalogo",
    "Visibilità prodotti per il listino cliente — nascondi, archivia e ripristina senza perdere lo storico",
)

st.info(
    "**Nascondi** esclude il prodotto solo dalla visualizzazione cliente. "
    "**Archivia / elimina dal listino** lo rimuove dal listino operativo senza cancellare "
    "dati, pubblicazioni, storico prezzi o documenti collegati."
)


df = admin_catalogue_dataframe(db_path)

if df.empty:
    st.info("Il catalogo non contiene ancora prodotti.")
    st.stop()

status_normalized = (
    df["Stato"]
    .fillna(STATUS_VISIBLE)
    .astype(str)
    .str.strip()
    .str.upper()
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Prodotti", len(df))
m2.metric("Visibili", int(status_normalized.eq(STATUS_VISIBLE).sum()))
m3.metric("Nascosti", int(status_normalized.eq(STATUS_HIDDEN).sum()))
m4.metric("Archiviati", int(status_normalized.eq(STATUS_ARCHIVED).sum()))

st.subheader("Ricerca e filtri")
f1, f2 = st.columns([2.4, 1])

with f1:
    search = st.text_input(
        "Cerca prodotto",
        placeholder="AIC, nome commerciale, principio attivo, forma farmaceutica o fornitore...",
    )

with f2:
    status_filter = st.selectbox(
        "Stato",
        ["Tutti", STATUS_VISIBLE, STATUS_HIDDEN, STATUS_ARCHIVED],
        format_func=lambda value: (
            value
            if value == "Tutti"
            else f"{STATUS_ICONS[value]} {STATUS_LABELS[value]}"
        ),
    )

filtered = df.copy()
filtered["Stato"] = (
    filtered["Stato"]
    .fillna(STATUS_VISIBLE)
    .astype(str)
    .str.strip()
    .str.upper()
)

if status_filter != "Tutti":
    filtered = filtered[filtered["Stato"] == status_filter]

if search.strip():
    needle = search.strip().casefold()
    searchable = [
        "AIC",
        "Nome Commerciale",
        "Principio Attivo",
        "Forma Farmaceutica",
        "Fornitori",
    ]
    mask = filtered[searchable].fillna("").astype(str).apply(
        lambda row: row.str.casefold().str.contains(
            needle,
            regex=False,
        ).any(),
        axis=1,
    )
    filtered = filtered[mask]

filtered = filtered.reset_index(drop=True)
filtered["Stato visualizzato"] = filtered["Stato"].map(
    lambda value: f"{STATUS_ICONS.get(value, '•')} {STATUS_LABELS.get(value, value)}"
)

st.caption(f"Risultati: **{len(filtered)}**")

show_cols = [
    "AIC",
    "Nome Commerciale",
    "Principio Attivo",
    "Forma Farmaceutica",
    "Fornitori",
    "Offerte attive",
    "Stato visualizzato",
]

selection = st.dataframe(
    filtered[show_cols],
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Stato visualizzato": st.column_config.TextColumn("Stato"),
        "Offerte attive": st.column_config.NumberColumn(format="%d"),
    },
)

selected_rows = (
    selection.selection.rows
    if selection and selection.selection
    else []
)

if not selected_rows:
    st.caption(
        "Seleziona un prodotto per modificarne la visibilità nel catalogo."
    )
    st.stop()

row = filtered.iloc[selected_rows[0]]
product_id = int(row["ID"])
current_status = str(row["Stato"] or STATUS_VISIBLE).upper()

st.divider()
st.subheader(row.get("Nome Commerciale") or "Scheda prodotto")

c1, c2, c3 = st.columns(3)
with c1:
    st.write(f"**AIC:** {row.get('AIC') or '-'}")
    st.write(f"**Principio attivo:** {row.get('Principio Attivo') or '-'}")
with c2:
    st.write(f"**Forma farmaceutica:** {row.get('Forma Farmaceutica') or '-'}")
    st.write(f"**Fornitori:** {row.get('Fornitori') or '-'}")
with c3:
    st.write(
        "**Stato:** "
        f"{STATUS_ICONS.get(current_status, '•')} "
        f"{STATUS_LABELS.get(current_status, current_status)}"
    )
    st.write(f"**Offerte attive:** {int(row.get('Offerte attive') or 0)}")

if row.get("Motivazione"):
    st.caption(f"Ultima motivazione: {row.get('Motivazione')}")
if row.get("Aggiornato il"):
    updated_by = row.get("Aggiornato da") or "-"
    st.caption(
        f"Ultimo aggiornamento stato: {row.get('Aggiornato il')} · {updated_by}"
    )

st.markdown("#### Modifica stato")
reason = st.text_area(
    "Motivazione",
    max_chars=1000,
    placeholder="Indica il motivo della modifica. La motivazione verrà registrata nell'audit.",
    key=f"catalogue_reason_{product_id}_{current_status}",
)


def apply_status(target_status: str) -> None:
    try:
        result = set_product_status(
            product_id,
            target_status,
            reason,
            db_path,
        )
    except CatalogueVisibilityError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error("Non è stato possibile aggiornare lo stato del prodotto.")
        with st.expander("Dettaglio tecnico"):
            st.code(f"{type(exc).__name__}: {exc}")
    else:
        st.success(
            "Stato aggiornato: "
            f"{STATUS_LABELS.get(result['new_status'], result['new_status'])}."
        )
        st.rerun()


if current_status == STATUS_VISIBLE:
    left, right = st.columns(2)
    with left:
        if st.button(
            "👁 Nascondi ai clienti",
            type="primary",
            use_container_width=True,
            help="Il prodotto resterà disponibile agli utenti interni ma non ai Clienti.",
        ):
            apply_status(STATUS_HIDDEN)

    with right:
        confirm_archive = st.checkbox(
            "Confermo l'archiviazione",
            key=f"confirm_archive_{product_id}",
        )
        if st.button(
            "🗃 Elimina dal listino (archivia)",
            use_container_width=True,
            disabled=not confirm_archive,
            help="Non cancella fisicamente il prodotto: lo rimuove dal listino operativo preservando lo storico.",
        ):
            apply_status(STATUS_ARCHIVED)

elif current_status == STATUS_HIDDEN:
    left, right = st.columns(2)
    with left:
        if st.button(
            "✅ Rendi visibile ai clienti",
            type="primary",
            use_container_width=True,
        ):
            apply_status(STATUS_VISIBLE)

    with right:
        confirm_archive = st.checkbox(
            "Confermo l'archiviazione",
            key=f"confirm_archive_{product_id}",
        )
        if st.button(
            "🗃 Elimina dal listino (archivia)",
            use_container_width=True,
            disabled=not confirm_archive,
        ):
            apply_status(STATUS_ARCHIVED)

elif current_status == STATUS_ARCHIVED:
    left, right = st.columns(2)
    with left:
        if st.button(
            "♻️ Ripristina come visibile",
            type="primary",
            use_container_width=True,
        ):
            apply_status(STATUS_VISIBLE)

    with right:
        if st.button(
            "👁 Ripristina come nascosto",
            use_container_width=True,
            help="Ripristina il prodotto per gli utenti interni mantenendolo invisibile ai Clienti.",
        ):
            apply_status(STATUS_HIDDEN)

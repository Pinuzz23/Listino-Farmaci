from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.auth import has_permission, require_permission
from modules.catalogue_visibility import (
    STATUS_ARCHIVED,
    STATUS_HIDDEN,
    STATUS_ICONS,
    STATUS_LABELS,
    STATUS_VISIBLE,
    CatalogueVisibilityError,
    admin_catalogue_dataframe,
)
from modules.order_management import (
    OrderManagementError,
    init_db,
    product_edit_snapshot,
    set_product_status,
    update_product_and_offer,
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
    "Visibilità, archiviazione e correzione controllata degli articoli del listino",
)

st.info(
    "**Nascondi** esclude il prodotto solo dalla visualizzazione cliente. "
    "**Archivia / elimina dal listino** lo rimuove dal listino operativo senza cancellare "
    "storico e documenti. Le modifiche Order Management generano un delta ERP."
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
    st.caption("Seleziona un prodotto per gestirlo.")
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


st.markdown("#### Visibilità / archiviazione")
reason = st.text_area(
    "Motivazione cambio stato",
    max_chars=1000,
    placeholder="Indica il motivo. Verrà registrato nell'audit.",
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
    except (CatalogueVisibilityError, OrderManagementError) as exc:
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
            help="Non cancella fisicamente il prodotto: lo rimuove dal listino operativo e genera DISABLE nel delta ERP.",
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
            help="Il ripristino genera REACTIVATE nel delta ERP.",
        ):
            apply_status(STATUS_VISIBLE)

    with right:
        if st.button(
            "👁 Ripristina come nascosto",
            use_container_width=True,
            help="Ripristina l'articolo internamente e genera REACTIVATE nel delta ERP, mantenendolo invisibile al Cliente.",
        ):
            apply_status(STATUS_HIDDEN)


if not has_permission("edit_catalogue"):
    st.stop()

st.divider()
st.markdown("#### Modifica articolo / offerta")
st.caption(
    "AIC, Fornitore e Codice Fornitore restano identificativi protetti. "
    "Le correzioni vengono registrate nell'audit e producono un evento UPDATE per ERP."
)

try:
    snapshot = product_edit_snapshot(product_id, db_path)
except Exception as exc:
    st.error("Non è stato possibile caricare i dati modificabili dell'articolo.")
    with st.expander("Dettaglio tecnico"):
        st.code(f"{type(exc).__name__}: {exc}")
    st.stop()

product = snapshot["product"]
offers = snapshot["offers"]
if offers.empty:
    st.info("Nessuna offerta attiva associata a questo prodotto.")
    st.stop()


def clean(value, default=""):
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    return default if value is None else value


def option_index(options, value):
    normalized = "" if value is None else str(value)
    return options.index(normalized) if normalized in options else 0


offer_labels = {
    f"{clean(item['Fornitore'], '-')} · {clean(item['Codice Fornitore'], '-')} · offerta #{int(item['offer_id'])}": int(item["offer_id"])
    for _, item in offers.iterrows()
}
selected_offer_label = st.selectbox(
    "Offerta da modificare",
    list(offer_labels.keys()),
    key=f"edit_offer_{product_id}",
)
selected_offer_id = offer_labels[selected_offer_label]
offer = offers[offers["offer_id"] == selected_offer_id].iloc[0].to_dict()

lists = schema.get("lists", {})
yn_values = [str(value) for value in lists.get("YN", ["Y", "N"])]
stup_values = [str(value) for value in lists.get("STUPEFACENTE", [])]
group_values = [str(value) for value in lists.get("GRUPPO_STIVAGGIO", [])]
temp_values = [str(value) for value in lists.get("TEMPERATURA_STIVAGGIO", [])]
fala_values = [""] + yn_values
iva_values = [0.0, 0.04, 0.10, 0.22]

with st.form(f"edit_catalogue_{product_id}_{selected_offer_id}"):
    st.write(
        f"**Identificativi protetti:** AIC {clean(product.get('AIC'), '-')} · "
        f"Fornitore {clean(offer.get('Fornitore'), '-')} · "
        f"Codice {clean(offer.get('Codice Fornitore'), '-')}"
    )

    p1, p2 = st.columns(2)
    with p1:
        nome = st.text_input(
            "Nome Commerciale",
            value=str(clean(product.get("Nome Commerciale"))),
        )
        principio = st.text_input(
            "Principio Attivo",
            value=str(clean(product.get("Principio Attivo"))),
        )
        forma = st.text_input(
            "Forma Farmaceutica",
            value=str(clean(product.get("Forma Farmaceutica"))),
        )
        materiale = st.selectbox(
            "Materiale Pericoloso",
            yn_values,
            index=option_index(yn_values, clean(product.get("Materiale Pericoloso"))),
        )
        stupefacente = st.selectbox(
            "Stupefacente",
            stup_values,
            index=option_index(stup_values, clean(product.get("Stupefacente"))),
        )
        fala = st.selectbox(
            "Fala / Lasa",
            fala_values,
            index=option_index(fala_values, clean(product.get("Fala / Lasa"))),
        )
        atc7 = st.text_input("ATC7", value=str(clean(product.get("ATC7"))))
        atc9 = st.text_input("ATC9", value=str(clean(product.get("ATC9"))))

    with p2:
        gruppo = st.selectbox(
            "Gruppo di Stivaggio",
            group_values,
            index=option_index(group_values, clean(product.get("Gruppo di Stivaggio"))),
        )
        temperatura = st.selectbox(
            "Temperatura di Stivaggio",
            temp_values,
            index=option_index(temp_values, clean(product.get("Temperatura di Stivaggio"))),
        )
        upc = st.number_input(
            "UPC",
            min_value=1,
            step=1,
            value=int(clean(product.get("UPC"), 1) or 1),
        )
        x = st.number_input(
            "X (cm)", min_value=0.01, value=float(clean(product.get("X"), 0.01) or 0.01), step=0.01
        )
        y = st.number_input(
            "Y (cm)", min_value=0.01, value=float(clean(product.get("Y"), 0.01) or 0.01), step=0.01
        )
        z = st.number_input(
            "Z (cm)", min_value=0.01, value=float(clean(product.get("Z"), 0.01) or 0.01), step=0.01
        )
        note = st.text_area("Note", value=str(clean(product.get("Note"))))

    st.markdown("##### Dati offerta")
    o1, o2, o3, o4 = st.columns(4)
    with o1:
        prezzo_unitario = st.number_input(
            "Prezzo Unitario",
            min_value=0.0,
            value=float(clean(offer.get("Prezzo Unitario"), 0.0) or 0.0),
            step=0.01,
            format="%.4f",
        )
    with o2:
        prezzo_confezione = st.number_input(
            "Prezzo Confezione",
            min_value=0.0,
            value=float(clean(offer.get("Prezzo Confezione"), 0.0) or 0.0),
            step=0.01,
            format="%.2f",
        )
    with o3:
        minimo = st.number_input(
            "Minimo Movimentabile",
            min_value=1,
            step=1,
            value=int(clean(offer.get("Minimo Movimentabile"), 1) or 1),
        )
    with o4:
        current_iva = float(clean(offer.get("IVA"), 0.0) or 0.0)
        iva = st.selectbox(
            "IVA",
            iva_values,
            index=min(range(len(iva_values)), key=lambda idx: abs(iva_values[idx] - current_iva)),
            format_func=lambda value: f"{value * 100:.0f}%",
        )

    edit_reason = st.text_area(
        "Motivazione modifica",
        max_chars=1000,
        placeholder="Motivo della correzione dati / offerta.",
    )

    submitted = st.form_submit_button(
        "💾 Salva modifica e genera delta ERP",
        type="primary",
        use_container_width=True,
    )

if submitted:
    values = {
        "Nome Commerciale": nome,
        "Principio Attivo": principio,
        "Forma Farmaceutica": forma,
        "Materiale Pericoloso": materiale,
        "Stupefacente": stupefacente,
        "ATC7": atc7,
        "ATC9": atc9,
        "Fala / Lasa": fala,
        "Gruppo di Stivaggio": gruppo,
        "Temperatura di Stivaggio": temperatura,
        "UPC": upc,
        "Note": note,
        "X": x,
        "Y": y,
        "Z": z,
        "Prezzo Unitario": prezzo_unitario,
        "Prezzo Confezione": prezzo_confezione,
        "Minimo Movimentabile": minimo,
        "IVA": iva,
    }
    try:
        update_product_and_offer(
            product_id,
            selected_offer_id,
            values,
            edit_reason,
            db_path,
        )
    except OrderManagementError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error("Non è stato possibile salvare la modifica.")
        with st.expander("Dettaglio tecnico"):
            st.code(f"{type(exc).__name__}: {exc}")
    else:
        st.success("Modifica salvata e delta ERP UPDATE generato.")
        st.rerun()

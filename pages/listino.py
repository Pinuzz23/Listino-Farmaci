from datetime import datetime

import pandas as pd
import streamlit as st

from modules.auth import audit, has_permission, require_permission
from modules.aifa_documents import AifaDocumentError, fetch_and_store_rcp
from modules.catalog_db import (
    catalogue_dataframe,
    export_catalogue_excel,
    init_db,
    price_history_dataframe,
)
from modules.db_backend import use_postgres
from modules.product_documents import create_signed_url, get_current_document
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles


def _clean_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _format_retrieved_at(value) -> str:
    if not value:
        return "-"
    try:
        ts = pd.to_datetime(value)
        return ts.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)


def _render_rcp_section(row) -> None:
    # Customer Care non possiede view_rcp: la sezione non viene mostrata.
    if not has_permission("view_rcp"):
        return

    st.markdown("#### Documentazione ufficiale AIFA")

    aic = _clean_text(row.get("AIC"))

    if not aic:
        st.caption(
            "Scheda tecnica non disponibile: il prodotto non ha un AIC associato."
        )
        return

    if not use_postgres():
        st.info(
            "La documentazione RCP persistente è disponibile nell'ambiente "
            "cloud collegato a PostgreSQL / Supabase."
        )
        return

    try:
        document = get_current_document(aic, "RCP")
    except Exception as exc:
        st.warning(
            "Impossibile verificare al momento la documentazione RCP "
            "nel database."
        )
        with st.expander("Dettaglio tecnico"):
            st.code(f"{type(exc).__name__}: {exc}")
        return

    if document:
        st.success("✅ RCP archiviato")
        st.caption(
            "Fonte: AIFA · "
            f"acquisito {_format_retrieved_at(document.get('retrieved_at'))}"
        )

        actions = []

        if has_permission("view_rcp"):
            actions.append("open")
        if has_permission("download_rcp"):
            actions.append("download")
        if has_permission("update_rcp"):
            actions.append("update")

        action_cols = st.columns(len(actions)) if actions else []

        for action, col in zip(actions, action_cols):
            with col:
                if action == "open":
                    try:
                        open_url = create_signed_url(
                            document["storage_path"],
                            expires_in=900,
                            download=False,
                        )
                        st.link_button(
                            "📄 Apri RCP",
                            open_url,
                            use_container_width=True,
                        )
                    except Exception as exc:
                        st.caption("Apertura non disponibile")
                        with st.expander("Dettaglio tecnico link"):
                            st.code(f"{type(exc).__name__}: {exc}")

                elif action == "download":
                    try:
                        download_url = create_signed_url(
                            document["storage_path"],
                            expires_in=900,
                            download=True,
                        )
                        st.link_button(
                            "⬇️ Scarica PDF",
                            download_url,
                            use_container_width=True,
                        )
                    except Exception as exc:
                        st.caption("Download non disponibile")
                        with st.expander("Dettaglio tecnico download"):
                            st.code(f"{type(exc).__name__}: {exc}")

                elif action == "update":
                    refresh_key = (
                        f"refresh_rcp_"
                        f"{row.get('product_id', 'p')}_"
                        f"{row.get('offer_id', 'o')}"
                    )

                    if st.button(
                        "🔄 Aggiorna da AIFA",
                        key=refresh_key,
                        use_container_width=True,
                        help=(
                            "Recupera nuovamente l'RCP da AIFA. Se il PDF è "
                            "cambiato, la versione precedente resta nello storico."
                        ),
                    ):
                        require_permission("update_rcp")

                        try:
                            with st.spinner(
                                "Controllo RCP AIFA e aggiornamento archivio..."
                            ):
                                fetch_and_store_rcp(aic)
                        except AifaDocumentError as exc:
                            st.error(f"RCP AIFA non recuperato: {exc}")
                        except Exception as exc:
                            st.error(
                                "Errore durante l'aggiornamento del documento RCP."
                            )
                            with st.expander(
                                "Dettaglio tecnico aggiornamento"
                            ):
                                st.code(f"{type(exc).__name__}: {exc}")
                        else:
                            audit(
                                "RCP_UPDATE",
                                entity_type="product",
                                entity_id=aic,
                                details={"document_type": "RCP"},
                            )
                            st.success("RCP aggiornato correttamente.")
                            st.rerun()

        with st.expander("Dettagli documento"):
            st.write(f"**AIC6:** {document.get('aic6') or '-'}")
            st.write(f"**Tipo:** {document.get('document_type') or '-'}")
            st.write(f"**Fonte:** {document.get('source') or '-'}")
            st.write(f"**Stato:** {document.get('status') or '-'}")
            st.write(
                f"**Dimensione:** "
                f"{int(document.get('file_size') or 0):,} byte"
            )
            st.write(f"**SHA-256:** `{document.get('sha256') or '-'}`")

        return

    st.caption(
        "Nessuna scheda tecnica RCP è ancora archiviata per questo prodotto."
    )

    if not has_permission("fetch_rcp"):
        return

    fetch_key = (
        f"fetch_rcp_"
        f"{row.get('product_id', 'p')}_"
        f"{row.get('offer_id', 'o')}"
    )

    if st.button(
        "📥 Recupera RCP da AIFA",
        key=fetch_key,
        type="primary",
        help=(
            "Scarica l'RCP ufficiale AIFA e lo archivia nel bucket privato "
            "Supabase Storage."
        ),
    ):
        require_permission("fetch_rcp")

        try:
            with st.spinner(
                "Recupero RCP da AIFA e archiviazione su Supabase..."
            ):
                fetch_and_store_rcp(aic)
        except AifaDocumentError as exc:
            st.error(f"RCP AIFA non recuperato: {exc}")
        except Exception as exc:
            st.error(
                "Errore durante il salvataggio del documento RCP."
            )
            with st.expander("Dettaglio tecnico salvataggio"):
                st.code(f"{type(exc).__name__}: {exc}")
        else:
            audit(
                "RCP_FETCH",
                entity_type="product",
                entity_id=aic,
                details={"document_type": "RCP"},
            )
            st.success("✅ RCP recuperato e archiviato correttamente.")
            st.rerun()

require_permission("view_catalogue")

inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
init_db(db_path)

hero(
    "Listino prodotti",
    "Vista consolidata delle offerte pubblicate — ricerca, filtri, "
    "dettaglio prodotto, documentazione AIFA e storico prezzi",
)

df = catalogue_dataframe(db_path)

if df.empty:
    st.info(
        "Il listino è vuoto. Pubblica il primo tracciato "
        "dalla pagina Validazione."
    )
    st.stop()

m1, m2, m3, m4 = st.columns(4)
m1.metric("Righe listino", len(df))
m2.metric("Prodotti", df["product_id"].nunique())
m3.metric("Fornitori", df["Fornitore"].nunique())
m4.metric(
    "Con AIC",
    int(df["AIC"].fillna("").astype(str).str.strip().ne("").sum()),
)

st.subheader("Ricerca e filtri")

f1, f2, f3, f4 = st.columns([2.2, 1.1, 1.1, 1.1])

with f1:
    search = st.text_input(
        "Cerca",
        placeholder=(
            "AIC, nome commerciale, principio attivo, "
            "ATC, codice fornitore..."
        ),
    )

with f2:
    suppliers = ["Tutti"] + sorted(
        df["Fornitore"].dropna().astype(str).unique().tolist()
    )
    supplier = st.selectbox("Fornitore", suppliers)

with f3:
    groups = ["Tutti"] + sorted(
        df["Gruppo di Stivaggio"].dropna().astype(str).unique().tolist()
    )
    group = st.selectbox("Stivaggio", groups)

with f4:
    narcotics = ["Tutti"] + sorted(
        df["Stupefacente"].dropna().astype(str).unique().tolist()
    )
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
        "AIC",
        "Nome Commerciale",
        "Principio Attivo",
        "ATC7",
        "ATC9",
        "Codice Fornitore",
        "Fornitore",
    ]
    mask = filtered[searchable].fillna("").astype(str).apply(
        lambda row: row.str.casefold().str.contains(
            needle,
            regex=False,
        ).any(),
        axis=1,
    )
    filtered = filtered[mask]

st.caption(f"Risultati: **{len(filtered)}**")

show_cols = [
    "AIC",
    "Nome Commerciale",
    "Principio Attivo",
    "ATC7",
    "Fornitore",
    "Prezzo Confezione",
    "UPC",
    "Gruppo di Stivaggio",
    "Ultimo aggiornamento",
]

view = filtered[show_cols].copy()
view["Prezzo Confezione"] = pd.to_numeric(
    view["Prezzo Confezione"],
    errors="coerce",
)

selection = st.dataframe(
    view,
    hide_index=True,
    use_container_width=True,
    on_select="rerun",
    selection_mode="single-row",
    column_config={
        "Prezzo Confezione": st.column_config.NumberColumn(
            format="€ %.2f"
        ),
    },
)

if has_permission("export_catalogue"):
    export_bytes = export_catalogue_excel(db_path, filtered)

    st.download_button(
        "⬇️ Esporta listino filtrato",
        data=export_bytes,
        file_name=f"Listino_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

selected_rows = (
    selection.selection.rows
    if selection and selection.selection
    else []
)

if selected_rows:
    selected_index = filtered.index[selected_rows[0]]
    row = filtered.loc[selected_index]

    st.divider()
    st.subheader(
        _clean_text(row.get("Nome Commerciale")) or "Scheda prodotto"
    )

    a, b, c = st.columns(3)

    with a:
        st.markdown("#### Anagrafica")
        st.write(f"**AIC:** {_clean_text(row.get('AIC')) or '-'}")
        st.write(
            f"**Principio Attivo:** "
            f"{_clean_text(row.get('Principio Attivo')) or '-'}"
        )
        st.write(f"**ATC7:** {_clean_text(row.get('ATC7')) or '-'}")
        st.write(f"**ATC9:** {_clean_text(row.get('ATC9')) or '-'}")

    with b:
        st.markdown("#### Commerciale")
        st.write(
            f"**Fornitore:** {_clean_text(row.get('Fornitore')) or '-'}"
        )
        st.write(
            f"**Codice Fornitore:** "
            f"{_clean_text(row.get('Codice Fornitore')) or '-'}"
        )
        st.write(
            f"**Prezzo Unitario:** "
            f"€ {float(row.get('Prezzo Unitario') or 0):,.4f}"
        )
        st.write(
            f"**Prezzo Confezione:** "
            f"€ {float(row.get('Prezzo Confezione') or 0):,.2f}"
        )
        st.write(f"**IVA:** {float(row.get('IVA') or 0) * 100:g}%")

    with c:
        st.markdown("#### Logistica")
        st.write(f"**UPC:** {_clean_text(row.get('UPC')) or '-'}")
        st.write(
            f"**Minimo movimentabile:** "
            f"{_clean_text(row.get('Minimo Movimentabile')) or '-'}"
        )
        st.write(
            f"**Gruppo:** "
            f"{_clean_text(row.get('Gruppo di Stivaggio')) or '-'}"
        )
        st.write(
            f"**Temperatura:** "
            f"{_clean_text(row.get('Temperatura di Stivaggio')) or '-'}"
        )
        st.write(
            f"**Stupefacente:** "
            f"{_clean_text(row.get('Stupefacente')) or '-'}"
        )

    st.divider()
    _render_rcp_section(row)

    if has_permission("view_history"):
        st.divider()
        st.markdown("#### Storico prezzi")

        history = price_history_dataframe(
            db_path,
            int(row["offer_id"]),
        )

        if history.empty:
            st.caption("Nessuno storico disponibile.")
        else:
            st.dataframe(
                history,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Prezzo Unitario": st.column_config.NumberColumn(
                        format="€ %.4f"
                    ),
                    "Prezzo Confezione": st.column_config.NumberColumn(
                        format="€ %.2f"
                    ),
                    "IVA": st.column_config.NumberColumn(format="%.2f"),
                },
            )

else:
    st.caption(
        "Seleziona una riga del listino per aprire la scheda prodotto, "
        "la documentazione AIFA e lo storico prezzi."
    )

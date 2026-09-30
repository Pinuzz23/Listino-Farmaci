from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from modules.auth import require_permission
from modules.order_management import (
    OrderManagementError,
    catalogue_dataframe,
    catalogue_to_csv,
    confirm_import,
    delta_dataframe,
    init_db,
    package_csv,
    packages_dataframe,
    prepare_export,
)
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles


require_permission("export_erp")

inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
init_db(db_path)

hero(
    "Export ERP",
    "Gestione del delta incrementale Buyer → Order Management → importatore ERP",
)

st.info(
    "Il download del CSV porta i delta nello stato **EXPORTED**. "
    "Usa **Conferma importazione ERP** solo dopo che l'importatore gestionale "
    "ha completato correttamente il caricamento."
)


tab_delta, tab_packages, tab_catalogue = st.tabs(
    ["Delta da trasferire", "Pacchetti esportati", "Listino CSV"]
)


with tab_delta:
    delta = delta_dataframe(db_path)
    if delta.empty:
        st.success("Non ci sono ancora eventi ERP.")
    else:
        pending = delta[delta["Stato ERP"] == "PENDING"].copy()
        exported = int(delta["Stato ERP"].eq("EXPORTED").sum())
        imported = int(delta["Stato ERP"].eq("IMPORTED").sum())

        c1, c2, c3 = st.columns(3)
        c1.metric("Da esportare", len(pending))
        c2.metric("Esportati / da confermare", exported)
        c3.metric("Importati ERP", imported)

        if pending.empty:
            st.success("Tutti i delta disponibili risultano già esportati.")
        else:
            f1, f2, f3, f4 = st.columns([1.5, 1.2, 1.2, 1.5])
            with f1:
                search = st.text_input(
                    "Cerca nel delta",
                    placeholder="AIC, prodotto o fornitore...",
                    key="erp_delta_search",
                )
            with f2:
                source_values = ["Tutte"] + sorted(
                    pending["Origine"].dropna().astype(str).unique().tolist()
                )
                source_filter = st.selectbox(
                    "Origine",
                    source_values,
                    key="erp_delta_source",
                )
            with f3:
                action_values = ["Tutte"] + sorted(
                    pending["Azione"].dropna().astype(str).unique().tolist()
                )
                action_filter = st.selectbox(
                    "Operazione",
                    action_values,
                    key="erp_delta_action",
                )
            with f4:
                batch_values = sorted(
                    pending["Batch"].dropna().astype(str).unique().tolist()
                )
                selected_batches = st.multiselect(
                    "Batch Buyer",
                    batch_values,
                    help="Lascia vuoto per includere tutti i batch nel filtro corrente.",
                )

            filtered = pending.copy()
            if source_filter != "Tutte":
                filtered = filtered[filtered["Origine"] == source_filter]
            if action_filter != "Tutte":
                filtered = filtered[filtered["Azione"] == action_filter]
            if selected_batches:
                # Gli eventi Order senza batch restano fuori solo quando si filtra esplicitamente per batch.
                filtered = filtered[filtered["Batch"].astype(str).isin(selected_batches)]
            if search.strip():
                needle = search.strip().casefold()
                search_cols = ["AIC", "Nome Commerciale", "Fornitore"]
                mask = filtered[search_cols].fillna("").astype(str).apply(
                    lambda row: row.str.casefold().str.contains(
                        needle,
                        regex=False,
                    ).any(),
                    axis=1,
                )
                filtered = filtered[mask]

            filtered = filtered.reset_index(drop=True)
            st.caption(f"Delta nel filtro corrente: **{len(filtered)}**")
            st.dataframe(
                filtered[
                    [
                        "ID",
                        "Azione",
                        "Origine",
                        "Batch",
                        "AIC",
                        "Nome Commerciale",
                        "Fornitore",
                        "Creato il",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
            )

            if not filtered.empty:
                st.warning(
                    "Il pulsante prepara un unico pacchetto CSV contenente tutti i delta "
                    "attualmente mostrati nella tabella."
                )
                if st.button(
                    "🔄 Prepara CSV delta filtrato",
                    type="primary",
                    use_container_width=True,
                ):
                    try:
                        with st.spinner("Preparazione pacchetto ERP..."):
                            result = prepare_export(
                                filtered["ID"].astype(int).tolist(),
                                db_path,
                            )
                    except OrderManagementError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error("Non è stato possibile preparare il pacchetto ERP.")
                        with st.expander("Dettaglio tecnico"):
                            st.code(f"{type(exc).__name__}: {exc}")
                    else:
                        st.session_state["last_erp_package"] = result["package_id"]
                        st.session_state["last_erp_csv"] = result["data"]
                        st.success(
                            f"Pacchetto {result['package_id']} preparato: "
                            f"{result['row_count']} righe."
                        )

            package_id = st.session_state.get("last_erp_package")
            package_data = st.session_state.get("last_erp_csv")
            if package_id and package_data:
                st.download_button(
                    "⬇️ Scarica ultimo CSV preparato",
                    data=package_data,
                    file_name=f"{package_id}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )


with tab_packages:
    packages = packages_dataframe(db_path)
    if packages.empty:
        st.info("Nessun pacchetto ERP ancora generato.")
    else:
        st.dataframe(
            packages,
            hide_index=True,
            use_container_width=True,
        )

        package_options = packages["Pacchetto"].astype(str).tolist()
        selected_package = st.selectbox(
            "Pacchetto",
            package_options,
            key="erp_package_select",
        )
        package_row = packages[
            packages["Pacchetto"].astype(str) == selected_package
        ].iloc[0]

        left, right = st.columns(2)
        with left:
            try:
                redownload = package_csv(selected_package, db_path)
            except Exception as exc:
                st.error("CSV del pacchetto non disponibile.")
                with st.expander("Dettaglio tecnico pacchetto"):
                    st.code(f"{type(exc).__name__}: {exc}")
            else:
                st.download_button(
                    "⬇️ Scarica nuovamente CSV",
                    data=redownload,
                    file_name=f"{selected_package}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

        with right:
            if str(package_row["Stato"]).upper() == "EXPORTED":
                confirm = st.checkbox(
                    "Confermo che l'importatore ERP ha concluso correttamente",
                    key=f"confirm_erp_{selected_package}",
                )
                if st.button(
                    "✅ Conferma importazione ERP",
                    type="primary",
                    disabled=not confirm,
                    use_container_width=True,
                ):
                    try:
                        confirm_import(selected_package, db_path)
                    except OrderManagementError as exc:
                        st.error(str(exc))
                    except Exception as exc:
                        st.error("Conferma ERP non riuscita.")
                        with st.expander("Dettaglio tecnico conferma"):
                            st.code(f"{type(exc).__name__}: {exc}")
                    else:
                        st.success("Pacchetto marcato come IMPORTED.")
                        st.rerun()
            else:
                st.success("Importazione ERP già confermata.")


with tab_catalogue:
    st.caption(
        "Esporta il listino corrente, o una sua porzione filtrata, nel formato CSV "
        "configurato per l'importatore."
    )
    catalogue = catalogue_dataframe(db_path)
    if catalogue.empty:
        st.info("Il listino è vuoto.")
    else:
        f1, f2, f3 = st.columns([2, 1.2, 1.2])
        with f1:
            cat_search = st.text_input(
                "Cerca",
                placeholder="AIC, nome commerciale, principio attivo...",
                key="erp_catalogue_search",
            )
        with f2:
            suppliers = ["Tutti"] + sorted(
                catalogue["Fornitore"].dropna().astype(str).unique().tolist()
            )
            cat_supplier = st.selectbox(
                "Fornitore",
                suppliers,
                key="erp_catalogue_supplier",
            )
        with f3:
            statuses = ["Tutti"] + sorted(
                catalogue["Stato Catalogo"].dropna().astype(str).unique().tolist()
            )
            cat_status = st.selectbox(
                "Stato catalogo",
                statuses,
                key="erp_catalogue_status",
            )

        cat_filtered = catalogue.copy()
        if cat_supplier != "Tutti":
            cat_filtered = cat_filtered[cat_filtered["Fornitore"] == cat_supplier]
        if cat_status != "Tutti":
            cat_filtered = cat_filtered[
                cat_filtered["Stato Catalogo"] == cat_status
            ]
        if cat_search.strip():
            needle = cat_search.strip().casefold()
            search_cols = [
                "AIC",
                "Nome Commerciale",
                "Principio Attivo",
                "Codice Fornitore",
            ]
            mask = cat_filtered[search_cols].fillna("").astype(str).apply(
                lambda row: row.str.casefold().str.contains(
                    needle,
                    regex=False,
                ).any(),
                axis=1,
            )
            cat_filtered = cat_filtered[mask]

        st.caption(f"Righe da esportare: **{len(cat_filtered)}**")
        preview_cols = [
            "AIC",
            "Nome Commerciale",
            "Fornitore",
            "Prezzo Confezione",
            "Stato Catalogo",
        ]
        st.dataframe(
            cat_filtered[preview_cols],
            hide_index=True,
            use_container_width=True,
        )

        if not cat_filtered.empty:
            csv_bytes = catalogue_to_csv(cat_filtered)
            st.download_button(
                "⬇️ Esporta porzione listino in CSV",
                data=csv_bytes,
                file_name=f"Listino_ERP_{datetime.now().strftime('%Y-%m-%d_%H%M')}.csv",
                mime="text/csv",
                use_container_width=True,
            )

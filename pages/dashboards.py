from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from modules.dashboard_builder import (
    CATEGORICAL_FIELDS,
    GLOBAL_FILTER_FIELDS,
    NUMERIC_FIELDS,
    TABLE_FIELDS,
    add_widget,
    apply_global_filters,
    apply_price_range,
    apply_widget_filter,
    bar_dataframe,
    catalogue_for_dashboards,
    create_dashboard,
    delete_dashboard,
    delete_widget,
    get_dashboard,
    init_dashboard_tables,
    list_dashboards,
    list_widgets,
    metric_value,
    move_widget,
    table_dataframe,
    top_n_dataframe,
    trend_dataframe,
    update_dashboard,
    update_widget,
)
from modules.schema_loader import load_schema
from modules.ui import hero, inject_styles


inject_styles()
schema = load_schema()
db_path = schema.get("catalogue", {}).get("db_path", "data/listino.db")
init_dashboard_tables(db_path)

hero(
    "Le mie Dashboard",
    "Crea viste personalizzate del listino con KPI, grafici, Top N, tabelle e trend prezzi — senza scrivere codice.",
)

catalogue = catalogue_for_dashboards(db_path)
dashboards_df = list_dashboards(db_path)


def _options(df: pd.DataFrame, field: str):
    if df.empty or field not in df.columns:
        return []
    vals = df[field].dropna().tolist()
    clean = []
    seen = set()
    for value in vals:
        if isinstance(value, float) and pd.isna(value):
            continue
        key = str(value)
        if not key.strip() or key in seen:
            continue
        seen.add(key)
        clean.append(value)
    try:
        return sorted(clean, key=lambda x: str(x).casefold())
    except Exception:
        return clean


def _idx(options, value, default=0):
    try:
        return options.index(value)
    except Exception:
        return default


def _format_iva(value):
    try:
        return f"{float(value) * 100:g}%"
    except Exception:
        return str(value)


def _format_metric(value, kind):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if kind == "money":
        return f"€ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if kind == "integer":
        return f"{int(value):,}".replace(",", ".")
    if isinstance(value, float):
        return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(value)


def _config_form(prefix: str, widget_type: str, config: dict | None = None):
    config = config or {}
    out = {}

    # Widget-level optional filter.
    filter_choices = ["Nessuno"] + [f for f in CATEGORICAL_FIELDS if f in catalogue.columns]
    filter_field_default = config.get("filter_field") or "Nessuno"
    filter_field = st.selectbox(
        "Filtro del widget",
        filter_choices,
        index=_idx(filter_choices, filter_field_default),
        key=f"{prefix}_filter_field",
        help="Facoltativo: applica questo filtro soltanto al widget, oltre ai filtri globali della dashboard.",
    )
    if filter_field != "Nessuno":
        values = _options(catalogue, filter_field)
        if values:
            current = config.get("filter_value")
            out["filter_field"] = filter_field
            out["filter_value"] = st.selectbox(
                "Valore filtro widget",
                values,
                index=_idx(values, current),
                key=f"{prefix}_filter_value",
                format_func=_format_iva if filter_field == "IVA" else str,
            )

    st.markdown("---")

    if widget_type == "KPI":
        metrics = ["Righe listino", "Prodotti distinti", "Fornitori distinti"] + [f for f in NUMERIC_FIELDS if f in catalogue.columns]
        metric = st.selectbox(
            "Misura",
            metrics,
            index=_idx(metrics, config.get("metric", "Righe listino")),
            key=f"{prefix}_metric",
        )
        out["metric"] = metric
        if metric in NUMERIC_FIELDS:
            aggs = ["mean", "sum", "min", "max", "median"]
            labels = {"mean": "Media", "sum": "Somma", "min": "Minimo", "max": "Massimo", "median": "Mediana"}
            agg = st.selectbox(
                "Calcolo",
                aggs,
                index=_idx(aggs, config.get("aggregation", "mean")),
                format_func=lambda x: labels[x],
                key=f"{prefix}_aggregation",
            )
            out["aggregation"] = agg

    elif widget_type == "Barre":
        dimensions = [f for f in CATEGORICAL_FIELDS if f in catalogue.columns and f not in {"AIC", "Codice Fornitore"}]
        dimension = st.selectbox(
            "Dimensione",
            dimensions,
            index=_idx(dimensions, config.get("dimension", "Fornitore")),
            key=f"{prefix}_dimension",
        )
        measures = ["Conteggio righe"] + [f for f in NUMERIC_FIELDS if f in catalogue.columns]
        measure = st.selectbox(
            "Misura",
            measures,
            index=_idx(measures, config.get("measure", "Conteggio righe")),
            key=f"{prefix}_measure",
        )
        out.update({"dimension": dimension, "measure": measure})
        if measure != "Conteggio righe":
            aggs = ["mean", "sum", "min", "max", "median"]
            agg = st.selectbox(
                "Aggregazione",
                aggs,
                index=_idx(aggs, config.get("aggregation", "mean")),
                format_func=lambda x: {"mean": "Media", "sum": "Somma", "min": "Minimo", "max": "Massimo", "median": "Mediana"}[x],
                key=f"{prefix}_aggregation",
            )
            out["aggregation"] = agg
        out["top_n"] = st.number_input(
            "Numero massimo categorie",
            min_value=1,
            max_value=50,
            value=int(config.get("top_n", 10) or 10),
            step=1,
            key=f"{prefix}_topn",
        )

    elif widget_type == "Top N":
        labels = [f for f in ["Nome Commerciale", "Fornitore", "Principio Attivo", "AIC", "Codice Fornitore"] if f in catalogue.columns]
        metrics = [f for f in NUMERIC_FIELDS if f in catalogue.columns]
        out["label"] = st.selectbox(
            "Etichetta",
            labels,
            index=_idx(labels, config.get("label", "Nome Commerciale")),
            key=f"{prefix}_label",
        )
        out["metric"] = st.selectbox(
            "Misura ranking",
            metrics,
            index=_idx(metrics, config.get("metric", "Prezzo Confezione")),
            key=f"{prefix}_metric",
        )
        directions = ["desc", "asc"]
        out["direction"] = st.selectbox(
            "Ordine",
            directions,
            index=_idx(directions, config.get("direction", "desc")),
            format_func=lambda x: "Più alto → più basso" if x == "desc" else "Più basso → più alto",
            key=f"{prefix}_direction",
        )
        out["top_n"] = st.number_input(
            "Top N",
            min_value=1,
            max_value=50,
            value=int(config.get("top_n", 10) or 10),
            step=1,
            key=f"{prefix}_topn",
        )

    elif widget_type == "Tabella":
        options = [f for f in TABLE_FIELDS if f in catalogue.columns]
        current = [f for f in config.get("columns", ["AIC", "Nome Commerciale", "Principio Attivo", "Fornitore", "Prezzo Confezione"]) if f in options]
        out["columns"] = st.multiselect(
            "Colonne",
            options,
            default=current or options[:5],
            key=f"{prefix}_columns",
        )
        out["limit"] = st.number_input(
            "Righe massime",
            min_value=5,
            max_value=500,
            value=int(config.get("limit", 50) or 50),
            step=5,
            key=f"{prefix}_limit",
        )

    elif widget_type == "Trend prezzi":
        metrics = ["Prezzo Unitario", "Prezzo Confezione"]
        out["metric"] = st.selectbox(
            "Prezzo",
            metrics,
            index=_idx(metrics, config.get("metric", "Prezzo Confezione")),
            key=f"{prefix}_metric",
        )
        aggs = ["mean", "min", "max", "median"]
        out["aggregation"] = st.selectbox(
            "Aggregazione giornaliera",
            aggs,
            index=_idx(aggs, config.get("aggregation", "mean")),
            format_func=lambda x: {"mean": "Media", "min": "Minimo", "max": "Massimo", "median": "Mediana"}[x],
            key=f"{prefix}_aggregation",
        )
        groups = ["Nessuno", "Fornitore", "Nome Commerciale", "Principio Attivo"]
        out["group_by"] = st.selectbox(
            "Serie",
            groups,
            index=_idx(groups, config.get("group_by", "Nessuno")),
            key=f"{prefix}_group_by",
        )

    return out


def _render_widget(widget: dict, filtered: pd.DataFrame):
    title = widget.get("title", "Widget")
    kind = widget.get("widget_type")
    config = widget.get("config", {})
    data = apply_widget_filter(filtered, config)

    st.markdown(f"#### {title}")

    if kind == "KPI":
        value, value_kind = metric_value(data, config)
        st.metric(title, _format_metric(value, value_kind))
        if config.get("filter_field"):
            st.caption(f"Filtro widget: {config['filter_field']} = {config.get('filter_value')}")

    elif kind == "Barre":
        chart = bar_dataframe(data, config)
        if chart.empty:
            st.caption("Nessun dato disponibile con i filtri correnti.")
        else:
            dimension = config.get("dimension", "Fornitore")
            st.bar_chart(chart.set_index(dimension)["Valore"], use_container_width=True)

    elif kind == "Top N":
        top = top_n_dataframe(data, config)
        if top.empty:
            st.caption("Nessun dato disponibile con i filtri correnti.")
        else:
            label = config.get("label", "Nome Commerciale")
            metric = config.get("metric", "Prezzo Confezione")
            chart = top[[label, metric]].dropna().set_index(label)
            st.bar_chart(chart, use_container_width=True)
            st.dataframe(top, hide_index=True, use_container_width=True)

    elif kind == "Tabella":
        table = table_dataframe(data, config)
        if table.empty:
            st.caption("Nessun dato disponibile con i filtri correnti.")
        else:
            st.dataframe(
                table,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Prezzo Unitario": st.column_config.NumberColumn(format="€ %.4f"),
                    "Prezzo Confezione": st.column_config.NumberColumn(format="€ %.2f"),
                    "IVA": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    elif kind == "Trend prezzi":
        trend = trend_dataframe(db_path, data, config)
        if trend.empty:
            st.caption("Lo storico prezzi non contiene dati sufficienti per questo grafico.")
        else:
            if trend["Serie"].nunique() > 12:
                keep = trend.groupby("Serie")["Valore"].count().sort_values(ascending=False).head(12).index
                trend = trend[trend["Serie"].isin(keep)]
                st.caption("Mostrate le 12 serie con più osservazioni per mantenere leggibile il grafico.")
            try:
                st.line_chart(trend, x="Data", y="Valore", color="Serie", use_container_width=True)
            except TypeError:
                pivot = trend.pivot_table(index="Data", columns="Serie", values="Valore", aggfunc="mean")
                st.line_chart(pivot, use_container_width=True)


view_tab, manage_tab, create_tab = st.tabs(["📊 Visualizza", "🛠️ Gestisci", "➕ Nuova dashboard"])

with create_tab:
    st.subheader("Crea una nuova dashboard")
    st.caption("La dashboard salva soltanto la configurazione dei widget. I numeri si aggiornano sempre sui dati correnti del listino.")

    st.markdown("#### Modelli pronti")
    t1, t2, t3 = st.columns(3)
    if t1.button("📦 Crea Overview Listino", use_container_width=True):
        try:
            new_id = create_dashboard(db_path, "Overview Listino", "Panoramica generale del listino pubblicato.")
            add_widget(db_path, new_id, "Righe listino", "KPI", {"metric": "Righe listino"}, "half")
            add_widget(db_path, new_id, "Fornitori", "KPI", {"metric": "Fornitori distinti"}, "half")
            add_widget(db_path, new_id, "Prodotti per fornitore", "Barre", {"dimension": "Fornitore", "measure": "Conteggio righe", "top_n": 15}, "full")
            add_widget(db_path, new_id, "Dettaglio listino", "Tabella", {"columns": ["AIC", "Nome Commerciale", "Principio Attivo", "Fornitore", "Prezzo Confezione", "Gruppo di Stivaggio"], "limit": 50}, "full")
            st.session_state["r7_dashboard_id"] = new_id
            st.success("Modello Overview creato.")
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))
    if t2.button("💶 Crea Analisi Prezzi", use_container_width=True):
        try:
            new_id = create_dashboard(db_path, "Analisi Prezzi", "KPI, ranking e trend dei prezzi pubblicati.")
            add_widget(db_path, new_id, "Prezzo unitario medio", "KPI", {"metric": "Prezzo Unitario", "aggregation": "mean"}, "half")
            add_widget(db_path, new_id, "Prezzo confezione massimo", "KPI", {"metric": "Prezzo Confezione", "aggregation": "max"}, "half")
            add_widget(db_path, new_id, "Top 10 prezzi confezione", "Top N", {"label": "Nome Commerciale", "metric": "Prezzo Confezione", "direction": "desc", "top_n": 10}, "full")
            add_widget(db_path, new_id, "Trend prezzo confezione", "Trend prezzi", {"metric": "Prezzo Confezione", "aggregation": "mean", "group_by": "Nessuno"}, "full")
            st.session_state["r7_dashboard_id"] = new_id
            st.success("Modello Analisi Prezzi creato.")
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))
    if t3.button("🧊 Crea Logistica", use_container_width=True):
        try:
            new_id = create_dashboard(db_path, "Logistica", "Stivaggio, temperatura e caratteristiche logistiche del listino.")
            add_widget(db_path, new_id, "Prodotti per stivaggio", "Barre", {"dimension": "Gruppo di Stivaggio", "measure": "Conteggio righe", "top_n": 10}, "half")
            add_widget(db_path, new_id, "Classificazione stupefacenti", "Barre", {"dimension": "Stupefacente", "measure": "Conteggio righe", "top_n": 15}, "half")
            add_widget(db_path, new_id, "Temperatura di stivaggio", "Barre", {"dimension": "Temperatura di Stivaggio", "measure": "Conteggio righe", "top_n": 20}, "full")
            add_widget(db_path, new_id, "Dettaglio logistico", "Tabella", {"columns": ["AIC", "Nome Commerciale", "Fornitore", "Gruppo di Stivaggio", "Temperatura di Stivaggio", "UPC", "Minimo Movimentabile"], "limit": 75}, "full")
            st.session_state["r7_dashboard_id"] = new_id
            st.success("Modello Logistica creato.")
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))

    st.divider()
    st.markdown("#### Dashboard vuota")
    with st.form("create_dashboard_form", clear_on_submit=True):
        name = st.text_input("Nome dashboard", placeholder="Es. Analisi Prezzi")
        description = st.text_area("Descrizione", placeholder="A cosa serve questa dashboard?")
        submitted = st.form_submit_button("Crea dashboard", type="primary")
    if submitted:
        try:
            new_id = create_dashboard(db_path, name, description)
            st.session_state["r7_dashboard_id"] = new_id
            st.success("Dashboard creata. Ora puoi aggiungere i widget dalla scheda Gestisci.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

with manage_tab:
    dashboards_df = list_dashboards(db_path)
    if dashboards_df.empty:
        st.info("Non hai ancora dashboard personali. Creane una dalla scheda **Nuova dashboard**.")
    else:
        ids = dashboards_df["dashboard_id"].astype(int).tolist()
        name_by_id = dict(zip(dashboards_df["dashboard_id"].astype(int), dashboards_df["Nome"]))
        preferred = st.session_state.get("r7_dashboard_id")
        default_index = ids.index(preferred) if preferred in ids else 0
        dashboard_id = st.selectbox(
            "Dashboard da gestire",
            ids,
            index=default_index,
            format_func=lambda x: name_by_id.get(x, str(x)),
            key="manage_dashboard_select",
        )
        st.session_state["r7_dashboard_id"] = int(dashboard_id)
        dashboard = get_dashboard(db_path, int(dashboard_id))

        with st.expander("Impostazioni dashboard", expanded=False):
            with st.form(f"edit_dashboard_{dashboard_id}"):
                new_name = st.text_input("Nome", value=dashboard.get("name", ""))
                new_desc = st.text_area("Descrizione", value=dashboard.get("description", "") or "")
                c_save, c_delete = st.columns([1, 1])
                save_meta = c_save.form_submit_button("Salva impostazioni", type="primary")
                request_delete = c_delete.form_submit_button("Elimina dashboard")
            if save_meta:
                try:
                    update_dashboard(db_path, int(dashboard_id), new_name, new_desc)
                    st.success("Dashboard aggiornata.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if request_delete:
                st.session_state[f"confirm_delete_dashboard_{dashboard_id}"] = True

            if st.session_state.get(f"confirm_delete_dashboard_{dashboard_id}"):
                st.warning("La cancellazione elimina la configurazione della dashboard e dei suoi widget, non i dati del listino.")
                yes, no = st.columns(2)
                if yes.button("Conferma eliminazione", type="primary", key=f"yes_delete_dash_{dashboard_id}"):
                    delete_dashboard(db_path, int(dashboard_id))
                    st.session_state.pop(f"confirm_delete_dashboard_{dashboard_id}", None)
                    st.session_state.pop("r7_dashboard_id", None)
                    st.rerun()
                if no.button("Annulla", key=f"no_delete_dash_{dashboard_id}"):
                    st.session_state.pop(f"confirm_delete_dashboard_{dashboard_id}", None)
                    st.rerun()

        st.subheader("Widget")
        widgets = list_widgets(db_path, int(dashboard_id))
        if not widgets:
            st.caption("Questa dashboard non contiene ancora widget.")

        for i, widget in enumerate(widgets):
            with st.expander(f"{i + 1}. {widget['title']} · {widget['widget_type']}", expanded=False):
                top = st.columns([1, 1, 4, 1])
                if top[0].button("↑", disabled=i == 0, key=f"up_{widget['widget_id']}"):
                    move_widget(db_path, widget["widget_id"], -1)
                    st.rerun()
                if top[1].button("↓", disabled=i == len(widgets) - 1, key=f"down_{widget['widget_id']}"):
                    move_widget(db_path, widget["widget_id"], 1)
                    st.rerun()
                top[2].caption("Usa le frecce per cambiare l'ordine di visualizzazione.")
                if top[3].button("🗑️", key=f"delete_widget_{widget['widget_id']}"):
                    delete_widget(db_path, widget["widget_id"])
                    st.rerun()

                title = st.text_input("Titolo", value=widget["title"], key=f"edit_title_{widget['widget_id']}")
                types = ["KPI", "Barre", "Top N", "Tabella", "Trend prezzi"]
                wtype = st.selectbox(
                    "Tipo widget",
                    types,
                    index=_idx(types, widget["widget_type"]),
                    key=f"edit_type_{widget['widget_id']}",
                )
                widths = ["half", "full"]
                width = st.selectbox(
                    "Larghezza",
                    widths,
                    index=_idx(widths, widget.get("width", "half")),
                    format_func=lambda x: "Mezza riga" if x == "half" else "Riga intera",
                    key=f"edit_width_{widget['widget_id']}",
                )
                config = _config_form(f"edit_{widget['widget_id']}_{wtype}", wtype, widget.get("config", {}))
                if st.button("Salva widget", type="primary", key=f"save_widget_{widget['widget_id']}"):
                    update_widget(db_path, widget["widget_id"], title, wtype, config, width)
                    st.success("Widget aggiornato.")
                    st.rerun()

        st.divider()
        st.subheader("Aggiungi widget")
        with st.container(border=True):
            types = ["KPI", "Barre", "Top N", "Tabella", "Trend prezzi"]
            add_type = st.selectbox("Tipo", types, key=f"new_type_{dashboard_id}")
            add_title = st.text_input("Titolo widget", value={
                "KPI": "Nuovo KPI",
                "Barre": "Distribuzione",
                "Top N": "Top prodotti",
                "Tabella": "Dettaglio listino",
                "Trend prezzi": "Andamento prezzi",
            }[add_type], key=f"new_title_{dashboard_id}_{add_type}")
            add_width = st.selectbox(
                "Larghezza",
                ["half", "full"],
                format_func=lambda x: "Mezza riga" if x == "half" else "Riga intera",
                key=f"new_width_{dashboard_id}_{add_type}",
            )
            add_config = _config_form(f"new_{dashboard_id}_{add_type}", add_type, {})
            if st.button("➕ Aggiungi alla dashboard", type="primary", key=f"add_widget_{dashboard_id}_{add_type}"):
                add_widget(db_path, int(dashboard_id), add_title, add_type, add_config, add_width)
                st.success("Widget aggiunto.")
                st.rerun()

with view_tab:
    dashboards_df = list_dashboards(db_path)
    if dashboards_df.empty:
        st.info("Crea la tua prima dashboard dalla scheda **Nuova dashboard**.")
    else:
        ids = dashboards_df["dashboard_id"].astype(int).tolist()
        name_by_id = dict(zip(dashboards_df["dashboard_id"].astype(int), dashboards_df["Nome"]))
        preferred = st.session_state.get("r7_dashboard_id")
        default_index = ids.index(preferred) if preferred in ids else 0
        selected_id = st.selectbox(
            "Dashboard",
            ids,
            index=default_index,
            format_func=lambda x: name_by_id.get(x, str(x)),
            key="view_dashboard_select",
        )
        st.session_state["r7_dashboard_id"] = int(selected_id)
        dashboard = get_dashboard(db_path, int(selected_id))
        widgets = list_widgets(db_path, int(selected_id))

        if dashboard.get("description"):
            st.caption(dashboard["description"])

        if catalogue.empty:
            st.warning("Il listino è vuoto. Puoi configurare la dashboard, ma i widget mostreranno dati solo dopo una pubblicazione.")
            filtered = catalogue.copy()
        else:
            with st.expander("🎛️ Filtri globali", expanded=True):
                filter_cols = st.columns(4)
                global_filters = {}
                visible_fields = [f for f in GLOBAL_FILTER_FIELDS if f in catalogue.columns]
                for idx, field in enumerate(visible_fields):
                    values = ["Tutti"] + _options(catalogue, field)
                    value = filter_cols[idx % 4].selectbox(
                        field,
                        values,
                        key=f"global_{selected_id}_{field}",
                        format_func=_format_iva if field == "IVA" else str,
                    )
                    if value != "Tutti":
                        global_filters[field] = value

                price_field = filter_cols[len(visible_fields) % 4].selectbox(
                    "Filtro prezzo",
                    ["Nessuno", "Prezzo Unitario", "Prezzo Confezione"],
                    key=f"global_price_field_{selected_id}",
                )
                price_min = price_max = None
                if price_field != "Nessuno":
                    vals = pd.to_numeric(catalogue[price_field], errors="coerce").dropna()
                    if not vals.empty:
                        overall_min, overall_max = float(vals.min()), float(vals.max())
                        if overall_min < overall_max:
                            price_min, price_max = st.slider(
                                f"Range {price_field}",
                                min_value=overall_min,
                                max_value=overall_max,
                                value=(overall_min, overall_max),
                                key=f"global_price_range_{selected_id}_{price_field}",
                            )
                        else:
                            st.caption(f"{price_field}: unico valore disponibile € {overall_min:,.2f}")

            filtered = apply_global_filters(catalogue, global_filters)
            if price_field != "Nessuno" and price_min is not None:
                filtered = apply_price_range(filtered, price_field, price_min, price_max)

        r1, r2, r3 = st.columns(3)
        r1.metric("Righe dopo i filtri", len(filtered))
        r2.metric("Prodotti", filtered["product_id"].nunique() if "product_id" in filtered.columns and not filtered.empty else 0)
        r3.metric("Fornitori", filtered["Fornitore"].nunique() if "Fornitore" in filtered.columns and not filtered.empty else 0)

        st.divider()
        if not widgets:
            st.info("Questa dashboard non contiene ancora widget. Vai in **Gestisci** per aggiungerli.")
        else:
            # Render half-width widgets in pairs, full-width widgets on their own row.
            i = 0
            while i < len(widgets):
                widget = widgets[i]
                if widget.get("width", "half") == "full":
                    with st.container(border=True):
                        _render_widget(widget, filtered)
                    i += 1
                    continue

                pair = [widget]
                if i + 1 < len(widgets) and widgets[i + 1].get("width", "half") == "half":
                    pair.append(widgets[i + 1])
                cols = st.columns(len(pair))
                for col, item in zip(cols, pair):
                    with col:
                        with st.container(border=True):
                            _render_widget(item, filtered)
                i += len(pair)

        st.caption(f"Aggiornata sui dati correnti del listino · {datetime.now().strftime('%d/%m/%Y %H:%M')}")

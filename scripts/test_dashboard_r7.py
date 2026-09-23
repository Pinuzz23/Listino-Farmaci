from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.catalog_db import publish_records
from modules.dashboard_builder import (
    add_widget,
    bar_dataframe,
    catalogue_for_dashboards,
    create_dashboard,
    get_dashboard,
    init_dashboard_tables,
    list_dashboards,
    list_widgets,
    metric_value,
    top_n_dataframe,
    trend_dataframe,
)

records = [
    {
        "Fornitore": "ALFA S.P.A.", "AIC": "000000001", "Codice Fornitore": "A1",
        "Nome Commerciale": "PRODOTTO A", "Principio Attivo": "ATTIVO A",
        "Materiale Pericoloso": "N", "Stupefacente": "No", "ATC7": "A01AA01", "ATC9": "",
        "Fala / Lasa": "N", "Gruppo di Stivaggio": "STD_Standard",
        "Temperatura di Stivaggio": "da + 15 a 25°C", "Prezzo Unitario": 1.0,
        "Prezzo Confezione": 10.0, "UPC": 10, "Minimo Movimentabile": 1, "IVA": 0.1, "Note": ""
    },
    {
        "Fornitore": "BETA S.P.A.", "AIC": "000000002", "Codice Fornitore": "B1",
        "Nome Commerciale": "PRODOTTO B", "Principio Attivo": "ATTIVO B",
        "Materiale Pericoloso": "N", "Stupefacente": "No", "ATC7": "B01BB01", "ATC9": "",
        "Fala / Lasa": "N", "Gruppo di Stivaggio": "FRIGO_Frigo",
        "Temperatura di Stivaggio": "da + 2 a 8°C", "Prezzo Unitario": 3.0,
        "Prezzo Confezione": 30.0, "UPC": 10, "Minimo Movimentabile": 2, "IVA": 0.22, "Note": ""
    },
]

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "listino.db"
    publish_records(records, "test1.xlsx", db, app_version="r7-test")
    records2 = [dict(records[0]), dict(records[1])]
    records2[0]["Prezzo Unitario"] = 1.5
    records2[0]["Prezzo Confezione"] = 15.0
    publish_records(records2, "test2.xlsx", db, app_version="r7-test")

    init_dashboard_tables(db)
    dash_id = create_dashboard(db, "Analisi Test", "test")
    add_widget(db, dash_id, "Prezzo medio", "KPI", {"metric": "Prezzo Unitario", "aggregation": "mean"})
    add_widget(db, dash_id, "Per fornitore", "Barre", {"dimension": "Fornitore", "measure": "Conteggio righe", "top_n": 10})

    dashboards = list_dashboards(db)
    widgets = list_widgets(db, dash_id)
    df = catalogue_for_dashboards(db)
    metric, kind = metric_value(df, widgets[0]["config"])
    bar = bar_dataframe(df, widgets[1]["config"])
    top = top_n_dataframe(df, {"label": "Nome Commerciale", "metric": "Prezzo Confezione", "direction": "desc", "top_n": 1})
    trend = trend_dataframe(db, df, {"metric": "Prezzo Confezione", "aggregation": "mean", "group_by": "Nessuno"})

    assert len(dashboards) == 1
    assert get_dashboard(db, dash_id)["name"] == "Analisi Test"
    assert len(widgets) == 2
    assert round(metric, 2) == 2.25, metric
    assert set(bar["Fornitore"]) == {"ALFA S.P.A.", "BETA S.P.A."}
    assert top.iloc[0]["Nome Commerciale"] == "PRODOTTO B"
    assert len(trend) >= 1
    print("OK - Dashboard R7: persistenza, KPI, barre, Top N e trend prezzi funzionano.")

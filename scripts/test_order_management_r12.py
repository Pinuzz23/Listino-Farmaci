from __future__ import annotations

import tempfile
from pathlib import Path

import modules.catalogue_visibility as visibility
import modules.order_management as om


USER = {
    "user_id": "test-order-user",
    "role_id": "ORDER_MANAGEMENT",
    "permissions": [
        "access_app",
        "view_catalogue",
        "export_catalogue",
        "manage_catalogue",
        "edit_catalogue",
        "export_erp",
    ],
}


def sample_record() -> dict:
    return {
        "Fornitore": "FORNITORE TEST",
        "AIC": "012345678",
        "Codice Fornitore": "CF-001",
        "Nome Commerciale": "Farmaco Test",
        "Principio Attivo": "Principio Test",
        "Forma Farmaceutica": "Compresse",
        "Materiale Pericoloso": "N",
        "Stupefacente": "No",
        "ATC7": "ATC7",
        "ATC9": "ATC9",
        "Fala / Lasa": "N",
        "Gruppo di Stivaggio": "STD_Standard",
        "Temperatura di Stivaggio": "da + 15 a 25°C",
        "Prezzo Unitario": 1.0,
        "Prezzo Confezione": 10.0,
        "UPC": 10,
        "Minimo Movimentabile": 1,
        "IVA": 0.10,
        "Note": "",
        "X": 10.0,
        "Y": 5.0,
        "Z": 2.0,
    }


def main() -> None:
    om.current_user = lambda: USER
    visibility.current_user = lambda: USER

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "r12.db"
        om.init_db(db_path)

        result = om.publish_records(
            [sample_record()],
            source_name="test.xlsx",
            db_path=db_path,
            app_version="r12-test",
        )
        assert result["batch_id"]

        delta = om.delta_dataframe(db_path)
        assert len(delta) == 1
        assert delta.iloc[0]["Azione"] == "INSERT"
        assert delta.iloc[0]["Stato ERP"] == "PENDING"

        package = om.prepare_export(delta["ID"].astype(int).tolist(), db_path)
        assert package["row_count"] == 1
        assert b"Operazione" in package["data"]

        om.confirm_import(package["package_id"], db_path)
        packages = om.packages_dataframe(db_path)
        assert packages.iloc[0]["Stato"] == "IMPORTED"

        catalogue = om.catalogue_dataframe(db_path)
        product_id = int(catalogue.iloc[0]["product_id"])
        offer_id = int(catalogue.iloc[0]["offer_id"])

        values = sample_record()
        values["Nome Commerciale"] = "Farmaco Test Corretto"
        values["Prezzo Unitario"] = 1.2
        values["Prezzo Confezione"] = 12.0
        om.update_product_and_offer(product_id, offer_id, values, "Correzione test", db_path)

        delta = om.delta_dataframe(db_path)
        assert (delta["Azione"] == "UPDATE").any()
        assert (delta["Stato ERP"] == "PENDING").any()

        om.set_product_status(product_id, "ARCHIVED", "Fine commercializzazione test", db_path)
        delta = om.delta_dataframe(db_path)
        assert (delta["Azione"] == "DISABLE").any()

        print("R12 Order Management test: OK")


if __name__ == "__main__":
    main()

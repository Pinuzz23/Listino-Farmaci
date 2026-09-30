from __future__ import annotations

from modules.db_backend import use_postgres

if use_postgres():
    from modules.catalog_db_postgres import *  # noqa: F401,F403
else:
    from modules.catalog_db_sqlite import *  # noqa: F401,F403

# R10 / Master definitivo 22 campi: pubblicazione e preview dei nuovi attributi.
from modules.master22_catalog import (  # noqa: E402,F401
    preview_publication,
    publish_records,
)

# R11 / Visibilità catalogo: init, lettura ed export diventano role-aware.
# CLIENTE vede solo VISIBLE; gli utenti interni vedono VISIBLE + HIDDEN;
# ARCHIVED resta disponibile soltanto nella pagina Gestione catalogo Admin.
from modules.catalogue_visibility import (  # noqa: E402,F401
    catalogue_dataframe,
    export_catalogue_excel,
    init_db,
)

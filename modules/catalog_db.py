from __future__ import annotations

from modules.db_backend import use_postgres

if use_postgres():
    from modules.catalog_db_postgres import *  # noqa: F401,F403
else:
    from modules.catalog_db_sqlite import *  # noqa: F401,F403

# R10 / Master definitivo 22 campi: override mirati per i nuovi attributi
# senza duplicare l'intero backend PostgreSQL/SQLite.
from modules.master22_catalog import (  # noqa: E402,F401
    catalogue_dataframe,
    export_catalogue_excel,
    init_db,
    preview_publication,
    publish_records,
)

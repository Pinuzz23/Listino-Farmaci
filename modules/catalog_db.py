from __future__ import annotations

from modules.db_backend import use_postgres

if use_postgres():
    # R13: patcha i percorsi PostgreSQL/Master22/R12 prima degli import operativi.
    import modules.catalog_db_postgres_r13  # noqa: F401
    # R13.1: verifica e ricostruisce in modo idempotente eventuali delta Buyer
    # mancanti per le pubblicazioni successive all'introduzione della R12.
    import modules.erp_delta_guard  # noqa: F401
    from modules.catalog_db_postgres import *  # noqa: F401,F403
else:
    from modules.catalog_db_sqlite import *  # noqa: F401,F403

# R14: estende il master a 23 campi e collega validità/rinnovi al catalogo.
import modules.validity_r14  # noqa: F401

# R10/R13: preview del delta di pubblicazione sul tracciato definitivo a 22 campi.
from modules.master22_catalog import preview_publication  # noqa: E402,F401

# R12/R13: ruolo Order Management, coda ERP e pubblicazione Buyer ottimizzata.
from modules.order_management import (  # noqa: E402,F401
    catalogue_dataframe,
    export_catalogue_excel,
    init_db,
    publish_records,
)

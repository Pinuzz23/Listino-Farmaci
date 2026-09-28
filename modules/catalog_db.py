from __future__ import annotations

from modules.db_backend import use_postgres

if use_postgres():
    from modules.catalog_db_postgres import *  # noqa: F401,F403
else:
    from modules.catalog_db_sqlite import *  # noqa: F401,F403

from __future__ import annotations

import os


def get_postgres_url() -> str | None:
    """
    Restituisce la URL PostgreSQL quando disponibile.
    Su Streamlit Cloud la legge dai Secrets.
    In alternativa può leggerla dalla variabile DATABASE_URL.
    """
    try:
        import streamlit as st

        if "database" in st.secrets:
            database = st.secrets["database"]
            if "url" in database:
                url = str(database["url"]).strip()
                if url:
                    return url
    except Exception:
        pass

    url = os.getenv("DATABASE_URL", "").strip()
    return url or None


def use_postgres() -> bool:
    return bool(get_postgres_url())


def backend_name() -> str:
    return "PostgreSQL" if use_postgres() else "SQLite"
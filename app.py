import streamlit as st

from modules.db_backend import backend_name


# ============================================================
# CONFIGURAZIONE PAGINA
# Deve essere eseguita prima degli altri comandi Streamlit.
# ============================================================

st.set_page_config(
    page_title="Listino Farmaci",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# DATABASE BACKEND
# Locale      -> SQLite
# Streamlit   -> PostgreSQL / Supabase
# ============================================================

db_backend = backend_name()

if db_backend == "PostgreSQL":
    st.caption("🟢 Database attivo: PostgreSQL / Supabase")
else:
    st.caption("🟡 Database attivo: SQLite locale")


# ============================================================
# NAVIGAZIONE
# ============================================================

pages = {
    "Operatività": [
        st.Page(
            "pages/dashboard.py",
            title="Dashboard",
            icon="🏠",
            default=True,
        ),
        st.Page(
            "pages/validazione.py",
            title="Validazione",
            icon="✅",
        ),
        st.Page(
            "pages/listino.py",
            title="Listino prodotti",
            icon="📦",
        ),
        st.Page(
            "pages/assistente.py",
            title="Assistente Listino",
            icon="💬",
        ),
        st.Page(
            "pages/dashboards.py",
            title="Le mie Dashboard",
            icon="📊",
        ),
        st.Page(
            "pages/pubblicazioni.py",
            title="Pubblicazioni",
            icon="📑",
        ),
    ]
}


pg = st.navigation(pages)
pg.run()
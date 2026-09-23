import streamlit as st

if "database" in st.secrets and "url" in st.secrets["database"]:
    st.success("✅ Configurazione database trovata nei Secrets")
else:
    st.error("❌ Configurazione database non trovata")
    
import psycopg2

try:
    conn = psycopg2.connect(
        st.secrets["database"]["url"],
        connect_timeout=10
    )

    with conn.cursor() as cur:
        cur.execute("SELECT current_database();")
        db_name = cur.fetchone()[0]

    conn.close()

    st.success(f"✅ Connessione PostgreSQL riuscita — database: {db_name}")

except Exception as e:
    st.error("❌ Connessione PostgreSQL non riuscita")
    st.code(f"{type(e).__name__}: {str(e)}")

st.set_page_config(
    page_title="Listino Farmaci",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = {
    "Operatività": [
        st.Page("pages/dashboard.py", title="Dashboard", icon="🏠", default=True),
        st.Page("pages/validazione.py", title="Validazione", icon="✅"),
        st.Page("pages/listino.py", title="Listino prodotti", icon="📦"),
        st.Page("pages/assistente.py", title="Assistente Listino", icon="💬"),
        st.Page("pages/dashboards.py", title="Le mie Dashboard", icon="📊"),
        st.Page("pages/pubblicazioni.py", title="Pubblicazioni", icon="📑"),
    ]
}

pg = st.navigation(pages)
pg.run()

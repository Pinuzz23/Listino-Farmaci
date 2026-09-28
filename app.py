import streamlit as st

from modules.auth import (
    current_user,
    render_force_password_change,
    render_login,
    sign_out,
)
from modules.db_backend import backend_name


st.set_page_config(
    page_title="Listino Farmaci",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)


user = current_user()

if not user:
    render_login()
    st.stop()


if user.get("must_change_password"):
    render_force_password_change()
    st.stop()


permissions = set(user.get("permissions") or [])


def allowed(permission_id: str) -> bool:
    return permission_id in permissions


operativita = []

if allowed("view_dashboard"):
    operativita.append(
        st.Page(
            "pages/dashboard.py",
            title="Dashboard",
            icon="🏠",
            default=True,
        )
    )

if allowed("validate_files"):
    operativita.append(
        st.Page(
            "pages/validazione.py",
            title="Validazione",
            icon="✅",
        )
    )

if allowed("view_catalogue"):
    operativita.append(
        st.Page(
            "pages/listino.py",
            title="Listino prodotti",
            icon="📦",
        )
    )

if allowed("use_listino_assistant"):
    operativita.append(
        st.Page(
            "pages/assistente.py",
            title="Assistente Listino",
            icon="💬",
        )
    )

if allowed("view_dashboards"):
    operativita.append(
        st.Page(
            "pages/dashboards.py",
            title="Le mie Dashboard",
            icon="📊",
        )
    )

if allowed("view_publications"):
    operativita.append(
        st.Page(
            "pages/pubblicazioni.py",
            title="Pubblicazioni",
            icon="📑",
        )
    )


if not operativita:
    st.error(
        "Il tuo account è attivo ma non dispone di pagine accessibili. "
        "Contatta un amministratore."
    )
    st.stop()


pages = {
    "Operatività": operativita,
}

if allowed("manage_users"):
    pages["Amministrazione"] = [
        st.Page(
            "pages/utenti.py",
            title="Gestione utenti",
            icon="👥",
        )
    ]


with st.sidebar:
    st.divider()
    st.caption("Utente")
    st.markdown(f"**{user.get('display_name', '')}**")
    st.caption(user.get("email", ""))

    organization = user.get("organization_name")
    if organization:
        st.caption(organization)

    st.caption(f"Ruolo: {user.get('role_id', '-')}")

    if st.button(
        "🚪 Esci",
        use_container_width=True,
    ):
        sign_out()
        st.rerun()


db_backend = backend_name()

if db_backend == "PostgreSQL":
    st.caption("🟢 Database attivo: PostgreSQL / Supabase")
else:
    st.caption("🟡 Database attivo: SQLite locale")


pg = st.navigation(pages)
pg.run()

import streamlit as st

from modules.access_requests import ROLE_LABELS
from modules.auth import (
    current_user,
    render_force_password_change,
    render_login,
    sign_out,
)
from modules.db_backend import backend_name, use_postgres

# R13: applica le ottimizzazioni PostgreSQL prima che R11/R12 importino
# i riferimenti alle funzioni di pubblicazione Master22.
if use_postgres():
    import modules.catalog_db_postgres_r13  # noqa: F401

from modules.catalogue_visibility import init_db as init_catalogue_visibility_db
from modules.order_management import init_db as init_order_management_db


st.set_page_config(
    page_title="Listino Farmaci",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# R12: etichetta UI del nuovo ruolo. Il ruolo resta interno e viene assegnato
# dall'Admin; non viene esposto tra i profili richiedibili dalla schermata pubblica.
ROLE_LABELS["ORDER_MANAGEMENT"] = "Order Management"


# R11: crea in modo non distruttivo lo stato catalogo e il permesso Admin.
@st.cache_resource(show_spinner=False)
def _bootstrap_catalogue_visibility() -> bool:
    init_catalogue_visibility_db()
    return True


# R12: crea ruolo Order Management, permessi e coda delta ERP.
# Viene eseguito dopo R11 così manage_catalogue può essere assegnato anche
# a ORDER_MANAGEMENT senza alterare il comportamento storico dell'Admin.
@st.cache_resource(show_spinner=False)
def _bootstrap_order_management() -> bool:
    init_order_management_db()
    return True


if use_postgres():
    try:
        _bootstrap_catalogue_visibility()
        _bootstrap_order_management()
    except Exception as exc:
        st.error(
            "Non è stato possibile inizializzare i moduli applicativi sul database. "
            "Riprova tra poco o verifica la connessione a Supabase."
        )
        with st.expander("Dettaglio tecnico"):
            st.code(f"{type(exc).__name__}: {exc}")
        st.stop()


user = current_user()

if not user:
    render_login()
    st.stop()


if user.get("must_change_password"):
    render_force_password_change()
    st.stop()


permissions = set(user.get("permissions") or [])
role_id = str(user.get("role_id") or "").upper()


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
            default=not allowed("view_dashboard"),
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


# R12: l'operatore Order Management ha una propria area di lavoro.
if role_id == "ORDER_MANAGEMENT":
    order_pages = []
    if allowed("manage_catalogue"):
        order_pages.append(
            st.Page(
                "pages/gestione_catalogo.py",
                title="Gestione catalogo",
                icon="🛠️",
            )
        )
    if allowed("export_erp"):
        order_pages.append(
            st.Page(
                "pages/export_erp.py",
                title="Export ERP",
                icon="🔄",
            )
        )
    if order_pages:
        pages["Order Management"] = order_pages
else:
    amministrazione = []

    if allowed("manage_users"):
        amministrazione.append(
            st.Page(
                "pages/utenti.py",
                title="Gestione utenti",
                icon="👥",
            )
        )

    if allowed("manage_catalogue"):
        amministrazione.append(
            st.Page(
                "pages/gestione_catalogo.py",
                title="Gestione catalogo",
                icon="📦",
            )
        )

    if allowed("export_erp"):
        amministrazione.append(
            st.Page(
                "pages/export_erp.py",
                title="Export ERP",
                icon="🔄",
            )
        )

    if amministrazione:
        pages["Amministrazione"] = amministrazione


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

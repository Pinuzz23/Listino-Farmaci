from __future__ import annotations

import pandas as pd
import streamlit as st

from modules.access_requests import (
    AccessRequestError,
    ROLE_LABELS,
    approve_access_request,
    list_access_requests,
    list_roles,
    list_user_profiles,
    reject_access_request,
    update_user_profile,
)
from modules.auth import current_user, require_permission


require_permission("manage_users")

st.title("👥 Gestione utenti")
st.caption(
    "Richieste di accesso, creazione account e gestione dei profili."
)

tab_requests, tab_users, tab_history = st.tabs(
    [
        "Richieste in attesa",
        "Utenti",
        "Storico richieste",
    ]
)


with tab_requests:
    pending = list_access_requests("PENDING")

    if not pending:
        st.success("Non ci sono richieste di accesso in attesa.")
    else:
        st.metric("Richieste in attesa", len(pending))

        table = pd.DataFrame(
            [
                {
                    "ID": r["request_id"],
                    "Data": r["requested_at"],
                    "Nome": r["display_name"],
                    "Email": r["email"],
                    "Azienda": r.get("company") or "",
                    "Profilo richiesto": ROLE_LABELS.get(
                        r["requested_role"],
                        r["requested_role"],
                    ),
                }
                for r in pending
            ]
        )

        st.dataframe(
            table,
            hide_index=True,
            use_container_width=True,
        )

        option_map = {
            (
                f"#{r['request_id']} · {r['display_name']} · "
                f"{r['email']}"
            ): r
            for r in pending
        }

        selected_label = st.selectbox(
            "Seleziona richiesta",
            list(option_map.keys()),
        )
        request_row = option_map[selected_label]

        st.markdown("#### Dettaglio")
        c1, c2 = st.columns(2)

        with c1:
            st.write(f"**Nome:** {request_row['display_name']}")
            st.write(f"**Email:** {request_row['email']}")
            st.write(
                f"**Azienda:** {request_row.get('company') or '-'}"
            )

        with c2:
            st.write(
                "**Profilo richiesto:** "
                + ROLE_LABELS.get(
                    request_row["requested_role"],
                    request_row["requested_role"],
                )
            )
            st.write(
                f"**Data richiesta:** {request_row['requested_at']}"
            )

        if request_row.get("notes"):
            st.write(f"**Note:** {request_row['notes']}")

        roles = list_roles()
        role_ids = [r["role_id"] for r in roles]
        role_labels = {
            r["role_id"]: r["display_name"]
            for r in roles
        }

        requested_role = request_row["requested_role"]
        default_index = (
            role_ids.index(requested_role)
            if requested_role in role_ids
            else 0
        )

        assigned_role = st.selectbox(
            "Ruolo da assegnare",
            role_ids,
            index=default_index,
            format_func=lambda role: role_labels.get(role, role),
            key=f"assign_role_{request_row['request_id']}",
        )

        review_notes = st.text_area(
            "Note dell'Admin",
            max_chars=2000,
            key=f"review_notes_{request_row['request_id']}",
        )

        approve_col, reject_col = st.columns(2)

        with approve_col:
            if st.button(
                "✅ Approva e crea account",
                type="primary",
                use_container_width=True,
                key=f"approve_{request_row['request_id']}",
            ):
                try:
                    with st.spinner(
                        "Creazione account Supabase..."
                    ):
                        result = approve_access_request(
                            request_row["request_id"],
                            assigned_role,
                            review_notes,
                        )
                except AccessRequestError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(
                        "Errore durante l'approvazione della richiesta."
                    )
                    with st.expander("Dettaglio tecnico"):
                        st.code(f"{type(exc).__name__}: {exc}")
                else:
                    st.success(
                        "Account creato correttamente. "
                        "Copia ora la password temporanea: "
                        "non viene memorizzata nell'app."
                    )
                    st.write(f"**Email:** {result['email']}")
                    st.write(
                        f"**Ruolo:** "
                        f"{ROLE_LABELS.get(result['role_id'], result['role_id'])}"
                    )
                    st.code(result["temporary_password"])
                    st.warning(
                        "Comunica la password temporanea all'utente "
                        "tramite un canale sicuro. Al primo accesso "
                        "l'app obbligherà l'utente a sceglierne una nuova."
                    )

        with reject_col:
            if st.button(
                "❌ Rifiuta richiesta",
                use_container_width=True,
                key=f"reject_{request_row['request_id']}",
            ):
                try:
                    reject_access_request(
                        request_row["request_id"],
                        review_notes,
                    )
                except AccessRequestError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error(
                        "Errore durante il rifiuto della richiesta."
                    )
                else:
                    st.success("Richiesta rifiutata.")
                    st.rerun()


with tab_users:
    users = list_user_profiles()

    if not users:
        st.info("Nessun profilo utente disponibile.")
    else:
        user_table = pd.DataFrame(
            [
                {
                    "Nome": u["display_name"],
                    "Email": u["email"],
                    "Azienda": u.get("organization_name") or "",
                    "Ruolo": ROLE_LABELS.get(
                        u["role_id"],
                        u["role_id"],
                    ),
                    "Attivo": bool(u["active"]),
                    "Cambio password": bool(
                        u.get("must_change_password")
                    ),
                }
                for u in users
            ]
        )

        st.dataframe(
            user_table,
            hide_index=True,
            use_container_width=True,
        )

        current = current_user() or {}

        editable_users = [
            u
            for u in users
            if str(u["user_id"]) != str(current.get("user_id"))
        ]

        if editable_users:
            user_options = {
                f"{u['display_name']} · {u['email']}": u
                for u in editable_users
            }

            selected_user_label = st.selectbox(
                "Utente da modificare",
                list(user_options.keys()),
            )
            selected_user = user_options[selected_user_label]

            roles = list_roles()
            role_ids = [r["role_id"] for r in roles]
            role_labels = {
                r["role_id"]: r["display_name"]
                for r in roles
            }

            role_index = (
                role_ids.index(selected_user["role_id"])
                if selected_user["role_id"] in role_ids
                else 0
            )

            edit_role = st.selectbox(
                "Ruolo",
                role_ids,
                index=role_index,
                format_func=lambda role: role_labels.get(
                    role,
                    role,
                ),
                key=f"edit_role_{selected_user['user_id']}",
            )

            edit_org = st.text_input(
                "Azienda / organizzazione",
                value=selected_user.get("organization_name") or "",
                max_chars=200,
                key=f"edit_org_{selected_user['user_id']}",
            )

            edit_active = st.checkbox(
                "Account attivo",
                value=bool(selected_user["active"]),
                key=f"edit_active_{selected_user['user_id']}",
            )

            if st.button(
                "Salva modifiche utente",
                type="primary",
                key=f"save_user_{selected_user['user_id']}",
            ):
                try:
                    update_user_profile(
                        str(selected_user["user_id"]),
                        role_id=edit_role,
                        active=edit_active,
                        organization_name=edit_org,
                    )
                except AccessRequestError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error(
                        "Errore durante l'aggiornamento dell'utente."
                    )
                else:
                    st.success("Profilo utente aggiornato.")
                    st.rerun()

        st.caption(
            "Il tuo stesso account Admin non è modificabile da questa "
            "schermata, per evitare disattivazioni accidentali."
        )


with tab_history:
    history = list_access_requests()

    if not history:
        st.info("Nessuna richiesta registrata.")
    else:
        history_table = pd.DataFrame(
            [
                {
                    "ID": r["request_id"],
                    "Richiesta": r["requested_at"],
                    "Nome": r["display_name"],
                    "Email": r["email"],
                    "Azienda": r.get("company") or "",
                    "Richiesto": ROLE_LABELS.get(
                        r["requested_role"],
                        r["requested_role"],
                    ),
                    "Stato": r["status"],
                    "Assegnato": ROLE_LABELS.get(
                        r.get("assigned_role"),
                        r.get("assigned_role") or "",
                    ),
                    "Revisionata": r.get("reviewed_at"),
                    "Admin": r.get("reviewer_email") or "",
                }
                for r in history
            ]
        )

        st.dataframe(
            history_table,
            hide_index=True,
            use_container_width=True,
        )

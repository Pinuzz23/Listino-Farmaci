from __future__ import annotations

import re
import time
from typing import Any

import psycopg2
import requests
import streamlit as st
from psycopg2.extras import Json, RealDictCursor

from modules.db_backend import get_postgres_url


_SESSION_KEY = "_listino_auth_session"
_CONTEXT_KEY = "_listino_auth_context"
_REQUEST_PANEL_KEY = "_listino_show_access_request"


class AuthError(RuntimeError):
    pass


def _supabase_auth_settings() -> tuple[str, str]:
    try:
        cfg = st.secrets["supabase"]
        url = str(cfg["url"]).strip().rstrip("/")
        publishable_key = str(cfg["publishable_key"]).strip()
    except Exception as exc:
        raise AuthError(
            "Configurazione Supabase Auth mancante. "
            "Verifica [supabase].url e [supabase].publishable_key nei Secrets."
        ) from exc

    if not url or not publishable_key:
        raise AuthError(
            "Configurazione Supabase Auth incompleta nei Secrets."
        )

    return url, publishable_key


def _connect():
    url = get_postgres_url()
    if not url:
        raise AuthError("Connessione PostgreSQL non configurata.")

    return psycopg2.connect(
        url,
        connect_timeout=10,
        sslmode="require",
        application_name="listino-farmaci-auth",
    )


def _auth_headers(
    access_token: str | None = None,
) -> dict[str, str]:
    _, publishable_key = _supabase_auth_settings()

    headers = {
        "apikey": publishable_key,
        "Content-Type": "application/json",
    }

    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    return headers


def _safe_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}"

    for key in ("msg", "message", "error_description", "error"):
        value = payload.get(key)
        if value:
            return str(value)[:300]

    return f"HTTP {response.status_code}"


def _normalize_session(payload: dict[str, Any]) -> dict[str, Any]:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    user = payload.get("user") or {}

    if not access_token or not refresh_token:
        raise AuthError("Supabase non ha restituito una sessione valida.")

    expires_in = int(payload.get("expires_in") or 3600)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + max(expires_in - 60, 60),
        "user": user,
    }


def _load_profile(user_id: str) -> dict[str, Any] | None:
    conn = _connect()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    up.user_id,
                    up.email,
                    up.display_name,
                    up.organization_name,
                    up.role_id,
                    up.active,
                    up.customer_id,
                    up.must_change_password
                FROM public.user_profiles up
                WHERE up.user_id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            row = cur.fetchone()

            if not row:
                return None

            profile = dict(row)

            cur.execute(
                """
                SELECT rp.permission_id
                FROM public.role_permissions rp
                WHERE rp.role_id = %s
                ORDER BY rp.permission_id
                """,
                (profile["role_id"],),
            )

            profile["permissions"] = [
                r["permission_id"] for r in cur.fetchall()
            ]

            return profile

    finally:
        conn.close()


def _profile_to_context(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(profile["user_id"]),
        "email": profile["email"],
        "display_name": profile["display_name"],
        "organization_name": profile.get("organization_name"),
        "role_id": profile["role_id"],
        "active": bool(profile["active"]),
        "customer_id": profile.get("customer_id"),
        "must_change_password": bool(
            profile.get("must_change_password")
        ),
        "permissions": sorted(
            set(profile.get("permissions") or [])
        ),
    }


def audit(
    action: str,
    *,
    success: bool = True,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    actor_role: str | None = None,
) -> None:
    if actor_user_id is None or actor_email is None or actor_role is None:
        context = st.session_state.get(_CONTEXT_KEY) or {}
        actor_user_id = actor_user_id or context.get("user_id")
        actor_email = actor_email or context.get("email")
        actor_role = actor_role or context.get("role_id")

    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.audit_log (
                        actor_user_id,
                        actor_email,
                        actor_role,
                        action,
                        entity_type,
                        entity_id,
                        success,
                        details,
                        created_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    """,
                    (
                        actor_user_id,
                        actor_email,
                        actor_role,
                        action,
                        entity_type,
                        entity_id,
                        success,
                        Json(details or {}),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def sign_in(email: str, password: str) -> dict[str, Any]:
    email = str(email or "").strip().lower()
    password = str(password or "")

    if not email or not password:
        raise AuthError("Inserisci email e password.")

    url, _ = _supabase_auth_settings()

    try:
        response = requests.post(
            f"{url}/auth/v1/token?grant_type=password",
            headers=_auth_headers(),
            json={
                "email": email,
                "password": password,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise AuthError(
            "Servizio di autenticazione temporaneamente non raggiungibile."
        ) from exc

    if response.status_code != 200:
        raw = _safe_error_message(response).lower()

        audit(
            "LOGIN_FAILED",
            success=False,
            actor_email=email,
            details={
                "status_code": response.status_code,
                "reason": _safe_error_message(response),
            },
        )

        if "email not confirmed" in raw:
            raise AuthError(
                "L'indirizzo email non risulta ancora confermato."
            )

        raise AuthError("Email o password non corrette.")

    session = _normalize_session(response.json())
    auth_user = session.get("user") or {}
    user_id = auth_user.get("id")

    if not user_id:
        raise AuthError("Identità utente non valida.")

    profile = _load_profile(user_id)

    if not profile:
        _remote_sign_out(session["access_token"])
        audit(
            "LOGIN_DENIED",
            success=False,
            actor_user_id=user_id,
            actor_email=email,
            details={"reason": "missing_user_profile"},
        )
        raise AuthError(
            "Utente autenticato ma non abilitato all'applicazione. "
            "Contatta un amministratore."
        )

    if not profile.get("active"):
        _remote_sign_out(session["access_token"])
        audit(
            "LOGIN_DENIED",
            success=False,
            actor_user_id=str(profile["user_id"]),
            actor_email=profile.get("email"),
            actor_role=profile.get("role_id"),
            details={"reason": "inactive_profile"},
        )
        raise AuthError(
            "Account disabilitato. Contatta un amministratore."
        )

    permissions = set(profile.get("permissions") or [])

    if "access_app" not in permissions:
        _remote_sign_out(session["access_token"])
        audit(
            "LOGIN_DENIED",
            success=False,
            actor_user_id=str(profile["user_id"]),
            actor_email=profile.get("email"),
            actor_role=profile.get("role_id"),
            details={"reason": "missing_access_app"},
        )
        raise AuthError("Account non autorizzato ad accedere all'app.")

    context = _profile_to_context(profile)

    st.session_state[_SESSION_KEY] = session
    st.session_state[_CONTEXT_KEY] = context

    audit(
        "LOGIN",
        success=True,
        actor_user_id=context["user_id"],
        actor_email=context["email"],
        actor_role=context["role_id"],
    )

    return context


def _refresh_session() -> dict[str, Any]:
    current = st.session_state.get(_SESSION_KEY)

    if not current or not current.get("refresh_token"):
        raise AuthError("Sessione non disponibile.")

    url, _ = _supabase_auth_settings()

    try:
        response = requests.post(
            f"{url}/auth/v1/token?grant_type=refresh_token",
            headers=_auth_headers(),
            json={
                "refresh_token": current["refresh_token"],
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        raise AuthError("Impossibile aggiornare la sessione.") from exc

    if response.status_code != 200:
        clear_local_session()
        raise AuthError(
            "Sessione scaduta. Effettua nuovamente l'accesso."
        )

    refreshed = _normalize_session(response.json())
    st.session_state[_SESSION_KEY] = refreshed

    return refreshed


def _remote_sign_out(access_token: str | None) -> None:
    if not access_token:
        return

    try:
        url, _ = _supabase_auth_settings()
        requests.post(
            f"{url}/auth/v1/logout",
            headers=_auth_headers(access_token),
            timeout=10,
        )
    except Exception:
        pass


def sign_out() -> None:
    context = st.session_state.get(_CONTEXT_KEY) or {}
    session = st.session_state.get(_SESSION_KEY) or {}

    audit(
        "LOGOUT",
        success=True,
        actor_user_id=context.get("user_id"),
        actor_email=context.get("email"),
        actor_role=context.get("role_id"),
    )

    _remote_sign_out(session.get("access_token"))
    clear_local_session()


def clear_local_session() -> None:
    st.session_state.pop(_SESSION_KEY, None)
    st.session_state.pop(_CONTEXT_KEY, None)


def current_user() -> dict[str, Any] | None:
    session = st.session_state.get(_SESSION_KEY)
    context = st.session_state.get(_CONTEXT_KEY)

    if not session or not context:
        return None

    try:
        if float(session.get("expires_at") or 0) <= time.time():
            _refresh_session()
    except Exception:
        clear_local_session()
        return None

    try:
        profile = _load_profile(context["user_id"])
    except Exception:
        return context

    if not profile or not profile.get("active"):
        sign_out()
        return None

    permissions = set(profile.get("permissions") or [])

    if "access_app" not in permissions:
        sign_out()
        return None

    refreshed_context = _profile_to_context(profile)
    st.session_state[_CONTEXT_KEY] = refreshed_context

    return refreshed_context


def is_authenticated() -> bool:
    return current_user() is not None


def has_permission(permission_id: str) -> bool:
    user = current_user()
    if not user:
        return False

    return permission_id in set(user.get("permissions") or [])


def require_permission(
    permission_id: str,
    message: str = "Non hai i privilegi necessari per questa operazione.",
) -> None:
    if not has_permission(permission_id):
        st.error(message)
        st.stop()


def _validate_new_password(password: str) -> None:
    if len(password) < 12:
        raise AuthError(
            "La nuova password deve contenere almeno 12 caratteri."
        )

    rules = (
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    )

    if not all(rules):
        raise AuthError(
            "Usa almeno una minuscola, una maiuscola, un numero "
            "e un carattere speciale."
        )


def change_password(
    new_password: str,
    confirm_password: str,
) -> None:
    if new_password != confirm_password:
        raise AuthError("Le due password non coincidono.")

    _validate_new_password(new_password)

    session = st.session_state.get(_SESSION_KEY)
    context = st.session_state.get(_CONTEXT_KEY)

    if not session or not context:
        raise AuthError("Sessione non disponibile.")

    if float(session.get("expires_at") or 0) <= time.time():
        session = _refresh_session()

    access_token = session.get("access_token")
    url, _ = _supabase_auth_settings()

    try:
        response = requests.put(
            f"{url}/auth/v1/user",
            headers=_auth_headers(access_token),
            json={"password": new_password},
            timeout=20,
        )
    except requests.RequestException as exc:
        raise AuthError(
            "Servizio di autenticazione temporaneamente non raggiungibile."
        ) from exc

    if response.status_code not in (200, 201):
        raise AuthError(
            "Non è stato possibile aggiornare la password: "
            + _safe_error_message(response)
        )

    conn = _connect()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.user_profiles
                SET
                    must_change_password = FALSE,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (context["user_id"],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    context["must_change_password"] = False
    st.session_state[_CONTEXT_KEY] = context

    audit(
        "PASSWORD_CHANGED",
        entity_type="user",
        entity_id=context["user_id"],
    )


def render_force_password_change() -> None:
    user = st.session_state.get(_CONTEXT_KEY) or {}

    st.markdown(
        """
        <div style="
            max-width: 580px;
            margin: 4rem auto 1rem auto;
            text-align: center;
        ">
            <div style="font-size:3rem;">🔐</div>
            <h1 style="margin-bottom:.25rem;">Imposta la tua password</h1>
            <p style="opacity:.7;margin-top:0;">
                Per sicurezza devi sostituire la password temporanea
                prima di utilizzare l'app.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, center, right = st.columns([1, 1.2, 1])

    with center:
        st.caption(user.get("email", ""))

        with st.form("force_password_change"):
            password = st.text_input(
                "Nuova password",
                type="password",
                autocomplete="new-password",
            )
            confirm = st.text_input(
                "Conferma nuova password",
                type="password",
                autocomplete="new-password",
            )

            st.caption(
                "Minimo 12 caratteri, con maiuscola, minuscola, "
                "numero e carattere speciale."
            )

            submitted = st.form_submit_button(
                "Salva nuova password",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            try:
                with st.spinner("Aggiornamento password..."):
                    change_password(password, confirm)
            except AuthError as exc:
                st.error(str(exc))
            except Exception:
                st.error(
                    "Non è stato possibile aggiornare la password."
                )
            else:
                st.success("Password aggiornata correttamente.")
                st.rerun()

        if st.button(
            "Esci",
            key="logout_from_password_change",
            use_container_width=True,
        ):
            sign_out()
            st.rerun()


def render_login() -> None:
    st.markdown(
        """
        <div style="
            max-width: 520px;
            margin: 4rem auto 1.5rem auto;
            text-align: center;
        ">
            <div style="font-size:3rem;">💊</div>
            <h1 style="margin-bottom:.25rem;">Listino Farmaci</h1>
            <p style="opacity:.7;margin-top:0;">
                Accedi con le tue credenziali
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, center, right = st.columns([1, 1.15, 1])

    with center:
        with st.form("login_form", clear_on_submit=False):
            email = st.text_input(
                "Email",
                autocomplete="email",
            )
            password = st.text_input(
                "Password",
                type="password",
                autocomplete="current-password",
            )

            submitted = st.form_submit_button(
                "Accedi",
                type="primary",
                use_container_width=True,
            )

        if submitted:
            try:
                with st.spinner("Accesso in corso..."):
                    sign_in(email, password)
            except AuthError as exc:
                st.error(str(exc))
            except Exception:
                st.error(
                    "Non è stato possibile completare l'accesso. "
                    "Riprova tra poco."
                )
            else:
                st.rerun()

        st.markdown("---")
        st.caption("Non hai ancora un account?")

        show_request = bool(
            st.session_state.get(_REQUEST_PANEL_KEY, False)
        )

        request_label = (
            "Chiudi richiesta accesso"
            if show_request
            else "📝 Richiedi accesso"
        )

        if st.button(
            request_label,
            use_container_width=True,
            key="toggle_access_request",
        ):
            st.session_state[_REQUEST_PANEL_KEY] = not show_request
            st.rerun()

        if st.session_state.get(_REQUEST_PANEL_KEY, False):
            _render_access_request_form()


def _render_access_request_form() -> None:
    from modules.access_requests import (
        AccessRequestError,
        submit_access_request,
    )

    st.subheader("Richiesta di accesso")
    st.caption(
        "La richiesta sarà valutata da un amministratore. "
        "Il ruolo definitivo viene assegnato dall'Admin."
    )

    role_options = {
        "Buyer": "BUYER",
        "Customer Care": "CUSTOMER_CARE",
        "Cliente": "CLIENTE",
    }

    with st.form("access_request_form"):
        display_name = st.text_input(
            "Nome e cognome",
            max_chars=160,
        )
        request_email = st.text_input(
            "Email",
            max_chars=254,
            autocomplete="email",
        )
        company = st.text_input(
            "Azienda / organizzazione",
            max_chars=200,
        )
        role_label = st.selectbox(
            "Profilo richiesto",
            list(role_options.keys()),
        )
        notes = st.text_area(
            "Note / motivo della richiesta",
            max_chars=2000,
            height=90,
        )

        submitted = st.form_submit_button(
            "Invia richiesta",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        try:
            submit_access_request(
                email=request_email,
                display_name=display_name,
                company=company,
                requested_role=role_options[role_label],
                notes=notes,
            )
        except AccessRequestError as exc:
            st.error(str(exc))
        except Exception:
            st.error(
                "Non è stato possibile inviare la richiesta. "
                "Riprova tra poco."
            )
        else:
            st.success(
                "Richiesta ricevuta. Se l'indirizzo non possiede già "
                "un account o una richiesta in attesa, sarà presa in "
                "carico da un amministratore."
            )

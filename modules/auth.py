from __future__ import annotations

import time
from typing import Any

import psycopg2
import requests
import streamlit as st
from psycopg2.extras import Json, RealDictCursor

from modules.db_backend import get_postgres_url


_SESSION_KEY = "_listino_auth_session"
_CONTEXT_KEY = "_listino_auth_context"


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
    """
    Restituisce un messaggio utile senza esporre token o segreti.
    """
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
                    up.role_id,
                    up.active,
                    up.customer_id
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
    """
    Audit best-effort: un problema nel log non deve bloccare l'app.
    """
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
        audit(
            "LOGIN_FAILED",
            success=False,
            actor_email=email,
            details={
                "status_code": response.status_code,
                "reason": _safe_error_message(response),
            },
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

    context = {
        "user_id": str(profile["user_id"]),
        "email": profile["email"],
        "display_name": profile["display_name"],
        "role_id": profile["role_id"],
        "active": bool(profile["active"]),
        "customer_id": profile.get("customer_id"),
        "permissions": sorted(permissions),
    }

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
        raise AuthError("Sessione scaduta. Effettua nuovamente l'accesso.")

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

    # Ricarica il profilo dal DB a ogni rerun.
    # In questo modo cambio ruolo/disattivazione hanno effetto subito.
    try:
        profile = _load_profile(context["user_id"])
    except Exception:
        # Se PostgreSQL ha un problema temporaneo manteniamo la sessione,
        # ma non inventiamo permessi nuovi.
        return context

    if not profile or not profile.get("active"):
        sign_out()
        return None

    permissions = sorted(set(profile.get("permissions") or []))

    if "access_app" not in permissions:
        sign_out()
        return None

    refreshed_context = {
        "user_id": str(profile["user_id"]),
        "email": profile["email"],
        "display_name": profile["display_name"],
        "role_id": profile["role_id"],
        "active": bool(profile["active"]),
        "customer_id": profile.get("customer_id"),
        "permissions": permissions,
    }

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


def render_login() -> None:
    st.markdown(
        """
        <div style="
            max-width: 520px;
            margin: 5rem auto 1.5rem auto;
            text-align: center;
        ">
            <div style="font-size:3rem;">💊</div>
            <h1 style="margin-bottom:.25rem;">Listino Farmaci</h1>
            <p style="opacity:.7;margin-top:0;">
                Accedi con le credenziali aziendali
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

from __future__ import annotations

import re
import secrets
import string
from typing import Any

import psycopg2
import requests
import streamlit as st
from psycopg2.extras import RealDictCursor

from modules.db_backend import get_postgres_url


ALLOWED_REQUESTED_ROLES = {"BUYER", "CUSTOMER_CARE", "CLIENTE"}

ROLE_LABELS = {
    "ADMIN": "Admin",
    "BUYER": "Buyer",
    "CUSTOMER_CARE": "Customer Care",
    "CLIENTE": "Cliente",
}


class AccessRequestError(RuntimeError):
    pass


def _connect():
    url = get_postgres_url()
    if not url:
        raise AccessRequestError("Connessione PostgreSQL non configurata.")

    return psycopg2.connect(
        url,
        connect_timeout=10,
        sslmode="require",
        application_name="listino-farmaci-access-requests",
    )


def _normalize_email(email: str) -> str:
    value = str(email or "").strip().lower()

    if len(value) > 254:
        raise AccessRequestError("Indirizzo email non valido.")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise AccessRequestError("Inserisci un indirizzo email valido.")

    return value


def _clean_text(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    return text[:max_len]


def submit_access_request(
    *,
    email: str,
    display_name: str,
    company: str,
    requested_role: str,
    notes: str = "",
) -> dict[str, Any]:
    """
    Registra una richiesta pubblica.

    Per evitare user-enumeration, se l'email possiede già un account
    o una richiesta PENDING restituisce comunque un esito generico positivo.
    """
    email = _normalize_email(email)
    display_name = _clean_text(display_name, 160)
    company = _clean_text(company, 200)
    notes = _clean_text(notes, 2000)
    requested_role = str(requested_role or "").strip().upper()

    if len(display_name) < 2:
        raise AccessRequestError("Inserisci nome e cognome.")

    if requested_role not in ALLOWED_REQUESTED_ROLES:
        raise AccessRequestError("Profilo richiesto non valido.")

    conn = _connect()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT user_id
                FROM public.user_profiles
                WHERE LOWER(email) = LOWER(%s)
                LIMIT 1
                """,
                (email,),
            )
            if cur.fetchone():
                return {"accepted": True, "created": False}

            cur.execute(
                """
                SELECT request_id
                FROM public.access_requests
                WHERE LOWER(email) = LOWER(%s)
                  AND status = 'PENDING'
                LIMIT 1
                """,
                (email,),
            )
            if cur.fetchone():
                return {"accepted": True, "created": False}

            cur.execute(
                """
                INSERT INTO public.access_requests (
                    email,
                    display_name,
                    company,
                    requested_role,
                    notes,
                    status,
                    requested_at
                )
                VALUES (%s, %s, %s, %s, %s, 'PENDING', NOW())
                RETURNING request_id
                """,
                (
                    email,
                    display_name,
                    company or None,
                    requested_role,
                    notes or None,
                ),
            )
            row = cur.fetchone()

        conn.commit()

        return {
            "accepted": True,
            "created": True,
            "request_id": row["request_id"],
        }

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return {"accepted": True, "created": False}
    finally:
        conn.close()


def list_access_requests(
    status: str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if status:
                cur.execute(
                    """
                    SELECT
                        ar.*,
                        reviewer.email AS reviewer_email
                    FROM public.access_requests ar
                    LEFT JOIN public.user_profiles reviewer
                      ON reviewer.user_id = ar.reviewed_by
                    WHERE ar.status = %s
                    ORDER BY ar.requested_at DESC
                    """,
                    (status.upper(),),
                )
            else:
                cur.execute(
                    """
                    SELECT
                        ar.*,
                        reviewer.email AS reviewer_email
                    FROM public.access_requests ar
                    LEFT JOIN public.user_profiles reviewer
                      ON reviewer.user_id = ar.reviewed_by
                    ORDER BY ar.requested_at DESC
                    """
                )

            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_user_profiles() -> list[dict[str, Any]]:
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
                    up.must_change_password,
                    up.created_at,
                    up.updated_at
                FROM public.user_profiles up
                ORDER BY up.display_name, up.email
                """
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def list_roles() -> list[dict[str, Any]]:
    conn = _connect()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT role_id, display_name, description
                FROM public.roles
                ORDER BY
                    CASE role_id
                        WHEN 'ADMIN' THEN 1
                        WHEN 'BUYER' THEN 2
                        WHEN 'CUSTOMER_CARE' THEN 3
                        WHEN 'CLIENTE' THEN 4
                        ELSE 99
                    END,
                    role_id
                """
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _require_admin_context() -> dict[str, Any]:
    from modules.auth import current_user, has_permission

    user = current_user()

    if not user or not has_permission("manage_users"):
        raise AccessRequestError(
            "Non hai i privilegi necessari per gestire gli utenti."
        )

    return user


def _supabase_admin_settings() -> tuple[str, str]:
    try:
        cfg = st.secrets["supabase"]
        url = str(cfg["url"]).strip().rstrip("/")
        secret_key = str(cfg["secret_key"]).strip()
    except Exception as exc:
        raise AccessRequestError(
            "Configurazione Supabase Admin mancante nei Secrets."
        ) from exc

    if not url or not secret_key:
        raise AccessRequestError(
            "Configurazione Supabase Admin incompleta."
        )

    return url, secret_key


def _admin_headers() -> dict[str, str]:
    _, secret_key = _supabase_admin_settings()

    return {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }


def _safe_response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}"

    for key in ("msg", "message", "error_description", "error"):
        value = payload.get(key)
        if value:
            return str(value)[:400]

    return f"HTTP {response.status_code}"


def _generate_temporary_password(length: int = 18) -> str:
    """
    Password casuale mostrata una sola volta all'Admin.
    Non viene mai salvata nel database.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_"

    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))

        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%*-_" for c in password)
        ):
            return password


def _delete_auth_user_best_effort(user_id: str) -> None:
    try:
        url, _ = _supabase_admin_settings()
        requests.delete(
            f"{url}/auth/v1/admin/users/{user_id}",
            headers=_admin_headers(),
            timeout=15,
        )
    except Exception:
        pass


def approve_access_request(
    request_id: int,
    assigned_role: str,
    review_notes: str = "",
) -> dict[str, Any]:
    """
    Approva la richiesta, crea l'utente Supabase Auth con password temporanea,
    crea user_profiles e impone il cambio password al primo accesso.
    """
    admin = _require_admin_context()
    assigned_role = str(assigned_role or "").strip().upper()
    review_notes = _clean_text(review_notes, 2000)

    roles = {row["role_id"] for row in list_roles()}
    if assigned_role not in roles:
        raise AccessRequestError("Ruolo assegnato non valido.")

    conn = _connect()
    auth_user_id: str | None = None
    temp_password: str | None = None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM public.access_requests
                WHERE request_id = %s
                FOR UPDATE
                """,
                (int(request_id),),
            )
            request_row = cur.fetchone()

            if not request_row:
                raise AccessRequestError("Richiesta non trovata.")

            if request_row["status"] != "PENDING":
                raise AccessRequestError(
                    "La richiesta è già stata gestita."
                )

            email = _normalize_email(request_row["email"])
            display_name = _clean_text(request_row["display_name"], 160)
            company = _clean_text(request_row["company"], 200)

            cur.execute(
                """
                SELECT user_id
                FROM public.user_profiles
                WHERE LOWER(email) = LOWER(%s)
                LIMIT 1
                """,
                (email,),
            )
            if cur.fetchone():
                raise AccessRequestError(
                    "Esiste già un profilo applicativo per questa email."
                )

            temp_password = _generate_temporary_password()
            supabase_url, _ = _supabase_admin_settings()

            try:
                response = requests.post(
                    f"{supabase_url}/auth/v1/admin/users",
                    headers=_admin_headers(),
                    json={
                        "email": email,
                        "password": temp_password,
                        "email_confirm": True,
                        "user_metadata": {
                            "display_name": display_name,
                            "organization_name": company or None,
                            "access_request_id": int(request_id),
                        },
                    },
                    timeout=20,
                )
            except requests.RequestException as exc:
                raise AccessRequestError(
                    "Supabase Auth non è raggiungibile al momento."
                ) from exc

            if response.status_code not in (200, 201):
                raise AccessRequestError(
                    "Creazione utente Supabase non riuscita: "
                    + _safe_response_error(response)
                )

            payload = response.json()
            auth_user = (
                payload.get("user")
                if isinstance(payload, dict)
                else None
            )

            if not auth_user and isinstance(payload, dict):
                auth_user = payload

            auth_user_id = str(
                (auth_user or {}).get("id") or ""
            ).strip()

            if not auth_user_id:
                raise AccessRequestError(
                    "Supabase non ha restituito l'ID del nuovo utente."
                )

            cur.execute(
                """
                INSERT INTO public.user_profiles (
                    user_id,
                    email,
                    display_name,
                    organization_name,
                    role_id,
                    active,
                    customer_id,
                    must_change_password,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    TRUE, NULL, TRUE, NOW(), NOW()
                )
                """,
                (
                    auth_user_id,
                    email,
                    display_name,
                    company or None,
                    assigned_role,
                ),
            )

            cur.execute(
                """
                UPDATE public.access_requests
                SET
                    status = 'APPROVED',
                    assigned_role = %s,
                    auth_user_id = %s,
                    review_notes = %s,
                    reviewed_at = NOW(),
                    reviewed_by = %s
                WHERE request_id = %s
                """,
                (
                    assigned_role,
                    auth_user_id,
                    review_notes or None,
                    admin["user_id"],
                    int(request_id),
                ),
            )

        conn.commit()

    except Exception:
        conn.rollback()

        if auth_user_id:
            _delete_auth_user_best_effort(auth_user_id)

        raise
    finally:
        conn.close()

    from modules.auth import audit

    audit(
        "ACCESS_REQUEST_APPROVED",
        entity_type="access_request",
        entity_id=str(request_id),
        details={
            "new_user_id": auth_user_id,
            "assigned_role": assigned_role,
        },
    )

    audit(
        "USER_CREATE",
        entity_type="user",
        entity_id=auth_user_id,
        details={
            "role_id": assigned_role,
            "source": "access_request",
        },
    )

    return {
        "user_id": auth_user_id,
        "email": email,
        "display_name": display_name,
        "role_id": assigned_role,
        "temporary_password": temp_password,
    }


def reject_access_request(
    request_id: int,
    review_notes: str = "",
) -> None:
    admin = _require_admin_context()
    review_notes = _clean_text(review_notes, 2000)

    conn = _connect()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE public.access_requests
                SET
                    status = 'REJECTED',
                    review_notes = %s,
                    reviewed_at = NOW(),
                    reviewed_by = %s
                WHERE request_id = %s
                  AND status = 'PENDING'
                RETURNING request_id
                """,
                (
                    review_notes or None,
                    admin["user_id"],
                    int(request_id),
                ),
            )

            if not cur.fetchone():
                raise AccessRequestError(
                    "Richiesta non trovata o già gestita."
                )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    from modules.auth import audit

    audit(
        "ACCESS_REQUEST_REJECTED",
        entity_type="access_request",
        entity_id=str(request_id),
        details={"review_notes": review_notes or None},
    )


def update_user_profile(
    user_id: str,
    *,
    role_id: str,
    active: bool,
    organization_name: str = "",
) -> None:
    admin = _require_admin_context()
    user_id = str(user_id)
    role_id = str(role_id or "").strip().upper()
    organization_name = _clean_text(organization_name, 200)

    if user_id == str(admin["user_id"]):
        raise AccessRequestError(
            "Per sicurezza non puoi modificare il tuo stesso profilo "
            "da questa schermata."
        )

    roles = {row["role_id"] for row in list_roles()}
    if role_id not in roles:
        raise AccessRequestError("Ruolo non valido.")

    conn = _connect()

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE public.user_profiles
                SET
                    role_id = %s,
                    active = %s,
                    organization_name = %s,
                    updated_at = NOW()
                WHERE user_id = %s
                RETURNING email
                """,
                (
                    role_id,
                    bool(active),
                    organization_name or None,
                    user_id,
                ),
            )
            row = cur.fetchone()

            if not row:
                raise AccessRequestError("Utente non trovato.")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    from modules.auth import audit

    audit(
        "USER_PROFILE_UPDATE",
        entity_type="user",
        entity_id=user_id,
        details={
            "role_id": role_id,
            "active": bool(active),
            "organization_name": organization_name or None,
        },
    )

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import psycopg2
import requests
import streamlit as st
from psycopg2.extras import Json, RealDictCursor

from modules.db_backend import get_postgres_url


def _supabase_settings() -> tuple[str, str, str]:
    try:
        cfg = st.secrets["supabase"]
        url = str(cfg["url"]).rstrip("/")
        secret_key = str(cfg["secret_key"]).strip()
        bucket = str(cfg["storage_bucket"]).strip()
    except Exception as exc:
        raise RuntimeError(
            "Configurazione [supabase] mancante o incompleta nei Secrets."
        ) from exc

    if not url or not secret_key or not bucket:
        raise RuntimeError("Configurazione [supabase] incompleta nei Secrets.")

    return url, secret_key, bucket


def _storage_headers(content_type: str | None = None) -> dict[str, str]:
    _, secret_key, _ = _supabase_settings()
    headers = {
        "apikey": secret_key,
        "Authorization": f"Bearer {secret_key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _connect():
    url = get_postgres_url()
    if not url:
        raise RuntimeError("Connessione PostgreSQL non configurata.")
    return psycopg2.connect(
        url,
        connect_timeout=10,
        sslmode="require",
        application_name="listino-farmaci-documents",
    )


def normalize_aic9(aic: Any) -> str:
    if aic is None:
        raise ValueError("AIC assente.")

    text = str(aic).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]

    digits = re.sub(r"\D", "", text)
    if not digits:
        raise ValueError("AIC non valido.")
    if len(digits) > 9:
        raise ValueError(f"AIC non valido: {aic!r}")

    return digits.zfill(9)


def aic6_from_aic(aic: Any) -> str:
    return normalize_aic9(aic)[:6]


def _validate_pdf(pdf_bytes: bytes) -> None:
    if not pdf_bytes:
        raise ValueError("PDF vuoto.")
    if not pdf_bytes.lstrip().startswith(b"%PDF-"):
        raise ValueError("Il contenuto ricevuto non sembra essere un PDF.")


def _storage_path(aic6: str, document_type: str, sha256: str) -> str:
    return f"{document_type.lower()}/{aic6}/{sha256}.pdf"


def _upload_pdf(storage_path: str, pdf_bytes: bytes) -> None:
    project_url, _, bucket = _supabase_settings()
    encoded_bucket = quote(bucket, safe="")
    encoded_path = quote(storage_path, safe="/")

    response = requests.post(
        f"{project_url}/storage/v1/object/{encoded_bucket}/{encoded_path}",
        headers={
            **_storage_headers("application/pdf"),
            "x-upsert": "true",
            "cache-control": "3600",
        },
        data=pdf_bytes,
        timeout=60,
    )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            "Upload Supabase Storage fallito "
            f"(HTTP {response.status_code}): {response.text[:500]}"
        )


def _delete_storage_object(storage_path: str) -> None:
    project_url, _, bucket = _supabase_settings()
    encoded_bucket = quote(bucket, safe="")
    encoded_path = quote(storage_path, safe="/")
    try:
        requests.delete(
            f"{project_url}/storage/v1/object/{encoded_bucket}/{encoded_path}",
            headers=_storage_headers(),
            timeout=30,
        )
    except Exception:
        pass


def create_signed_url(
    storage_path: str,
    expires_in: int = 900,
    download: bool = False,
) -> str:
    project_url, _, bucket = _supabase_settings()
    encoded_bucket = quote(bucket, safe="")
    encoded_path = quote(storage_path, safe="/")

    response = requests.post(
        f"{project_url}/storage/v1/object/sign/{encoded_bucket}/{encoded_path}",
        headers=_storage_headers("application/json"),
        json={"expiresIn": int(expires_in)},
        timeout=30,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Creazione signed URL fallita "
            f"(HTTP {response.status_code}): {response.text[:500]}"
        )

    payload = response.json()
    signed = (
        payload.get("signedURL")
        or payload.get("signedUrl")
        or payload.get("signed_url")
    )
    if not signed:
        raise RuntimeError("Supabase non ha restituito una signed URL.")

    if signed.startswith("http://") or signed.startswith("https://"):
        url = signed
    elif signed.startswith("/storage/v1/"):
        url = f"{project_url}{signed}"
    elif signed.startswith("/object/"):
        url = f"{project_url}/storage/v1{signed}"
    else:
        url = f"{project_url}/storage/v1/{signed.lstrip('/')}"

    if download:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}download=1"

    return url


def download_pdf(storage_path: str) -> bytes:
    project_url, _, bucket = _supabase_settings()
    encoded_bucket = quote(bucket, safe="")
    encoded_path = quote(storage_path, safe="/")

    response = requests.get(
        f"{project_url}/storage/v1/object/authenticated/"
        f"{encoded_bucket}/{encoded_path}",
        headers=_storage_headers(),
        timeout=60,
    )

    if response.status_code != 200:
        raise RuntimeError(
            "Download Supabase Storage fallito "
            f"(HTTP {response.status_code}): {response.text[:500]}"
        )
    return response.content


def save_product_document(
    *,
    aic: Any,
    document_type: str,
    pdf_bytes: bytes,
    source_url: str | None = None,
    document_date: date | str | None = None,
    source: str = "AIFA",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document_type = str(document_type).strip().upper()
    if document_type not in {"RCP", "FI"}:
        raise ValueError("document_type deve essere 'RCP' oppure 'FI'.")

    _validate_pdf(pdf_bytes)

    aic9 = normalize_aic9(aic)
    aic6 = aic9[:6]
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    size = len(pdf_bytes)

    _, _, bucket = _supabase_settings()
    storage_path = _storage_path(aic6, document_type, sha256)

    if isinstance(document_date, str) and document_date.strip():
        document_date = date.fromisoformat(document_date.strip())

    metadata_payload = dict(metadata or {})
    metadata_payload.setdefault("matched_aic9", aic9)

    conn = _connect()
    existing = None

    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM product_documents
                WHERE aic6 = %s
                  AND document_type = %s
                  AND sha256 = %s
                LIMIT 1
                """,
                (aic6, document_type, sha256),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    """
                    UPDATE product_documents
                    SET is_current = FALSE,
                        status = 'SUPERSEDED',
                        updated_at = NOW()
                    WHERE aic6 = %s
                      AND document_type = %s
                      AND document_id <> %s
                      AND is_current = TRUE
                    """,
                    (aic6, document_type, existing["document_id"]),
                )
                cur.execute(
                    """
                    UPDATE product_documents
                    SET source = %s,
                        source_url = %s,
                        document_date = %s,
                        retrieved_at = NOW(),
                        is_current = TRUE,
                        status = 'CURRENT',
                        metadata = %s,
                        updated_at = NOW()
                    WHERE document_id = %s
                    RETURNING *
                    """,
                    (
                        source,
                        source_url,
                        document_date,
                        Json(metadata_payload),
                        existing["document_id"],
                    ),
                )
                row = dict(cur.fetchone())
                conn.commit()
                return row

        _upload_pdf(storage_path, pdf_bytes)

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE product_documents
                SET is_current = FALSE,
                    status = 'SUPERSEDED',
                    updated_at = NOW()
                WHERE aic6 = %s
                  AND document_type = %s
                  AND is_current = TRUE
                """,
                (aic6, document_type),
            )
            cur.execute(
                """
                INSERT INTO product_documents (
                    aic6, document_type, source, source_url,
                    storage_bucket, storage_path, sha256,
                    mime_type, file_size, document_date,
                    retrieved_at, is_current, status, metadata,
                    created_at, updated_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    'application/pdf', %s, %s,
                    NOW(), TRUE, 'CURRENT', %s,
                    NOW(), NOW()
                )
                RETURNING *
                """,
                (
                    aic6,
                    document_type,
                    source,
                    source_url,
                    bucket,
                    storage_path,
                    sha256,
                    size,
                    document_date,
                    Json(metadata_payload),
                ),
            )
            row = dict(cur.fetchone())

        conn.commit()
        return row

    except Exception:
        conn.rollback()
        if not existing:
            _delete_storage_object(storage_path)
        raise
    finally:
        conn.close()


def get_current_document(
    aic: Any,
    document_type: str = "RCP",
) -> dict[str, Any] | None:
    aic6 = aic6_from_aic(aic)
    document_type = str(document_type).strip().upper()

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM product_documents
                WHERE aic6 = %s
                  AND document_type = %s
                  AND is_current = TRUE
                  AND status = 'CURRENT'
                ORDER BY retrieved_at DESC
                LIMIT 1
                """,
                (aic6, document_type),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def document_history(
    aic: Any,
    document_type: str = "RCP",
) -> list[dict[str, Any]]:
    aic6 = aic6_from_aic(aic)
    document_type = str(document_type).strip().upper()

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT *
                FROM product_documents
                WHERE aic6 = %s
                  AND document_type = %s
                ORDER BY retrieved_at DESC, document_id DESC
                """,
                (aic6, document_type),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()

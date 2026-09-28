from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


AIFA_API_BASE = "https://api.aifa.gov.it/aifa-bdf-eif-be/1.0.0"
SEARCH_ENDPOINT = f"{AIFA_API_BASE}/formadosaggio/ricerca"

DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "ListinoFarmaci-GKSD/1.0 "
    "(RCP retrieval from public AIFA Banca Dati Farmaci)"
)


class AifaDocumentError(RuntimeError):
    """Errore controllato nel recupero documenti AIFA."""


@dataclass(frozen=True)
class AifaResolvedDocument:
    requested_aic9: str
    aic6: str
    codice_sis: str
    document_type: str
    source_url: str
    search_query: str


@dataclass(frozen=True)
class AifaDownloadedDocument:
    resolved: AifaResolvedDocument
    pdf_bytes: bytes
    final_url: str
    sha256: str


def normalize_aic9(aic: Any) -> str:
    """
    Normalizza un AIC a 9 cifre conservando gli zeri iniziali.
    Gestisce anche valori letti da Excel come 12745055.0.
    """
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


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=2,
        status=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        }
    )
    return session


def _normalized_aic6(value: Any) -> str | None:
    if value is None:
        return None

    digits = re.sub(r"\D", "", str(value))

    if not digits or len(digits) > 6:
        return None

    return digits.zfill(6)


def _dict_get_ci(data: dict[str, Any], wanted: str) -> Any:
    wanted = wanted.lower()
    for key, value in data.items():
        if str(key).lower() == wanted:
            return value
    return None


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _extract_candidates(payload: Any) -> list[tuple[str, str]]:
    """
    Estrae coppie (CodiceSis, aic6) senza dipendere dalla forma esatta
    del JSON AIFA. Le API non sono documentate ufficialmente e la
    struttura può cambiare.
    """
    candidates: list[tuple[str, str]] = []

    for item in _walk_dicts(payload):
        codice_sis = _dict_get_ci(item, "CodiceSis")
        aic6 = _dict_get_ci(item, "aic6")

        if codice_sis is None or aic6 is None:
            continue

        sis_digits = re.sub(r"\D", "", str(codice_sis))
        normalized_aic6 = _normalized_aic6(aic6)

        if not sis_digits or normalized_aic6 is None:
            continue

        pair = (sis_digits, normalized_aic6)
        if pair not in candidates:
            candidates.append(pair)

    return candidates


def _search_terms(aic9: str) -> list[str]:
    """
    Prova prima l'AIC9 della confezione.
    Come fallback usa la specialità AIC6 anche nella forma zero-padded
    a 8 cifre descritta nella letteratura tecnica sul portale AIFA.
    """
    aic6 = aic9[:6]

    values = [
        aic9,
        aic6.zfill(8),
        aic6,
    ]

    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def resolve_aifa_document(
    aic: Any,
    document_type: str = "RCP",
    timeout: int = DEFAULT_TIMEOUT,
) -> AifaResolvedDocument:
    """
    Risolve:
        AIC9 -> CodiceSis + AIC6 -> endpoint documento AIFA.

    Non scarica ancora il PDF.
    """
    document_type = str(document_type).strip().upper()
    if document_type not in {"RCP", "FI"}:
        raise ValueError("document_type deve essere RCP oppure FI.")

    aic9 = normalize_aic9(aic)
    target_aic6 = aic9[:6]

    session = _session()
    diagnostics: list[str] = []

    for query_value in _search_terms(aic9):
        try:
            response = session.get(
                SEARCH_ENDPOINT,
                params={
                    "query": query_value,
                    "spellingCorrection": "true",
                    "page": 0,
                },
                headers={"Accept": "application/json"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            diagnostics.append(f"{query_value}: rete={type(exc).__name__}")
            continue

        if response.status_code != 200:
            diagnostics.append(
                f"{query_value}: HTTP {response.status_code}"
            )
            continue

        try:
            payload = response.json()
        except ValueError:
            diagnostics.append(f"{query_value}: risposta non JSON")
            continue

        candidates = _extract_candidates(payload)

        # Prima scegliamo solo un risultato coerente con l'AIC6 richiesto.
        matching = [
            (sis, candidate_aic6)
            for sis, candidate_aic6 in candidates
            if candidate_aic6 == target_aic6
        ]

        if not matching:
            diagnostics.append(
                f"{query_value}: nessun match AIC6={target_aic6}"
            )
            continue

        codice_sis, resolved_aic6 = matching[0]

        # L'endpoint AIFA usa in genere aic6 senza zeri iniziali,
        # ma preserviamo almeno "0" se il valore fosse tutto zero.
        aic6_for_url = resolved_aic6.lstrip("0") or "0"

        source_url = (
            f"{AIFA_API_BASE}/organizzazione/{codice_sis}"
            f"/farmaci/{aic6_for_url}/stampati?ts={document_type}"
        )

        return AifaResolvedDocument(
            requested_aic9=aic9,
            aic6=resolved_aic6,
            codice_sis=codice_sis,
            document_type=document_type,
            source_url=source_url,
            search_query=query_value,
        )

    detail = "; ".join(diagnostics[-6:]) or "nessun dettaglio disponibile"
    raise AifaDocumentError(
        f"Documento {document_type} non risolto per AIC {aic9}. "
        f"Dettagli: {detail}"
    )


def _looks_like_pdf(content: bytes) -> bool:
    return bool(content and content.lstrip().startswith(b"%PDF-"))


def _extract_url_from_payload(payload: Any) -> str | None:
    """
    Cerca ricorsivamente un URL HTTP/HTTPS in una risposta JSON.
    """
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith(("http://", "https://")):
            return text
        return None

    if isinstance(payload, dict):
        preferred_keys = (
            "url",
            "downloadUrl",
            "downloadURL",
            "signedUrl",
            "signedURL",
            "link",
            "href",
        )
        for key in preferred_keys:
            value = payload.get(key)
            found = _extract_url_from_payload(value)
            if found:
                return found

        for value in payload.values():
            found = _extract_url_from_payload(value)
            if found:
                return found

    if isinstance(payload, list):
        for value in payload:
            found = _extract_url_from_payload(value)
            if found:
                return found

    return None


def _extract_url_from_text(text: str) -> str | None:
    match = re.search(r'https?://[^\s"\'<>]+', text)
    return match.group(0) if match else None


def _download_pdf_response(
    session: requests.Session,
    url: str,
    timeout: int,
) -> tuple[bytes, str]:
    response = session.get(
        url,
        headers={
            "Accept": "application/pdf, application/json;q=0.9, */*;q=0.5"
        },
        timeout=timeout,
        allow_redirects=True,
    )

    if response.status_code != 200:
        raise AifaDocumentError(
            f"AIFA ha risposto HTTP {response.status_code} per il documento."
        )

    if _looks_like_pdf(response.content):
        return response.content, response.url

    content_type = response.headers.get("Content-Type", "").lower()

    # Alcune API restituiscono prima un JSON con l'URL del file.
    if "json" in content_type:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AifaDocumentError(
                "AIFA ha dichiarato una risposta JSON non valida."
            ) from exc

        nested_url = _extract_url_from_payload(payload)
        if nested_url:
            nested = session.get(
                nested_url,
                headers={"Accept": "application/pdf, */*;q=0.5"},
                timeout=timeout,
                allow_redirects=True,
            )
            if nested.status_code == 200 and _looks_like_pdf(nested.content):
                return nested.content, nested.url

    # Fallback difensivo: risposta testuale contenente un URL.
    text = response.text.strip()
    nested_url = _extract_url_from_text(text)
    if nested_url:
        nested = session.get(
            nested_url,
            headers={"Accept": "application/pdf, */*;q=0.5"},
            timeout=timeout,
            allow_redirects=True,
        )
        if nested.status_code == 200 and _looks_like_pdf(nested.content):
            return nested.content, nested.url

    raise AifaDocumentError(
        "La risposta AIFA non contiene un PDF riconoscibile."
    )


def download_aifa_document(
    aic: Any,
    document_type: str = "RCP",
    timeout: int = DEFAULT_TIMEOUT,
) -> AifaDownloadedDocument:
    """
    Risolve e scarica RCP/FI da AIFA.
    """
    resolved = resolve_aifa_document(
        aic=aic,
        document_type=document_type,
        timeout=timeout,
    )

    session = _session()
    pdf_bytes, final_url = _download_pdf_response(
        session=session,
        url=resolved.source_url,
        timeout=timeout,
    )

    return AifaDownloadedDocument(
        resolved=resolved,
        pdf_bytes=pdf_bytes,
        final_url=final_url,
        sha256=hashlib.sha256(pdf_bytes).hexdigest(),
    )


def fetch_and_store_aifa_document(
    aic: Any,
    document_type: str = "RCP",
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Flusso completo:
        AIC -> AIFA -> PDF -> Supabase Storage -> product_documents

    L'import di product_documents è lazy, così il test del solo endpoint
    AIFA può essere eseguito anche fuori da Streamlit.
    """
    downloaded = download_aifa_document(
        aic=aic,
        document_type=document_type,
        timeout=timeout,
    )

    from modules.product_documents import save_product_document

    result = save_product_document(
        aic=downloaded.resolved.requested_aic9,
        document_type=downloaded.resolved.document_type,
        pdf_bytes=downloaded.pdf_bytes,
        source_url=downloaded.resolved.source_url,
        source="AIFA",
        metadata={
            "codice_sis": downloaded.resolved.codice_sis,
            "aic6": downloaded.resolved.aic6,
            "search_query": downloaded.resolved.search_query,
            "final_download_url": downloaded.final_url,
            "aifa_pdf_sha256": downloaded.sha256,
        },
    )

    return result


def fetch_and_store_rcp(
    aic: Any,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    return fetch_and_store_aifa_document(
        aic=aic,
        document_type="RCP",
        timeout=timeout,
    )

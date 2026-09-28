from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permette di eseguire lo script dalla root del progetto.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.aifa_documents import (
    AifaDocumentError,
    download_aifa_document,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test AIFA: AIC -> recupero RCP PDF"
    )
    parser.add_argument(
        "aic",
        help="AIC della confezione, es. 012745055",
    )
    parser.add_argument(
        "--output",
        help="Percorso facoltativo dove salvare il PDF di test",
    )
    args = parser.parse_args()

    try:
        doc = download_aifa_document(args.aic, "RCP")
    except AifaDocumentError as exc:
        print(f"ERRORE AIFA: {exc}")
        return 2
    except Exception as exc:
        print(f"ERRORE: {type(exc).__name__}: {exc}")
        return 3

    print("OK - RCP recuperato")
    print(f"AIC9 richiesto : {doc.resolved.requested_aic9}")
    print(f"AIC6           : {doc.resolved.aic6}")
    print(f"Codice SIS     : {doc.resolved.codice_sis}")
    print(f"Query usata    : {doc.resolved.search_query}")
    print(f"Dimensione PDF : {len(doc.pdf_bytes):,} byte")
    print(f"SHA-256        : {doc.sha256}")
    print(f"Endpoint AIFA  : {doc.resolved.source_url}")
    print(f"URL finale     : {doc.final_url}")

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(doc.pdf_bytes)
        print(f"PDF salvato in : {path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

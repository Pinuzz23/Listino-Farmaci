from __future__ import annotations

from pathlib import Path

from modules.archive import archive_batch
from modules.catalog_db import (
    backup_database,
    preview_publication,
    publish_records,
)
from modules.db_backend import get_postgres_url, use_postgres


def preview(records, db_path):
    return preview_publication(records, db_path)


def _update_archive_path(db_path, batch_id: str, archive_path: str) -> None:
    if use_postgres():
        import psycopg2

        conn = psycopg2.connect(
            get_postgres_url(),
            connect_timeout=10,
            sslmode="require",
            application_name="listino-farmaci",
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE publications
                    SET archive_path = %s
                    WHERE batch_id = %s
                    """,
                    (archive_path, batch_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    import sqlite3

    with sqlite3.connect(Path(db_path)) as conn:
        conn.execute(
            """
            UPDATE publications
            SET archive_path = ?
            WHERE batch_id = ?
            """,
            (archive_path, batch_id),
        )
        conn.commit()


def publish(
    records,
    source_name,
    source_bytes,
    validated_bytes,
    report_bytes,
    db_path,
    archive_dir,
    backup_dir,
    app_version,
):
    # In SQLite crea il backup locale precedente alla pubblicazione.
    # In PostgreSQL backup_database() non crea file .db locali.
    backup_database(db_path, backup_dir)

    result = publish_records(
        records=records,
        source_name=source_name,
        db_path=db_path,
        app_version=app_version,
        archive_path=None,
    )

    # L'archivio documentale è ancora locale.
    # Su Streamlit Cloud non va considerato persistente:
    # verrà migrato successivamente a Supabase Storage.
    try:
        archive_path = archive_batch(
            archive_dir=archive_dir,
            batch_id=result["batch_id"],
            source_name=source_name,
            source_bytes=source_bytes,
            validated_bytes=validated_bytes,
            report_bytes=report_bytes,
        )

        _update_archive_path(
            db_path=db_path,
            batch_id=result["batch_id"],
            archive_path=archive_path,
        )

        result["archive_path"] = archive_path
        result["archive_error"] = None

    except Exception as exc:
        result["archive_path"] = None
        result["archive_error"] = str(exc)

    return result

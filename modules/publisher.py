from __future__ import annotations

from pathlib import Path

from modules.archive import archive_batch
from modules.catalog_db import backup_database, preview_publication, publish_records


def preview(records, db_path):
    return preview_publication(records, db_path)


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
    # Salva una copia dello stato precedente prima di ogni pubblicazione.
    backup_database(db_path, backup_dir)

    result = publish_records(
        records=records,
        source_name=source_name,
        db_path=db_path,
        app_version=app_version,
        archive_path=None,
    )

    # L'archivio documentale è distinto dal database: se la scrittura documentale
    # fallisce, la pubblicazione resta valida e l'interfaccia espone il warning.
    try:
        archive_path = archive_batch(
            archive_dir=archive_dir,
            batch_id=result["batch_id"],
            source_name=source_name,
            source_bytes=source_bytes,
            validated_bytes=validated_bytes,
            report_bytes=report_bytes,
        )

        import sqlite3
        with sqlite3.connect(Path(db_path)) as conn:
            conn.execute(
                "UPDATE publications SET archive_path=? WHERE batch_id=?",
                (archive_path, result["batch_id"]),
            )
            conn.commit()

        result["archive_path"] = archive_path
        result["archive_error"] = None
    except Exception as exc:
        result["archive_path"] = None
        result["archive_error"] = str(exc)

    return result

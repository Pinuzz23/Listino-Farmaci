from __future__ import annotations

from pathlib import Path


def archive_batch(
    archive_dir: str | Path,
    batch_id: str,
    source_name: str,
    source_bytes: bytes | None = None,
    validated_bytes: bytes | None = None,
    report_bytes: bytes | None = None,
) -> str:
    base = Path(archive_dir) / batch_id
    base.mkdir(parents=True, exist_ok=True)

    if source_bytes:
        (base / f"Originale_{Path(source_name).name}").write_bytes(source_bytes)
    if validated_bytes:
        (base / f"Tracciato_Validato_{Path(source_name).stem}.xlsx").write_bytes(validated_bytes)
    if report_bytes:
        (base / f"Report_Validazione_{Path(source_name).stem}.xlsx").write_bytes(report_bytes)

    return str(base)

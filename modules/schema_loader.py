import json
from pathlib import Path


def load_schema(path="config/schema.json"):
    schema_path = Path(path)
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)

    # Il master definitivo viene mantenuto disponibile anche su deploy puliti
    # (es. Streamlit Cloud), dove il file binario potrebbe non essere presente.
    try:
        from modules.master22_template import ensure_master_template
        ensure_master_template(schema, schema_path.resolve().parents[1])
    except Exception:
        # La lettura dello schema non deve fallire per un problema di generazione
        # del solo file template; la pagina Validazione gestirà l'assenza del file.
        pass
    return schema

import json
from pathlib import Path


def load_schema(path="config/schema.json"):
    schema_path = Path(path)
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)

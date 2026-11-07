"""Generate JSON Schema files for every contract: `uv run python -m scriptmaker.contracts.schema`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from . import AnswerSet, DocModel, ProgressFile, Script, StudyPlan

SCHEMA_DIR = Path(__file__).parent / "schemas"

CONTRACTS: dict[str, type[BaseModel]] = {
    "doc_model": DocModel,
    "script": Script,
    "answer_set": AnswerSet,
    "progress": ProgressFile,
    "study_plan": StudyPlan,
}


def render_schema(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_schemas(target: Path = SCHEMA_DIR) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in CONTRACTS.items():
        path = target / f"{name}.schema.json"
        path.write_text(render_schema(model), encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in write_schemas():
        print(f"wrote {p}", file=sys.stdout)

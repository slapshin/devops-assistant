"""Export JSON Schemas and the OpenAPI document for shared contracts.

Usage: uv run python -m scripts.export_schemas [--check]
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from app.domain.explanation import ExplanationInput
from app.domain.jobs import (
    AnalysisJob,
    AnalysisSubmission,
    AnalysisSubmitted,
    DiscoveredProjectList,
    EnvList,
    Problem,
    RuntimeConfig,
)
from app.domain.metrics import MetricCapability, MetricSeries
from app.domain.projects import ConnectionTest, ConnectionTestRequest, ProjectInput, ProjectList
from app.domain.report import AnalysisReport, AnalysisRequest
from app.main import create_app
from app.settings import load_settings

OUT = Path(__file__).resolve().parents[2] / "docs" / "contracts"

SCHEMAS: dict[str, type[BaseModel]] = {
    "AnalysisRequest": AnalysisRequest,
    "AnalysisSubmission": AnalysisSubmission,
    "AnalysisSubmitted": AnalysisSubmitted,
    "AnalysisJob": AnalysisJob,
    "AnalysisReport": AnalysisReport,
    "MetricSeries": MetricSeries,
    "MetricCapability": MetricCapability,
    "ExplanationInput": ExplanationInput,
    "Problem": Problem,
    "DiscoveredProjectList": DiscoveredProjectList,
    "ProjectList": ProjectList,
    "ProjectInput": ProjectInput,
    "ConnectionTestRequest": ConnectionTestRequest,
    "ConnectionTest": ConnectionTest,
    "EnvList": EnvList,
    "RuntimeConfig": RuntimeConfig,
}


def dump(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def outputs() -> dict[str, str]:
    files = {
        f"{name}.schema.json": dump(model.model_json_schema(mode="serialization"))
        for name, model in SCHEMAS.items()
    }
    app = create_app(load_settings(ai_provider="none", _env_file=None))
    files["openapi.json"] = dump(app.openapi())
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    stale = []
    for name, text in outputs().items():
        path = OUT / name
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    if stale:
        print(
            "Stale contract exports (run: uv run python -m scripts.export_schemas):",
            *stale,
            sep="\n  ",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

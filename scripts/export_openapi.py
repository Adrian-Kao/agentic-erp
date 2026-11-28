import json
from pathlib import Path

from erp_agent.api.main import app
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.messages import TaskMessage, TaskResult


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_json(
        Path("docs/schemas/exposure-report-v1.schema.json"),
        ExposureReport.model_json_schema(mode="serialization"),
    )
    write_json(Path("docs/schemas/mas-task-v1.schema.json"), TaskMessage.model_json_schema())
    write_json(
        Path("docs/schemas/mas-result-v1.schema.json"),
        TaskResult.model_json_schema(mode="serialization"),
    )
    write_json(Path("docs/schemas/openapi.json"), app.openapi())

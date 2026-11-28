from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.requests import InputModel


class AgentRequest(InputModel):
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    message: str = Field(min_length=1, max_length=4000)
    as_of_date: date | None = Field(default=None, ge=date(1900, 1, 1), le=date(9998, 12, 31))

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "session_id": "demo-session-001",
                    "message": "分析 TW01 未來六個月的 USD 曝險",
                    "as_of_date": "2026-11-27",
                }
            ]
        },
    }


class ModelAnswer(BaseModel):
    run_id: str | None = Field(max_length=36)
    summary: str = Field(max_length=1200)
    warning_codes: list[str] = Field(max_length=10)
    outcome: Literal["report", "information", "clarify", "out_of_scope"]


class ToolTrace(BaseModel):
    tool: str
    outcome: str
    duration_ms: int


class AgentResponse(BaseModel):
    session_id: str
    summary: str
    run_id: UUID | None
    exposure_report: ExposureReport | None
    active_skills: list[str]
    tool_trace_summary: list[ToolTrace]
    warnings: list[str]

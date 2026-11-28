from typing import Literal
from uuid import UUID

from pydantic import Field

from erp_agent.contracts.exposure import ExposureReport, Status
from erp_agent.contracts.requests import ExposureRequest, InputModel


class TaskMessage(InputModel):
    task_id: str = Field(min_length=1, max_length=128)
    source_agent: Literal["orchestrator"]
    target_agent: Literal["erp_agent"]
    action: Literal["analyze_fx_exposure"]
    parameters: ExposureRequest


class TaskResult(InputModel):
    task_id: str
    source_agent: Literal["erp_agent"] = "erp_agent"
    target_agent: Literal["orchestrator"] = "orchestrator"
    status: Status
    result_type: Literal["exposure_report_v1"] = "exposure_report_v1"
    run_id: UUID
    result: ExposureReport

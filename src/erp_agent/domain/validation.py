from erp_agent.contracts.exposure import ExposureReport
from erp_agent.contracts.requests import ExposureRequest
from erp_agent.database.repository import Snapshot
from erp_agent.domain.exposure_calculator import calculate


def validate_exposure(report: ExposureReport, snapshot: Snapshot, request: ExposureRequest) -> None:
    """Reject output changed since calculation, including amounts, count and provenance."""
    expected = calculate(snapshot, request, run_id=report.run_id, generated_at=report.generated_at)
    if expected != report:
        raise ValueError("Report does not reconcile to the source snapshot")

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from agents import Agent, Model, ModelSettings, OpenAIResponsesModel, RunConfig, Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError
from agents.mcp import MCPServerStreamableHttp
from openai import AsyncOpenAI
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from erp_agent.agent.skill_loader import SkillRegistry
from erp_agent.agent.tool_allowlist import WorkingState, build_tools
from erp_agent.application.services import Services
from erp_agent.contracts.agent import AgentRequest, AgentResponse, ModelAnswer
from erp_agent.contracts.errors import ServiceError
from erp_agent.contracts.exposure import ExposureReport
from erp_agent.mcp.client import connect_mcp
from erp_agent.memory.session_store import SessionStore
from erp_agent.observability.logging import event
from erp_agent.security.authorization import Principal

BASE_PROMPT = Path(__file__).with_name("instructions.md")


class AgentRuntime:
    def __init__(
        self,
        services: Services,
        skills: SkillRegistry,
        *,
        model: Model | None = None,
        connector: Callable[..., MCPServerStreamableHttp] = connect_mcp,
    ) -> None:
        self.services = services
        self.skills = skills
        self.sessions = SessionStore(services.settings.session_database)
        self.model = model
        self.connector = connector

    async def run(
        self, body: AgentRequest, principal: Principal, key: str, request_id: str
    ) -> AgentResponse:
        settings = self.services.settings
        secret = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if (
            not settings.enable_llm_agent
            or not settings.mcp_internal_token.get_secret_value()
            or (self.model is None and (not secret or not settings.openai_model))
        ):
            raise ServiceError(
                "AGENT_UNAVAILABLE",
                "LLM agent is disabled or unconfigured; use /v1/exposures/analyze",
                503,
            )
        skills = self.skills.select(body.message)
        instructions = [
            BASE_PROMPT.read_text(encoding="utf-8"),
            *(self.skills.load(name) for name in skills),
        ]
        if "erp-data-quality" in skills:
            instructions.append(
                self.skills.reference("erp-data-quality", "references/validation-checklist.md")
            )
        state = WorkingState(task_id=request_id)
        try:
            last_run = await run_in_threadpool(self.sessions.get, principal, body.session_id)
            if last_run:
                await run_in_threadpool(
                    self.services.exposures.get, last_run, principal, request_id
                )
        except ServiceError:
            last_run = None
        except sqlite3.Error as exc:
            raise ServiceError(
                "SESSION_UNAVAILABLE", "Session storage unavailable", 503, retryable=True
            ) from exc
        as_of_date = body.as_of_date.isoformat() if body.as_of_date else None
        instructions.append(
            "Authenticated context: "
            + json.dumps(
                {
                    "allowed_entity_ids": sorted(principal.entity_ids),
                    "default_as_of_date": as_of_date,
                    "last_run_id": str(last_run) if last_run else None,
                }
            )
        )
        try:
            async with asyncio.timeout(40):
                if self.model is not None:
                    answer = await self._ask(
                        self.model, instructions, body.message, key, principal, state, as_of_date
                    )
                else:
                    async with AsyncOpenAI(api_key=secret, timeout=12, max_retries=0) as provider:
                        model = OpenAIResponsesModel(
                            model=settings.openai_model, openai_client=provider
                        )
                        answer = await self._ask(
                            model, instructions, body.message, key, principal, state, as_of_date
                        )
        except TimeoutError as exc:
            raise ServiceError(
                "AGENT_TIMEOUT", "Agent or MCP tool timed out", 503, retryable=True
            ) from exc
        except (MaxTurnsExceeded, ModelBehaviorError, ValidationError) as exc:
            raise ServiceError(
                "AGENT_INVALID_RESULT", "Agent could not produce a valid result", 422
            ) from exc
        except Exception as exc:
            event("agent_dependency_error", error_type=type(exc).__name__)
            raise ServiceError(
                "AGENT_DEPENDENCY_UNAVAILABLE",
                "Agent model or MCP unavailable",
                503,
                retryable=True,
            ) from exc

        if not answer.run_id:
            if answer.outcome == "report" or state.reports:
                raise ServiceError(
                    "UNVERIFIED_RUN", "Agent did not confirm the tool-generated run", 422
                )
            codes = sorted({item.outcome for item in state.trace if item.outcome != "OK"})
            if any(code in {"MCP_UNAVAILABLE", "DATABASE_UNAVAILABLE"} for code in codes):
                raise ServiceError(
                    "AGENT_DEPENDENCY_UNAVAILABLE",
                    "Agent tool dependency unavailable",
                    503,
                    retryable=True,
                )
            summary = state.information or (
                "此操作不在 ERP Agent 的授權範圍內。"
                if codes or answer.outcome == "out_of_scope"
                else "請補充要分析的法人、截至日或期間。"
            )
            return AgentResponse(
                session_id=body.session_id,
                summary=summary,
                run_id=None,
                exposure_report=None,
                active_skills=list(skills),
                tool_trace_summary=state.trace,
                warnings=codes,
            )
        if answer.run_id not in state.reports:
            raise ServiceError(
                "UNVERIFIED_RUN", "Agent run was not returned by an authorized tool", 422
            )
        run_id = UUID(answer.run_id)
        report = await run_in_threadpool(self.services.exposures.get, run_id, principal, request_id)
        try:
            await run_in_threadpool(self.sessions.remember, principal, body.session_id, run_id)
        except sqlite3.Error as exc:
            raise ServiceError(
                "SESSION_UNAVAILABLE", "Session storage unavailable", 503, retryable=True
            ) from exc
        return AgentResponse(
            session_id=body.session_id,
            summary=summarize(report, state.reports[answer.run_id]),
            run_id=run_id,
            exposure_report=report,
            active_skills=list(skills),
            tool_trace_summary=state.trace,
            warnings=sorted({issue.code for issue in report.data_quality_issues}),
        )

    async def _ask(
        self,
        model: Model,
        instructions: list[str],
        message: str,
        key: str,
        principal: Principal,
        state: WorkingState,
        as_of_date: str | None,
    ) -> ModelAnswer:
        async with self.connector(self.services.settings, key, state.task_id) as mcp:
            agent = Agent(
                name="ERP Agent",
                instructions="\n\n".join(instructions),
                model=model,
                tools=build_tools(mcp, principal, state, as_of_date),
                output_type=ModelAnswer,
                model_settings=ModelSettings(
                    parallel_tool_calls=False, max_tokens=1000, store=False
                ),
            )
            result = await Runner.run(
                agent,
                message,
                max_turns=5,
                run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False),
            )
        return ModelAnswer.model_validate(result.final_output)


def summarize(report: ExposureReport, entities: list[str]) -> str:
    head = (
        f"截至 {report.as_of_date} 的 {report.horizon_months} 個曆月，"
        f"法人 {'、'.join(entities) or '依報表授權範圍'}，狀態 {report.status}。既有避險契約未納入。"
    )
    if report.provenance.scenario:
        head += (
            f"情境為 {report.provenance.scenario}，資料來源為 {report.provenance.source_system}。"
        )
    if report.status == "FAILED":
        return head + "資料品質驗證失敗，報表已停用金額輸出。"
    currencies = sorted(
        {row.currency for row in report.exposures}
        | {row.currency for row in report.overdue_summary}
    )
    scope = f"報表幣別為 {'、'.join(currencies) or '無未結外幣分組'}。"
    examples = "；".join(
        f"{row.entity_id} {row.period} {row.currency} {row.net_exposure:+.4f}"
        for row in report.exposures[:3]
    )
    return (
        head
        + scope
        + (f"前幾筆淨曝險為 {examples}。" if examples else "")
        + (
            f"逾期另列 {len(report.overdue_summary)} 組，資料品質問題 {len(report.data_quality_issues)} 項。"
        )
    )

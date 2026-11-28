from collections.abc import Callable
from datetime import timedelta
from typing import TypeVar

from pydantic import ValidationError

from erp_agent.contracts.errors import ServiceError
from erp_agent.kyriba.client import KyribaClient, KyribaReadGateway, WireRow
from erp_agent.kyriba.mock import MOCK_MAPPING, KyribaMockGateway
from erp_agent.kyriba.models import (
    AccountBalances,
    ActiveStatus,
    CashBalanceQuery,
    CashFlowQuery,
    CashFlowRow,
    CompanyRow,
    KyribaBatch,
    KyribaCashBalance,
    KyribaCashFlow,
    KyribaCompany,
    KyribaThirdParty,
    ReadMode,
    ThirdPartyRow,
)
from erp_agent.kyriba.settings import EntityMapping, KyribaSettings
from erp_agent.security.authorization import Principal

T = TypeVar("T")


def _out_of_scope() -> ServiceError:
    return ServiceError(
        "KYRIBA_SCOPE_MISMATCH", "Kyriba response could not be verified in scope", 502
    )


def _invalid_response() -> ServiceError:
    return ServiceError("KYRIBA_INVALID_RESPONSE", "Kyriba returned an unexpected response", 502)


class KyribaReadService:
    def __init__(
        self,
        gateway: KyribaReadGateway,
        entity_map: dict[str, dict[str, EntityMapping]],
        mode: ReadMode,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> None:
        self._gateway = gateway
        self._map = entity_map
        self._mode = mode
        self._page_size = page_size
        self._max_pages = max_pages

    def close(self) -> None:
        self._gateway.close()

    def companies(
        self, principal: Principal, entity_id: str, offset: int = 0
    ) -> KyribaBatch[KyribaCompany]:
        mapping = self._scope(principal, entity_id)

        def normalize(raw: WireRow) -> KyribaCompany:
            company = CompanyRow.model_validate(raw)
            if company.code != mapping.company_code:
                raise _out_of_scope()
            return KyribaCompany(
                code=company.code,
                source_id=company.uuid,
                name=company.description1,
                interface_code=company.interface_code,
            )

        rows, next_offset = self._paginate(
            lambda start, limit: self._gateway.companies(mapping.company_code, start, limit),
            normalize,
            lambda row: row.code,
            offset,
        )
        return KyribaBatch[KyribaCompany](
            mode=self._mode, entity_id=entity_id, rows=rows, next_offset=next_offset
        )

    def cash_flows(
        self, principal: Principal, entity_id: str, query: CashFlowQuery
    ) -> KyribaBatch[KyribaCashFlow]:
        self._scope(principal, entity_id, query.account_code)

        def normalize(raw: WireRow) -> KyribaCashFlow:
            flow = CashFlowRow.model_validate(raw)
            if flow.account.code != query.account_code:
                raise _out_of_scope()
            date_value = {
                "TRANSACTION": flow.dates.transaction_date,
                "VALUE": flow.dates.value_date,
                "ACCOUNTING": flow.dates.accounting_date,
            }.get(query.date_type)
            if date_value is not None and not query.start_date <= date_value <= query.end_date:
                raise _invalid_response()
            return KyribaCashFlow(
                source_id=flow.uuid,
                account_code=flow.account.code,
                flow_code=flow.flow_code.code,
                status=flow.status,
                transaction_date=flow.dates.transaction_date,
                value_date=flow.dates.value_date,
                accounting_date=flow.dates.accounting_date,
                amount=flow.flow_amount.amount,
                signed_amount=flow.flow_amount.signed_amount,
                currency=flow.flow_amount.currency.code,
            )

        rows, next_offset = self._paginate(
            lambda start, limit: self._gateway.cash_flows(query, start, limit),
            normalize,
            lambda row: row.source_id,
            query.offset,
        )
        return KyribaBatch[KyribaCashFlow](
            mode=self._mode, entity_id=entity_id, rows=rows, next_offset=next_offset
        )

    def cash_balances(
        self, principal: Principal, entity_id: str, query: CashBalanceQuery
    ) -> KyribaBatch[KyribaCashBalance]:
        self._scope(principal, entity_id, query.account_code)
        rows: list[KyribaCashBalance] = []
        start = query.start_date
        try:
            while True:
                remaining = (query.end_date - start).days
                end = start + timedelta(days=min(30, remaining))
                payload = AccountBalances.model_validate(
                    self._gateway.cash_balances(query, start, end)
                )
                if payload.account.code != query.account_code:
                    raise _out_of_scope()
                for balance in payload.balances:
                    calendar = balance.balance_date
                    if calendar.date_type != query.date_type or not start <= calendar.date <= end:
                        raise _invalid_response()
                    rows.append(
                        KyribaCashBalance(
                            account_code=payload.account.code,
                            balance_date=calendar.date,
                            date_type=calendar.date_type,
                            amount=balance.amount,
                            currency=balance.currency,
                        )
                    )
                if end == query.end_date:
                    break
                start = end + timedelta(days=1)
        except ValidationError as exc:
            raise _invalid_response() from exc
        rows.sort(key=lambda row: (row.balance_date, row.currency))
        return KyribaBatch[KyribaCashBalance](mode=self._mode, entity_id=entity_id, rows=rows)

    def third_parties(
        self,
        principal: Principal,
        entity_id: str,
        active_status: ActiveStatus | None = None,
        offset: int = 0,
    ) -> KyribaBatch[KyribaThirdParty]:
        mapping = self._scope(principal, entity_id)

        def normalize(raw: WireRow) -> KyribaThirdParty:
            party = ThirdPartyRow.model_validate(raw)
            if party.owner is None or party.owner.code != mapping.company_code:
                raise _out_of_scope()
            if active_status and party.active_status and active_status != party.active_status:
                raise _invalid_response()
            return KyribaThirdParty(
                code=party.code,
                source_id=party.uuid,
                name=party.name,
                company_code=party.owner.code,
                active_status=party.active_status,
            )

        rows, next_offset = self._paginate(
            lambda start, limit: self._gateway.third_parties(
                mapping.company_code, active_status, start, limit
            ),
            normalize,
            lambda row: row.code,
            offset,
        )
        return KyribaBatch[KyribaThirdParty](
            mode=self._mode, entity_id=entity_id, rows=rows, next_offset=next_offset
        )

    def _scope(
        self, principal: Principal, entity_id: str, account: str | None = None
    ) -> EntityMapping:
        principal.scope([entity_id])
        mapping = self._map.get(principal.tenant_id, {}).get(entity_id)
        if mapping is None or (account is not None and account not in mapping.account_codes):
            raise ServiceError(
                "KYRIBA_SCOPE_FORBIDDEN", "Entity or account is outside the Kyriba scope", 403
            )
        return mapping

    def _paginate(
        self,
        fetch: Callable[[int, int], list[WireRow]],
        normalize: Callable[[WireRow], T],
        identity: Callable[[T], str],
        offset: int,
    ) -> tuple[list[T], int | None]:
        rows: list[T] = []
        seen: set[str] = set()
        try:
            for _ in range(self._max_pages):
                page = fetch(offset, self._page_size)
                if len(page) > self._page_size:
                    raise _invalid_response()
                for raw in page:
                    row = normalize(raw)
                    key = identity(row)
                    if key in seen:
                        raise ServiceError(
                            "KYRIBA_PAGINATION_LOOP", "Kyriba repeated a paginated row", 502
                        )
                    seen.add(key)
                    rows.append(row)
                if len(page) < self._page_size:
                    return rows, None
                offset += self._page_size
                if offset > 2_147_483_647:
                    raise ServiceError(
                        "KYRIBA_OFFSET_LIMIT", "Kyriba page offset exceeds its bounds", 422
                    )
        except ValidationError as exc:
            raise _invalid_response() from exc
        return rows, offset


def create_kyriba_service(settings: KyribaSettings) -> KyribaReadService | None:
    if settings.mode == "disabled":
        return None
    gateway: KyribaReadGateway
    if settings.mode == "mock":
        gateway, mappings = KyribaMockGateway(), MOCK_MAPPING
    else:
        gateway, mappings = KyribaClient(settings), settings.entity_map
    return KyribaReadService(
        gateway, mappings, settings.mode, settings.page_size, settings.max_pages
    )

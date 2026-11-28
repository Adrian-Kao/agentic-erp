from datetime import date
from decimal import Decimal

from erp_agent.kyriba.client import WireRow
from erp_agent.kyriba.models import ActiveStatus, CashBalanceQuery, CashFlowQuery
from erp_agent.kyriba.settings import EntityMapping

MOCK_MAPPING = {
    "demo-a": {
        "TW01": EntityMapping(company_code="DEMO_A_TW01", account_codes=frozenset({"A_TW01_USD"})),
        "TW02": EntityMapping(company_code="DEMO_A_TW02", account_codes=frozenset({"A_TW02_EUR"})),
    },
    "demo-b": {
        "TW01": EntityMapping(company_code="DEMO_B_TW01", account_codes=frozenset({"B_TW01_USD"})),
    },
}
_COMPANIES = {e.company_code for tenant in MOCK_MAPPING.values() for e in tenant.values()}
_ACCOUNTS = {
    code for tenant in MOCK_MAPPING.values() for e in tenant.values() for code in e.account_codes
}


class KyribaMockGateway:
    """Static synthetic records with the same wire shapes as the live GET endpoints."""

    def close(self) -> None:
        # The fixture gateway owns no network or file handles.
        return None

    def companies(self, company: str, offset: int, limit: int) -> list[WireRow]:
        rows = [{"code": company, "description1": f"Synthetic {company}", "interfaceCode": company}]
        return rows[offset : offset + limit] if company in _COMPANIES else []

    def cash_flows(self, query: CashFlowQuery, offset: int, limit: int) -> list[WireRow]:
        if query.account_code not in _ACCOUNTS:
            return []
        currency = query.account_code[-3:]
        rows = []
        for index, (status, signed) in enumerate(
            (("ACTUAL", Decimal("10580.123456")), ("CONFIRMED", Decimal("-1500.25"))), start=10
        ):
            transaction = date(2026, 11, index)
            value = date(2026, 11, index + 1)
            reference = value if query.date_type == "VALUE" else transaction
            if query.start_date <= reference <= query.end_date:
                rows.append(
                    {
                        "uuid": f"mock-{query.account_code}-{index}",
                        "account": {"code": query.account_code},
                        "flowCode": {"code": "+WIRE" if signed > 0 else "-WIRE"},
                        "status": status,
                        "date": {
                            "transactionDate": str(transaction),
                            "valueDate": str(value),
                            "accountingDate": str(transaction),
                        },
                        "flowAmount": {
                            "amount": abs(signed),
                            "signedAmount": signed,
                            "currency": {"code": currency},
                        },
                    }
                )
        return rows[offset : offset + limit]

    def cash_balances(self, query: CashBalanceQuery, start: date, end: date) -> WireRow:
        currency = query.account_code[-3:]
        rows = []
        if query.account_code in _ACCOUNTS:
            for day, amount, included in (
                (date(2026, 11, 10), Decimal("20000.00"), query.actual),
                (date(2026, 12, 1), Decimal("31000.125"), query.confirmed_forecasts),
            ):
                if included and start <= day <= end:
                    rows.append(
                        {
                            "amount": amount,
                            "balanceDate": {"date": str(day), "dateType": query.date_type},
                            "currencyCode": currency,
                        }
                    )
        return {"account": {"code": query.account_code}, "cashBalance": rows}

    def third_parties(
        self, company: str, active_status: ActiveStatus | None, offset: int, limit: int
    ) -> list[WireRow]:
        rows = [
            {
                "code": f"{company}_{n}",
                "name": f"Synthetic counterparty {n}",
                "companyOwnership": {"code": company},
                "activeStatus": status,
            }
            for n, status in enumerate(("OPENED", "OPENED", "CLOSED"), start=1)
            if company in _COMPANIES and (active_status is None or status == active_status)
        ]
        return rows[offset : offset + limit]

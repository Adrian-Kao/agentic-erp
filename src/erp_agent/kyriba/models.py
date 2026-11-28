from datetime import date
from decimal import Decimal
from typing import Annotated, Generic, Literal, Self, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from erp_agent.contracts.requests import EntityCode, InputModel

# Code allowlist for this connector. These values are interpolated only into fixed RSQL clauses.
ProviderCode = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
ProviderId = Annotated[str, Field(min_length=1, max_length=128)]
ProviderName = Annotated[str, Field(max_length=200)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
BalanceDateType = Literal["TRANSACTION", "VALUE"]
FlowDateType = Literal["TRANSACTION", "VALUE", "ACCOUNTING", "UPDATE", "CREATION"]
ActiveStatus = Literal["OPENED", "CLOSED"]
ReadMode = Literal["mock", "live"]
Offset = Annotated[int, Field(ge=0, le=2_147_483_647, strict=True)]


def reject_float(value: object) -> object:
    if isinstance(value, (float, bool)):
        raise ValueError("Kyriba money must be an exact decimal")
    return value


Money = Annotated[Decimal, BeforeValidator(reject_float), Field(allow_inf_nan=False)]


class DateWindow(InputModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def check_dates(self) -> Self:
        if not 0 <= (self.end_date - self.start_date).days < self.max_days():
            raise ValueError(f"Date window must include 1 to {self.max_days()} days")
        return self

    def max_days(self) -> int:
        return 366

    def parameters(self) -> dict[str, str]:
        return {"startDate": self.start_date.isoformat(), "endDate": self.end_date.isoformat()}


class CashFlowQuery(DateWindow):
    account_code: ProviderCode
    date_type: FlowDateType = "TRANSACTION"
    offset: Offset = 0

    def max_days(self) -> int:
        return 31


class CashBalanceQuery(DateWindow):
    account_code: ProviderCode
    date_type: BalanceDateType = "TRANSACTION"
    actual: bool = True
    intraday: bool = False
    confirmed_forecasts: bool = False
    estimated_forecasts: bool = False

    @model_validator(mode="after")
    def check_balance_types(self) -> Self:
        if not any(
            (self.actual, self.intraday, self.confirmed_forecasts, self.estimated_forecasts)
        ):
            raise ValueError("Select at least one balance type")
        return self

    def parameters(self) -> dict[str, str]:
        return {
            **super().parameters(),
            "dateType": self.date_type,
            "actual": str(self.actual).lower(),
            "intraday": str(self.intraday).lower(),
            "confirmedForecasts": str(self.confirmed_forecasts).lower(),
            "estimatedForecasts": str(self.estimated_forecasts).lower(),
        }


class KyribaCompany(BaseModel):
    code: ProviderCode
    source_id: ProviderId | None = None
    name: ProviderName | None = None
    interface_code: ProviderId | None = None


class KyribaCashFlow(BaseModel):
    source_id: ProviderId
    account_code: ProviderCode
    flow_code: ProviderId
    status: ProviderId | None = None
    transaction_date: date | None = None
    value_date: date | None = None
    accounting_date: date | None = None
    amount: Money
    signed_amount: Money | None = None
    currency: CurrencyCode


class KyribaCashBalance(BaseModel):
    account_code: ProviderCode
    balance_date: date
    date_type: BalanceDateType
    amount: Money
    currency: CurrencyCode


class KyribaThirdParty(BaseModel):
    code: ProviderId
    source_id: ProviderId | None = None
    name: ProviderName | None = None
    company_code: ProviderCode
    active_status: ActiveStatus | None = None


T = TypeVar("T")


class KyribaBatch(BaseModel, Generic[T]):
    source_system: Literal["KYRIBA"] = "KYRIBA"
    mode: ReadMode
    entity_id: EntityCode
    rows: list[T]
    next_offset: Offset | None = None


class WireModel(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)


class CodeReference(WireModel):
    code: ProviderCode


class CompanyRow(WireModel):
    code: ProviderCode
    uuid: ProviderId | None = None
    description1: ProviderName | None = None
    interface_code: ProviderId | None = Field(default=None, validation_alias="interfaceCode")


class ThirdPartyRow(WireModel):
    code: ProviderId
    uuid: ProviderId | None = None
    name: ProviderName | None = None
    owner: CodeReference | None = Field(default=None, validation_alias="companyOwnership")
    active_status: ActiveStatus | None = Field(default=None, validation_alias="activeStatus")


class FlowDates(WireModel):
    transaction_date: date | None = Field(default=None, validation_alias="transactionDate")
    value_date: date | None = Field(default=None, validation_alias="valueDate")
    accounting_date: date | None = Field(default=None, validation_alias="accountingDate")


class FlowAmount(WireModel):
    amount: Money
    signed_amount: Money | None = Field(default=None, validation_alias="signedAmount")
    currency: CodeReference


class FlowReference(WireModel):
    code: ProviderId


class CashFlowRow(WireModel):
    uuid: ProviderId
    account: CodeReference
    flow_code: FlowReference = Field(validation_alias="flowCode")
    status: ProviderId | None = None
    dates: FlowDates = Field(validation_alias="date")
    flow_amount: FlowAmount = Field(validation_alias="flowAmount")


class BalanceDate(WireModel):
    date: date
    date_type: BalanceDateType = Field(validation_alias="dateType")


class BalanceRow(WireModel):
    amount: Money
    balance_date: BalanceDate = Field(validation_alias="balanceDate")
    currency: CurrencyCode = Field(validation_alias="currencyCode")


class AccountBalances(WireModel):
    account: CodeReference
    balances: list[BalanceRow] = Field(validation_alias="cashBalance")

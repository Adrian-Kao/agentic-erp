# Kyriba 唯讀整合

Kyriba 是 ERP Agent 可透過 MCP 讀取的補充資料源。回傳的資料包含來源、執行模式和 ERP 法人代碼。現有 `calc_net_exposure` 仍使用 ERP AR/AP 快照，現金部位與資金流保留為獨立資料。

## 官方文件已確認的操作

| MCP Tool | Kyriba HTTP 操作 | 主要參數 |
| --- | --- | --- |
| `kyriba_list_companies` | `GET /v1/companies` | `entity_id`，可選 `offset` |
| `kyriba_list_cash_flows` | `GET /v1/cash-flows` | `entity_id`，以及 `query` 的 `account_code`、`start_date`、`end_date`、`date_type`、可選 `offset` |
| `kyriba_get_cash_balances` | `GET /v1/cash-balances/accounts/{ref}/balances` | `entity_id`，以及 `query` 的帳戶和日期、`date_type` 和可選餘額類型 |
| `kyriba_list_third_parties` | `GET /v1/third-parties` | `entity_id`，可選 `active_status` 和 `offset` |

這四個讀取端點均已從 Kyriba 官方 Domain Skills 和 OpenAPI 文件確認。模組的唯一 POST 用於 OAuth token 交換。寫入、付款、核准、交易、資料匯入及批次聚合 API 均未接線。

文件索引與跨資源規則為 [llms.txt](https://developer.kyriba.com/llms.txt)、[Meta Skill](https://developer.kyriba.com/static/llm/skills/kyriba/SKILL.md)、[Integration Guide](https://developer.kyriba.com/static/llm/skills/kyriba-guide/SKILL.md)。

各資源規格見 [Companies Skill](https://developer.kyriba.com/static/llm/skills/companies/SKILL.md)／[OpenAPI](https://developer.kyriba.com/static/apis/companies/companies.yaml)、[Cash Flows Skill](https://developer.kyriba.com/static/llm/skills/cash-flows/SKILL.md)／[OpenAPI](https://developer.kyriba.com/static/apis/cash-flows/cash-flows.yaml)、[Cash Balances Skill](https://developer.kyriba.com/static/llm/skills/cash-balances/SKILL.md)／[OpenAPI](https://developer.kyriba.com/static/apis/cash-balances/cash-balances.yaml)、[Third Parties Skill](https://developer.kyriba.com/static/llm/skills/third-parties/SKILL.md)／[OpenAPI](https://developer.kyriba.com/static/apis/thirdparties/thirdparties.yaml)。

## Mock Mode

依 README 設定 ERP Demo API key 和內部 MCP token，並在 `.env` 設定 `KYRIBA_MODE=mock`。Kyriba 的 URL 與憑證可留空。重啟本機 MCP Server，或以 `docker compose up -d --build` 重新部署。Compose 只將 Kyriba 設定傳入 MCP 容器。

| ERP 租戶 | ERP 法人 | Kyriba 模擬公司 | Kyriba 模擬帳戶 |
| --- | --- | --- | --- |
| demo-a | TW01 | DEMO_A_TW01 | A_TW01_USD |
| demo-a | TW02 | DEMO_A_TW02 | A_TW02_EUR |
| demo-b | TW01 | DEMO_B_TW01 | B_TW01_USD |

本機啟動 `uv run uvicorn erp_agent.mcp.server:app --host 127.0.0.1 --port 8001` 後，可在 Python 中執行以下範例。將 ERP MCP caller 的開發用 API key 設在終端環境變數 `ERP_MCP_DEMO_API_KEY`。

```python
import asyncio
import os
from uuid import uuid4

from erp_agent.config import Settings
from erp_agent.mcp.client import connect_mcp


async def main():
    query = {
        "account_code": "A_TW01_USD",
        "start_date": "2026-11-01",
        "end_date": "2026-11-30",
    }
    async with connect_mcp(Settings(), os.environ["ERP_MCP_DEMO_API_KEY"], str(uuid4())) as mcp:
        for tool, params in (
            ("kyriba_list_companies", {"entity_id": "TW01"}),
            ("kyriba_list_cash_flows", {"entity_id": "TW01", "query": query}),
            ("kyriba_get_cash_balances", {"entity_id": "TW01", "query": query}),
            ("kyriba_list_third_parties", {"entity_id": "TW01", "active_status": "OPENED"}),
        ):
            reply = await mcp.call_tool(tool, params)
            print(tool, reply.structured_content)


asyncio.run(main())
```

Mock 查詢應取得兩筆現金流和一筆已實現餘額。每個 Tool 都使用現有的 `result`／`error` envelope。成功結果含 `source_system=KYRIBA`、`mode=mock` 或 `live`、`entity_id`、`rows` 及可選 `next_offset`。財務金額序列化為十進位字串。上游有提供 `flowAmount.signedAmount` 時會保留，否則為 null。餘額保留原幣，預設查詢 actual 資料，可用 `intraday`、`confirmed_forecasts`、`estimated_forecasts` 擴充。

在 Compose 中可用 `docker compose exec api python scripts/smoke_test.py --kyriba-mock --postgres-roles` 端到端驗證四個 Mock 工具。

## Live Mode

設定 `KYRIBA_MODE=live`。從 Kyriba 管理員取得同環境的下列設定，存入環境或秘密管理工具。

- `KYRIBA_TOKEN_URL` 是含 `/oauth/token` 的完整 HTTPS URL。
- `KYRIBA_BASE_URL` 是 Kyriba API 根 URL（包含環境提供的 API prefix，後面由 client 接上 `/v1/...`）。
- `KYRIBA_CLIENT_ID` 與 `KYRIBA_CLIENT_SECRET` 用於 HTTP Basic Auth。Token 請求為表單格式的 `grant_type=client_credentials`。
- `KYRIBA_ENTITY_MAP` 是 JSON 對映，例如 `{"erp-tenant":{"TW01":{"company_code":"KCOMP01","account_codes":["KACC01"]}}}`。請填入已由管理員核對的真實公司與帳戶代碼。

Kyriba 端配置 Companies、Cash Flows、Cash Balances、Third Parties 的唯讀資料權限，對應文件中的 `company-scope`、`cash-flows-scope`、`cash-balances-scope`、`third-party-scope`。OAuth 請求省略 `scope`。限制 OAuth client 可存取的公司、帳戶與環境。MCP 再用已認證的 ERP 租戶和法人核對對映，公司和帳戶代碼在對映中必須唯一。連線只使用設定的 HTTPS endpoint，且停用對外重新導向。

第三方查詢限明確具有 `companyOwnership.code` 的記錄。若上游回傳缺少歸屬、不同公司或不同帳戶的資料，整批拒絕並回 `KYRIBA_SCOPE_MISMATCH`。這項行為已以模擬 HTTP 驗證，仍需在實際租戶確認回應欄位與權限配置。

## 分頁、限流與錯誤

- 清單使用 `page.limit` 與 `page.offset`，短頁即停止。模組忽略上游 total 與 next URL。達到本地頁數上限時，用 `next_offset` 和原查詢條件繼續。
- Cash Flows 支援官方五種日期類型。每次工具呼叫限 31 個曆日。Cash Balances 支援 `TRANSACTION`、`VALUE`，一次最多查 366 天，拆成每個不超過 31 天的 Kyriba 請求。
- Token 使用回應明示的 `token_type`。第一次資源 401 會嘗試官方備援 scheme，需要時重新取 token 並重試一次。終止時回 `KYRIBA_UNAUTHORIZED`。403 回 `KYRIBA_FORBIDDEN`，不重試。
- 429 使用 `Kyriba-Customer-Rate-Limit-Reset` 的 epoch 毫秒。當等待超過設定上限、缺少有效值或仍收到 429，回傳可重試的 `KYRIBA_RATE_LIMITED`。網路逾時回 `KYRIBA_TIMEOUT`。
- 每個程序的 client 維持一次一個上游請求，採保守的每 30 秒 25 個請求。共用 OAuth client 的多個程序須由部署端統一限流。
- 可調環境值有 `KYRIBA_TIMEOUT_SECONDS`、`KYRIBA_RETRY_WAIT_SECONDS`、`KYRIBA_PAGE_SIZE`、`KYRIBA_MAX_PAGES`。預設分別為 5 秒、5 秒、100 筆與 10 頁。
- 多頁或多個日期區間可能超過預設 MCP 等待時間。程式呼叫者可在 `connect_mcp(..., timeout_seconds=60)` 設定較長的 MCP 等待時間。

執行 `uv run pytest -q tests/unit/test_kyriba_client.py tests/integration/test_kyriba_mcp.py` 驗證。這些測試不需要 Kyriba 憑證。

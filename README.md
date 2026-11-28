# ERP Agent

以 Microsoft FinanceBenchmark 公開合成資料建立可追溯的 Aster Electronics 模擬帳務，使用 MySQL 8.4 與 Decimal 產生外幣現金流曝險報表。Orchestrator 與 Strategy Agent 可透過 REST 合約整合。市場資料、策略、風控和交易由其他 Agent 負責。來源日記帳、模擬 AR/AP、獨立 golden fixture 各有明確資料層。

## Docker Compose

在專案根目錄執行以下 PowerShell 指令，建立本機設定。

```powershell
Copy-Item .env.example .env
uv sync --locked
uv run python scripts/fetch_microsoft_dataset.py
uv run python scripts/profile_microsoft_dataset.py
```

在 `.env` 設定各自的 `MYSQL_ROOT_PASSWORD`、`ERP_IMPORTER_PASSWORD`、`ERP_APP_PASSWORD`、`ERP_READ_PASSWORD` 和 `MCP_INTERNAL_TOKEN`。Compose 將資料庫密碼放入 URI，請使用英數字、連字號或底線。加入兩個不同的開發用 API key。以下 A key 具有查看資料集清單和列級血緣所需的開發管理權限。

```dotenv
DEMO_API_KEYS_JSON='{"YOUR_A_KEY":{"tenant_id":"DEMO_A","actor_id":"analyst-a","entity_ids":["TW01","TW02"],"dataset_admin":true},"YOUR_B_KEY":{"tenant_id":"DEMO_B","actor_id":"analyst-b","entity_ids":["TW01"]}}'
```

```powershell
docker compose up -d --build
docker compose ps -a
docker compose exec api python -m scripts.smoke_financebenchmark
docker compose run --rm --no-deps init python -m scripts.verify_microsoft_import
```

`init` 容器套用 Alembic、配置 MySQL 帳號、以 importer 匯入唯讀掛載的官方 ZIP 並建立情境，成功後退出。API 綁定 `127.0.0.1:8000`，MCP 於容器網路提供 `http://mcp:8001/mcp`。MySQL 埠保留於容器網路。可用 `ERP_API_PORT` 避免既有服務衝突。Swagger 與契約位於 [本機 /docs](http://127.0.0.1:8000/docs) 與 `/openapi.json`。

若無法連線到 GitHub，將 [固定 commit 的官方 ZIP](https://github.com/microsoft/FinanceBenchmark/blob/6ced62b961d4c18b2ba53f268b443eb852fb73ca/data/fno_benchmark_data_raw/FO%20Benchmark_Data.zip) 原檔放入 `data/source/FO Benchmark_Data.zip`，並保留 [MIT 授權](https://github.com/microsoft/FinanceBenchmark/blob/6ced62b961d4c18b2ba53f268b443eb852fb73ca/LICENSE) 通知。缺少原檔時匯入回報 `SOURCE_DATA_UNAVAILABLE`。件數與欄位參見 [來源盤點](docs/source-profile-report.md)、[匯入摘要](docs/import-summary.md) 及 [展示假設](docs/demo-assumptions.md)。

## 本機啟動

需要 Python 3.11+、uv，以及可達且獨立的 MySQL 8.4 開發資料庫，請先建立空的 `erp_demo`。在 `.env` 按該環境設定 `ERP_ADMIN_DATABASE_URL`、`ERP_IMPORT_DATABASE_URL`、`ERP_READ_DATABASE_URL` 和 `DATABASE_URL`，使用四個不同帳號。

```powershell
uv sync --locked
uv run python scripts/fetch_microsoft_dataset.py
uv run python scripts/profile_microsoft_dataset.py
uv run alembic upgrade head
uv run python scripts/provision_mysql_roles.py
uv run python scripts/import_microsoft_dataset.py
uv run python -m scripts.build_demo_scenario
uv run python -m scripts.verify_microsoft_import
uv run uvicorn erp_agent.api.main:app --host 127.0.0.1 --port 8000
```

MCP 請在第二個終端機啟動。SQLite 保留給無 MySQL 的隔離測試。離線 golden 可用初始化 CLI 的 `--fixture-only` 明確建立。

```powershell
uv run uvicorn erp_agent.mcp.server:app --host 127.0.0.1 --port 8001
uv run python -m scripts.smoke_financebenchmark
```

## REST 與 MCP 呼叫

以下假設已用上述設定填入 API key。

```powershell
$headers = @{ 'X-API-Key' = 'YOUR_A_KEY'; 'Idempotency-Key' = 'demo-tw01-20261127-001' }
$body = @{
  entity_ids = @('TW01')
  as_of_date = '2026-11-27'
  horizon_months = 6
  currencies = @('USD')
  include_overdue_summary = $true
  scenario = 'baseline'
} | ConvertTo-Json
$report = Invoke-RestMethod http://127.0.0.1:8000/v1/exposures/analyze -Method Post `
  -Headers $headers -ContentType 'application/json' -Body $body
$report.exposures
Invoke-RestMethod "http://127.0.0.1:8000/v1/exposures/$($report.run_id)" -Headers $headers
```

在 MySQL 省略情境時使用 baseline。將情境改為 `fixture_only` 可驗證 USD 2027-01 淨流入 `400000.0000`。若重用 idempotency key，請維持相同請求。用 `GET /v1/scenarios` 看可用情境，用 `GET /v1/datasets/manifest` 與 `GET /v1/exposures/{run_id}/lineage` 查來源。MCP 以 Streamable HTTP、服務 token 和已授權 API key 呼叫 `calc_net_exposure`。詳見 [MCP tools](docs/mcp-tools.md) 與 [API contract](docs/api-contract.md)。

## 選用 Kyriba 資料源

設定 `KYRIBA_MODE=mock` 即可透過 MCP 測試公司、現金流、現金餘額與第三方，Kyriba 憑證可留空。Live 設定、法人／帳戶對映與呼叫範例見 [Kyriba 唯讀整合](docs/kyriba-integration.md)。

## 選用自然語言 Agent

Deterministic API 與 MCP 可在空白 `OPENAI_API_KEY` 下運作。要啟用 `POST /v1/agent/run`，設定 `ENABLE_LLM_AGENT=true`、`OPENAI_API_KEY`、`OPENAI_MODEL`，確認 MCP URL 可連線後重啟 API。請求包含 `session_id`、`message`，可帶 `as_of_date`。未啟用時回 `503 AGENT_UNAVAILABLE`。模型規劃工具呼叫，服務從資料庫取得正式報表並組成金額摘要。工具只提供給模型最少必要的驗證摘要，已停用 Agents SDK trace export。啟用時使用者送出的自然語言與必要工具摘要會傳給所配置的模型供應商。

鎖定依賴包含 MCP Python SDK 2.3.0 與 OpenAI Agents SDK 0.26.0，採用 `MCPServer` 和 `MCPServerStreamableHttp`。詳見 [官方 MCP ASGI 指南](https://py.sdk.modelcontextprotocol.io/run/asgi/) 與 [Agents SDK MCP 文件](https://openai.github.io/openai-agents-python/mcp/)。

## 開發與稽核

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -q
uv run python scripts/export_openapi.py
docker compose logs api mcp
```

可用 `GET /v1/exposures/{run_id}` 取得授權報表。DB 的 `exposure_runs`、`exposure_items`、`audit_events` 可由獲授權的 DB 操作者查閱。應用日誌可用 `X-Request-ID` 串接。詳見 [操作手冊](docs/operating-runbook.md)、[架構](docs/architecture.md)、[ERD](docs/erd.md) 與 [演示流程](docs/demo-walkthrough.md)。

真實 ERP 接入時，實作 `ERPAdapter` 的 `list_entities` 和 `snapshot`，映射內部法人、帳款日期／狀態、原始 Decimal 金額與付款事件。確保可重建截至日的文件狀態、付款有效日、租戶及幣別一致性和來源版本，使用唯讀帳號與固定查詢。正式上線以 OIDC/JWT、服務身份與短效授權權杖替換 demo keys，加入 TLS、來源限制、秘密管理、租戶資料存取治理與稽核保留策略。

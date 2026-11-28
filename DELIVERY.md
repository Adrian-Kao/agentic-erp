# ERP Agent 交付說明

以 Microsoft FinanceBenchmark 的公開合成資料建置 Aster Electronics 模擬帳務。FastAPI 與 MCP 皆可查詢法人、計算外幣 AR／AP 曝險及重查已保存的報表。資料匯入、報表與稽核使用 MySQL 8.4，自然語言 Agent 的最近報表參照存於 SQLite。本文的相對路徑以專案根目錄為準。

## 1. 資料集下載與核對

資料固定在 Git commit `6ced62b961d4c18b2ba53f268b443eb852fb73ca`。來源是 Microsoft 為 Dynamics 365 Finance 準備的合成資料，含客戶與供應商主檔，以及 AP、AR 日記帳匯入範本。 [Microsoft ERP QA 說明](https://github.com/microsoft/FinanceBenchmark/blob/6ced62b961d4c18b2ba53f268b443eb852fb73ca/docs/erp_qa_setup.md) · [MIT 授權](https://github.com/microsoft/FinanceBenchmark/blob/6ced62b961d4c18b2ba53f268b443eb852fb73ca/LICENSE)

- 資料壓縮檔 `FO Benchmark_Data.zip` 放在 `data/source/FO Benchmark_Data.zip`。Docker 的 `init` 會唯讀掛載該目錄並直接解析 ZIP。 [下載官方 ZIP](https://raw.githubusercontent.com/microsoft/FinanceBenchmark/6ced62b961d4c18b2ba53f268b443eb852fb73ca/data/fno_benchmark_data_raw/FO%20Benchmark_Data.zip)
- 授權檔放在 `data/source/FinanceBenchmark-LICENSE.txt`。 [下載官方授權原檔](https://raw.githubusercontent.com/microsoft/FinanceBenchmark/6ced62b961d4c18b2ba53f268b443eb852fb73ca/LICENSE)
- ZIP SHA-256 應為 `5da754beab30462f4df48b93d69345e6d7d09b575ae4304d4bc5569a6572b239`。

可用下列指令自動下載並盤點。若 ZIP 已存在，下載程式會驗證現有檔案。

~~~powershell
Set-Location C:\GitHub\agentic-erp
uv sync --locked
uv run python scripts/fetch_microsoft_dataset.py
uv run python scripts/profile_microsoft_dataset.py
Get-FileHash -Algorithm SHA256 -LiteralPath 'data/source/FO Benchmark_Data.zip'
~~~

| 來源檔案 | 原始列 | 標準化 | 隔離 |
| --- | ---: | ---: | ---: |
| `D365_Customers_DMF_Final.csv` | 1,000 | 1,000 | 0 |
| `D365_Vendors_DMF_Final.csv` | 1,000 | 1,000 | 0 |
| `AP_Journal_Import_Updated.xlsx` | 3,428 | 3,357 | 71 |
| `AR_Journal_Import_Updated.xlsx` | 3,459 | 2,892 | 567 |
| **合計** | **8,887** | **8,249** | **638** |

匯入批次為 `0296d020-8b2b-5707-8a6c-be063924d91b`。隔離的 638 筆金額超出 `DECIMAL(20,4)` 可精確儲存的範圍，原值與來源列位置均保存在 Raw 層。欄位與盤點結果見 `docs/source-profile-report.md`，件數與資料血緣見 `docs/import-summary.md`。

## 2. 啟動環境與設定

使用 Windows PowerShell，先啟動 Docker Desktop。首次使用下載工具或本機 Python 測試需安裝 uv。若已手動放好經核對的來源 ZIP 與授權檔，可直接使用 Docker 啟動。

~~~powershell
Set-Location C:\GitHub\agentic-erp
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
~~~

在 `.env` 填入 `MYSQL_ROOT_PASSWORD`、`ERP_IMPORTER_PASSWORD`、`ERP_APP_PASSWORD`、`ERP_READ_PASSWORD` 與 `MCP_INTERNAL_TOKEN`。四組資料庫密碼會用於連線 URI，可使用英數字、連字號及底線。已有 MySQL 資料卷時，沿用該環境的資料庫密碼。交付或提交版本控制時使用 `.env.example` 作為範本，實際憑證留在本機。

把 `.env` 既有的 `DEMO_API_KEYS_JSON` 整行替換為下列格式。將兩個 `REPLACE_ME` 鍵名各換成不同的本機測試金鑰，檔案中只保留一個 `DEMO_API_KEYS_JSON=`。DEMO_A 的 TW01 管理員金鑰用於查詢資料集與列級血緣，DEMO_B 用於驗證跨租戶隔離。

~~~dotenv
DEMO_API_KEYS_JSON='{"REPLACE_ME_A":{"tenant_id":"DEMO_A","actor_id":"analyst-a","entity_ids":["TW01","TW02"],"dataset_admin":true},"REPLACE_ME_B":{"tenant_id":"DEMO_B","actor_id":"analyst-b","entity_ids":["TW01"]}}'
~~~

一般驗收可維持 `ENABLE_LLM_AGENT=false` 與 `KYRIBA_MODE=disabled`。OpenAI API key 和 Kyriba 憑證可留空。

## 3. 啟動 API 與 MCP

~~~powershell
docker compose up -d --build
docker compose ps -a
Invoke-RestMethod 'http://127.0.0.1:8000/readyz'
~~~

`db` 就緒後，`init` 會依序執行 Alembic migration、配置資料庫角色、匯入資料及建立情境。`init` 完成時應顯示 `Exited (0)`，`api` 與 `mcp` 應在執行。首次建置可能需要下載映像。

| 服務 | 連線位置 |
| --- | --- |
| FastAPI | 主機 `http://127.0.0.1:8000`，可用 `ERP_API_PORT` 改主機埠 |
| MCP | Compose 網路內 `http://mcp:8001/mcp`，使用 Streamable HTTP |
| MySQL | Compose 網路內 `db:3306`，資料庫 `erp_demo`。目前開發設定另映射主機 `127.0.0.1:3307` |

可由瀏覽器執行 API。 [Swagger UI](http://127.0.0.1:8000/docs) · [OpenAPI JSON](http://127.0.0.1:8000/openapi.json)

## 4. 自動與手動驗收

~~~powershell
docker compose exec -T api python -m scripts.smoke_financebenchmark
$LASTEXITCODE
docker compose run --rm --no-deps init python -m scripts.verify_microsoft_import
~~~

Smoke test 的結束碼應為 `0`。它會檢查 REST、MCP、跨租戶隔離、MySQL 權限及已保存的報表與稽核。匯入驗證會檢查 8,887 筆 Raw 列雜湊及 8,249 條 Raw 到 Canonical 的血緣。2026-11-28 實測結果如下。

| 驗收項目 | 實測結果 | Run ID |
| --- | --- | --- |
| REST `fixture_only`，TW01／2027-01／USD | 淨流入 `400000.0000` | `69d77fda-962c-426b-8b03-444c33d14cf8` |
| REST `baseline`，TW01／2027-01／USD | 淨流出 `957.4150`，97 筆快照文件中 96 筆有原始資料血緣 | `89a6574a-dfeb-46d9-82d7-111364f9cf8d` |
| MCP `calc_net_exposure` | `matches_rest: true`，具名血緣查詢通過 | `cf553cca-9256-47c7-8d7b-540397cb2283` |
| `data_quality_faults` | 預期回傳 `FAILED`，曝險明細為空 | — |
| MySQL 8.4.13 | 唯讀快照、原始表限權、報表及稽核持久化通過 | — |

若要在 Swagger 手動操作，使用 DEMO_A 的金鑰完成 `X-API-Key` 授權。呼叫 `POST /v1/exposures/analyze`，填入以下內容。若修改請求內容，改用新的 `Idempotency-Key` 或留空。

~~~json
{
  "entity_ids": ["TW01"],
  "as_of_date": "2026-11-27",
  "horizon_months": 6,
  "currencies": ["USD"],
  "include_overdue_summary": true,
  "scenario": "baseline"
}
~~~

取得 `run_id` 後，可用 `GET /v1/exposures/{run_id}` 重查報表，以 `GET /v1/exposures/{run_id}/lineage?include_records=true` 查來源列。`GET /v1/datasets/manifest` 可核對資料版本與匯入筆數。換用 DEMO_B 金鑰重查同一份 DEMO_A 報表時，預期為 HTTP 404。

MCP 提供 `get_erp_schema`、`list_legal_entities`、`query_erp_sql`、`calc_net_exposure` 及 `get_exposure_report`。`query_erp_sql` 僅接受具名唯讀查詢。若需另外跑開發測試，可在專案根目錄執行 `uv run pytest -q`。交付前完整回歸共 72 項通過。

## 5. 停止與排查

~~~powershell
docker compose logs --tail 100 init api mcp
docker compose stop
~~~

`docker compose stop` 會保留 MySQL 與 Session 資料卷。排查時可先依下列順序檢查。

| 現象 | 檢查方式 |
| --- | --- |
| `SOURCE_DATA_UNAVAILABLE` 或 `SOURCE_BLOB_MISMATCH` | 核對 ZIP 路徑、檔名與 SHA-256 |
| `Configure a DEMO_A/TW01 key with dataset_admin=true` | 確認 `.env` 只有一筆 `DEMO_API_KEYS_JSON`，且 A 金鑰包含 `TW01`、`dataset_admin=true`。修改後執行 `docker compose up -d --no-deps --force-recreate mcp api` |
| API 埠已被使用 | 在 `.env` 調整 `ERP_API_PORT`，並在瀏覽器及主機指令使用新埠。容器內 smoke test 仍使用 `8000` |
| `/v1/agent/run` 回傳 `503 AGENT_UNAVAILABLE` | 啟用自然語言功能需設定 `ENABLE_LLM_AGENT=true`、`OPENAI_API_KEY`、`OPENAI_MODEL`，再重新建立 API 容器 |

## 6. 架構、資料假設與選用整合

- **互動式架構圖** 直接用瀏覽器開啟專案根目錄的 `architecture.html`，可用「透鏡」切換 REST、Agent／MCP、匯入血緣與 Kyriba 檢視。
- **資料假設** 來源日記帳缺少可驗證的到期日、未結餘額及正式結清紀錄。`baseline` 的到期日、付款配置及多幣別由模擬情境建立。`fixture_only` 提供固定的獨立驗收資料。詳見 `docs/demo-assumptions.md`。
- **Kyriba** 可設 `KYRIBA_MODE=mock` 測試四種唯讀查詢。Live 模式需提供 Kyriba 憑證及法人／帳戶對映，本次 smoke test 驗證 FinanceBenchmark 與核心 MCP。設定見 `docs/kyriba-integration.md`。
- **自然語言 Agent** 已完成 Fake Model 的工具呼叫驗證。Live 模型與其他企業 MAS Agent 的串接，需在有憑證及授權的整合環境另行驗收。
- **其他文件** `README.md` 提供完整開發方式，`docs/api-contract.md` 與 `docs/mcp-tools.md` 提供介面合約，`docs/operating-runbook.md` 提供操作與故障排查。

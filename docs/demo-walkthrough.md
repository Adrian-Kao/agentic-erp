# FinanceBenchmark Aster Electronics 演示流程

1. 按 README 設定 DEMO_A、DEMO_B API key 與 MySQL 各角色密碼，再以固定 commit 的 Microsoft ZIP 啟動 Compose。
2. 執行 `docker compose exec api python -m scripts.smoke_financebenchmark`。用 DEMO_A 的開發管理金鑰開啟 `GET /v1/datasets/manifest`，核對 8,887 筆 raw 來源與 ZIP SHA256。
3. 用 `scenario=baseline`、`TW01`、USD、截至 2026-11-27 和六個月呼叫 `POST /v1/exposures/analyze`。2027-01 淨曝險為 `-957.4150`，報表驗證狀態為 PASSED。另有 USD 逾期應收 `2250.0000`。
4. 呼叫 `GET /v1/exposures/{run_id}/lineage?include_records=true` 核對 Microsoft 原始行位置、雜湊與情境假設。用 DEMO_B 金鑰回查同 run，會收到 404。
5. 換新的 idempotency key 並改用 `scenario=fixture_only`。2027-01 的 AR、AP 及淨流入分別為 `1200000.0000`、`800000.0000`、`400000.0000`。此測試情境沒有 Microsoft 批次。
6. 用 `scenario=data_quality_faults` 觀察缺幣別、超額付款和重複外部單號等異常。報表為 FAILED，金額清單為空。
7. 要在一份報表檢視 2026-12 至 2027-05 的全部六個月，選 baseline、截至 2026-12-01 和 USD／EUR／JPY。11 月 27 日的六個月依規則會從 11 月起算。
8. 可選擇啟用自然語言 Agent 並要求分析基準情境。檢視 MCP tool trace、所選 Skills 及已持久化報表。

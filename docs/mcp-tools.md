# ERP MCP 工具

MCP 使用 Streamable HTTP。手動連線時將 Bearer service token 與 X-API-Key 加入所有請求表頭。工具結果包含 `result` 和 `error`，其中之一用於傳回已驗證內容或結構化服務錯誤。MCP 協定檢查也可能直接回傳協定錯誤。

| 工具 | 參數 |
| --- | --- |
| get_erp_schema | 可選 topic 為 exposure、invoices 或 queries |
| list_legal_entities | 可選 entity_ids |
| query_erp_sql | query_name、filters、limit 1–100 |
| calc_net_exposure | ExposureRequest 的 entity_ids、as_of_date、horizon_months、currencies、include_overdue_summary、scenario |
| get_exposure_report | run_id |

查詢白名單為 `entity_list`、`open_ar`、`open_ap`、`overdue_ar`、`overdue_ap`、`source_lineage`。帳款和血緣查詢要求 filters.as_of_date，可帶 entity_ids、currencies 和 scenario。超過回傳行數會標記 truncated，超過快照總筆數會拒絕。正式報表由 calc_net_exposure 重新查詢生成。

`get_erp_schema(topic="dataset")` 顯示 FinanceBenchmark 方案與情境。具名 `source_lineage` 查詢提供授權範圍內的不透明 ID 與假設，列級檔案位置由具額外權限的 REST lineage API 提供。

已驗證用戶可讀取 `erp://schema/exposure-v1`，並取得 `analyze_fx_exposure` Prompt。用 `uv run python scripts/smoke_test.py --mcp` 驗證清單與實際工具結果。

啟用 `KYRIBA_MODE=mock` 或 `live` 時也會提供四個 Kyriba 唯讀工具。參數、授權對映和分頁範例見 [Kyriba 唯讀整合](kyriba-integration.md)。

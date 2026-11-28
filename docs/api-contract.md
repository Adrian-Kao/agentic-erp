# REST 契約

| 端點 | 行為 |
| --- | --- |
| GET /healthz、/readyz | 程序存活與資料庫就緒 |
| GET /v1/erp/entities、/v1/erp/schema | 活躍法人與公開欄位 |
| POST /v1/exposures/analyze | 計算、保存完整報表，支援 Idempotency-Key |
| GET /v1/exposures/{run_id} | 授權範圍內回查 |
| GET /v1/exposures/{run_id}/lineage | 保存的來源版本與參與紀錄摘要。開發管理者可選 `include_records=true` 查看最多 100 筆來源位置。 |
| GET /v1/datasets/manifest | 開發管理者查驗固定 commit、ZIP SHA256、有效件數與 mapping。 |
| GET /v1/datasets/quality | 欄位能力、隔離件數與缺漏。 |
| GET /v1/scenarios | 當前已授權的情境及版本。 |
| POST /v1/mas/tasks | 已授權 service principal 提交 MAS 任務與接收完整 envelope |
| GET /v1/agent/skills | Skills 公開 metadata |
| POST /v1/agent/run | 選用的自然語言 Agent 與安全重查 |

所有 `/v1` 端點驗證 `X-API-Key`。錯誤返回 code、message、request_id、details 和 retryable。缺少金鑰為 401，越權法人為 403，隱藏不可見報表為 404，idempotency 參數衝突為 409，驗證錯誤為 422，依賴不可用為 503。同一 key 下的參數順序差異會正規化。App 為每次 REST 請求產生新的 X-Request-ID。

報表金額使用四位小數字串。資料品質缺漏以 PARTIAL/WARNINGS 回傳及標明排除原因。重大錯誤回 FAILED/FAILED，金額分組為空。回傳 HTTP 200 僅代表成功處理業務請求，Strategy 需判斷內層 status 和 validation_status。

曝險請求可帶 `scenario`，值為 `baseline`、`data_quality_faults` 或 `fixture_only`。MySQL 預設為 baseline。報表保存資料集版本、匯入批次、參與文件、來源覆蓋率及假設。列級血緣提供來源檔案、工作表、行號和雜湊。

可下載 [ExposureReport schema](schemas/exposure-report-v1.schema.json)、[MAS task schema](schemas/mas-task-v1.schema.json)、[MAS result schema](schemas/mas-result-v1.schema.json) 與 [OpenAPI](schemas/openapi.json)。

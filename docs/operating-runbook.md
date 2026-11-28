# 操作手冊

1. 用 `docker compose ps -a` 確認 DB healthy 與 init exit 0。若失敗，查看 `docker compose logs init db`，檢查 migration、角色密碼和 DB 狀態。
2. 確認 `/healthz`、`/readyz` 和 Swagger 可用。以 smoke script 交叉驗證 REST/MCP 數字、來源雜湊和隔離。
3. 從 API 取得 X-Request-ID，搜尋 api/mcp 結構化日誌，再用 run ID 回查報表與 audit_events。
4. 重大資料異常使用 issue.code 和 record_ref 回到來源 ERP 核對。完成來源修正後建立新 run，保留原報表。
5. 如需停止容器，執行 `docker compose stop`。DB 和 session 使用持久化 volumes。

來源 ZIP 或 Git blob 不符時查看 init 日誌。使用固定 commit 重新取得原始 ZIP，並以 `verify_microsoft_import` 核對件數和 row hashes。情境變更可用新版本設定檔重新執行 `python -m scripts.build_demo_scenario --config ...`。版本會新增，既有報表仍可回查。

SQLite session 採 24 小時 TTL，於下一次查詢時清除已過期列。它保存最後一次 run ID。備份或正式上線時請另訂歷史報表、稽核與 session 生命週期，並在多實例情境改接共享狀態儲存。

現有 demo schema 採來源文件目前狀態搭配 issue_date 與付款有效日。如果要重建歷史時點中當時的取消／作廢狀態，ERP adapter 需提供有效日期或歷史版本。應用依帳款快照計算，不涵蓋現金、借款、衍生品及既有避險。

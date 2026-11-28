# ERP Agent 開發守則

- 依 `ERP_AGENT_CODEX_IMPLEMENTATION.md` 維護 ERP Agent 與共用合約。其他 MAS Agent 各自維護。
- 以授權快照、Decimal 和已持久化報表作為財務事實。保持法人、租戶、幣別及逾期分流。
- SQLAlchemy Core 查詢需參數化。ERP 適配器唯讀，模型只可呼叫具名工具。
- 調整契約後執行 `uv run python scripts/export_openapi.py` 更新產出的 JSON Schema。
- 變更前確認操作者的工作樹。使用合成資料與測試身分，憑證保存在本機環境。
- 驗證命令為 `uv run ruff check .`、`uv run mypy src` 與 `uv run pytest -q`。

# FinanceBenchmark 匯入摘要

來源為 Microsoft FinanceBenchmark 固定 commit `6ced62b961d4c18b2ba53f268b443eb852fb73ca`，Git blob `8f884ba254411a2a8df0773baad635ff195637ad`。ZIP SHA256 為 `5da754beab30462f4df48b93d69345e6d7d09b575ae4304d4bc5569a6572b239`。匯入批次為 `0296d020-8b2b-5707-8a6c-be063924d91b`。

| 來源 | Raw | Canonical | Quarantine |
| --- | ---: | ---: | ---: |
| 客戶 CSV | 1,000 | 1,000 | 0 |
| 供應商 CSV | 1,000 | 1,000 | 0 |
| AP 日記帳 | 3,428 | 3,357 | 71 |
| AR 日記帳 | 3,459 | 2,892 | 567 |
| 合計 | 8,887 | 8,249 | 638 |

MySQL 8.4 驗收重跑同批次和同 seed，筆數及版本 ID 不變。核對 8,887 筆 raw row hash，以及 8,249 條 raw → canonical links。638 筆隔離原因是來源數字的十進位精度超過 `DECIMAL(20,4)` 可精確表示的範圍。raw JSON 保留原值與列位置，canonical 流程沒有自動四捨五入。兩份 Excel 的輔助工作表另列於盤點報告。

Baseline 版本 `1c49bacd-1791-5d96-bb70-f5547aba509f` 存 293 筆情境文件，其中 290 筆可追溯至 Microsoft，3 筆為額外合成展示。Demo 截至日查詢單一法人 TW01 與 USD 時，報表涵蓋 97 筆快照文件，其中 96 筆有原始來源行。

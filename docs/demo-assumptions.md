# Aster Electronics 展示帳務假設

原始資料是 Microsoft FinanceBenchmark 的 D365 合成主檔與日記帳。星曜電子（Aster Electronics Co., Ltd.）是獨立的虛構展示法人，主要為 DEMO_A / TW01，本位幣 TWD。來源法人 USMF、源日期及 USD 金額保留在 canonical journal。模擬文件日期、付款、到期日與 EUR／JPY 幣別有獨立血緣。

baseline、data_quality_faults、fixture_only 是分離情境。固定 2026-11-27 快照的六個曆月從十一月起算。情境文件覆蓋 2026-12 至 2027-05，五月可由 2026-12-01 起算的六個曆月查得。

| 假設 | 規則 |
| --- | --- |
| AST-ENTITY | USMF 原始法人保留於 canonical。TW01／TW02 與 DEMO_B 為展示及隔離測試法人。 |
| AST-DATES | seed 113306018 從受驗證 canonical 日記帳排序、抽樣及配置到期日。模擬開單日為基準日前 20 天，銀行收款日為到期日之後 1～4 天，分期使用到期日。 |
| AST-MAGNITUDE | 採來源 Debit 與 Credit 的淨值絕對值作為展示原額，保留原始行與雜湊。 |
| AST-RE-DENOMINATION | 每月配置 USD、EUR、JPY。EUR／JPY 使用來源數字重設模擬面額，沒有執行匯率轉換。 |
| AST-STATUS | 大部分文件為 OPEN。額外項覆蓋逾期、已結清及取消。故障情境集中缺日期、缺幣別、超額付款、狀態不符和重複外部單號。 |
| AST-PAYMENTS | 每個月與幣別分組的固定位置生成 25% 部分付款。另記錄少數未來付款與 VOID 配置。當日快照只扣截至日 POSTED 金額。 |
| GOLDEN_FIXTURE | 固定小型測試情境。原始 Microsoft 資料與 golden 金額無關。 |

主法人 baseline 每月含 48 筆具到期日的有效 AR/AP 情境單，六個月共 288 筆。另有 3 筆僅供該法人測試的合成文件，以及 2 筆針對其他法人／租戶的衍生文件。所有情境透過版本指標啟用，已建立報表保存其版本和參與文件。

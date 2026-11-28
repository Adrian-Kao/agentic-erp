# Microsoft FinanceBenchmark 來源盤點

- 固定 commit `6ced62b961d4c18b2ba53f268b443eb852fb73ca`
- Git blob `8f884ba254411a2a8df0773baad635ff195637ad`（verified=True）
- ZIP SHA256 `5da754beab30462f4df48b93d69345e6d7d09b575ae4304d4bc5569a6572b239`
- 共 8,887 筆有效資料列。輔助工作表與範本說明列分開統計。
- 原始素材為 Dynamics 365 Finance 的公開合成資料，授權為 MIT。

| 類別 | 工作表 | 有效資料列 | Header 列 | 空白／說明／重複 header | 編碼／BOM／分隔符 |
| --- | --- | ---: | ---: | --- | --- |
| customers | CSV | 1000 | 1 | 0 / 0 / 0 | utf-8 / False / , |
| vendors | CSV | 1000 | 1 | 0 / 0 / 0 | utf-8 / False / , |
| ap | Sheet1 | 3428 | 10 | 1 / 8 / 0 | XLSX |
| ap | data_cache | 0 | — | 0 / 4 / 0 | XLSX |
| ar | Sheet1 | 3459 | 10 | 1 / 8 / 0 | XLSX |
| ar | data_cache | 0 | — | 0 / 4 / 0 | XLSX |

## customers · CSV

來源 `FO Benchmark_Data/Master Data/D365_Customers_DMF_Final.csv`

- 欄位 `CustomerAccount`, `OrganizationName`, `CustomerGroupId`, `CurrencyCode`, `PaymentTermsId`, `CreditMax`, `CreditRating`, `AddressStreet`, `AddressCity`, `AddressState`, `AddressZipCode`, `AddressCountryRegionId`, `PrimaryContactPersonFirstName`, `PrimaryContactPhone`, `PrimaryContactEmail`, `SalesTaxGroup`, `CustomerHoldStatus`
- 日期候選 無
- 金額候選 CreditMax
- 幣別候選 USD
- 公式／重複欄位 無

## vendors · CSV

來源 `FO Benchmark_Data/Master Data/D365_Vendors_DMF_Final.csv`

- 欄位 `VendorAccountNumber`, `VendorOrganizationName`, `VendorGroupId`, `CurrencyCode`, `PaymentTermsId`, `PaymentMethodName`, `AddressStreet`, `AddressCity`, `AddressState`, `AddressZipCode`, `AddressCountryRegionId`, `PrimaryContactPersonFirstName`, `PrimaryContactPhone`, `PrimaryContactEmail`, `SalesTaxGroup`, `VendorHoldStatus`
- 日期候選 無
- 金額候選 無
- 幣別候選 USD
- 公式／重複欄位 無

## ap · Sheet1

來源 `FO Benchmark_Data/Transactional Data/AP_Journal_Import_Updated.xlsx`

- 欄位 `Date`, `Company`, `Account type`, `Non-ledger account`, `Main account`, `Description`, `Currency`, `Debit`, `Credit`, `Offset company`, `Offset account type`, `Offset Non-ledger account`, `Offset main account`, `Line number`
- 日期候選 Date
- 金額候選 Debit
- 幣別候選 USD
- 公式／重複欄位 無

## ap · data_cache

來源 `FO Benchmark_Data/Transactional Data/AP_Journal_Import_Updated.xlsx`

- 欄位 無交易表頭
- 日期候選 無
- 金額候選 無
- 幣別候選 無
- 公式／重複欄位 無

## ar · Sheet1

來源 `FO Benchmark_Data/Transactional Data/AR_Journal_Import_Updated.xlsx`

- 欄位 `Date`, `Company`, `Account type`, `Non-ledger account`, `Main account`, `Description`, `Currency`, `Debit`, `Credit`, `Offset company`, `Offset account type`, `Offset Non-ledger account`, `Offset main account`, `Line number`
- 日期候選 Date
- 金額候選 Debit
- 幣別候選 USD
- 公式／重複欄位 無

## ar · data_cache

來源 `FO Benchmark_Data/Transactional Data/AR_Journal_Import_Updated.xlsx`

- 欄位 無交易表頭
- 日期候選 無
- 金額候選 無
- 幣別候選 無
- 公式／重複欄位 無

## 業務語意

- `company` — **DIRECT**。兩份 journal 的主要公司為 USMF。保留原值，TW01 屬獨立 Demo 情境。
- `counterparty_account` — **DERIVED_VERIFIABLY**。AR 使用 Customer 端的 Non-ledger account。AP 使用 Vendor 端的 Offset Non-ledger account。分別與客戶、供應商主檔精確核對。
- `currency` — **DIRECT**。Journal 欄位為 Currency，主檔欄位為 CurrencyCode。四份資料僅觀察到 USD。
- `journal_amount` — **DIRECT**。保留 Debit 與 Credit 原欄位。來源的 Debit 有值，Credit 為空。此值本身不表示是否開票、是否收款或剩餘金額。
- `journal_date` — **DIRECT**。Excel Journal 範本的日期是美式 MM/DD/YYYY 文字。
- `posting_date` — **UNVERIFIED**。Journal Date 可以標準化。來源沒有證明每筆已過帳或可重建歷史結算。
- `due_date` — **MISSING**。來源沒有發票到期日期。主檔付款條件不提供逐筆到期日。
- `invoice_id` — **MISSING**。Line number 是 journal 行號。
- `settlement_reference` — **MISSING**。無逐筆 settlement 或付款配置參照。
- `open_balance` — **MISSING**。無可驗證的未結餘額。

逐欄空值率、唯一值、型別、異常及前三筆去識別樣本見 `data/profiling/source_columns.csv`。

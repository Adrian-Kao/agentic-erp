---
name: erp-data-quality
description: Review ERP snapshot data quality, missing due dates, and overdue receivables and payables.
---
# ERP Data Quality

1. Verify the source snapshot date, run ID, provenance counts and currency scope.
2. Identify missing due dates, unsupported currencies, invalid amounts, mismatched allocations and overpayment by issue code.
3. Distinguish overdue unpaid documents from items due in the calculation horizon. Preserve warning codes and opaque record references.
4. Check AR minus AP per group and review source counts using the persisted report.
5. Stop if validation failed. Describe omitted data when status is PARTIAL and do not present it as a complete input to Strategy.

See the [validation checklist](references/validation-checklist.md).

# Validation checklist

- Verify cutoff, source counts and the input hash against the persisted run.
- Report MISSING_DUE_DATE as a warning with its record reference. The affected document has no future or overdue bucket.
- Unsupported currencies, overpayment, inconsistent settlements, and mismatched allocations are errors. The service returns FAILED and omits exposure amounts.
- Compare only consistent snapshots and report overdue amounts separately.
Review import coverage separately from scenario quality. Quarantined Microsoft rows are retained as raw data and excluded from canonical mapping. Missing upstream settlement and due-date fields establish simulation assumptions. For the fault scenario, inspect issue codes without presenting amounts as validated.

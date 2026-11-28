# Exposure rules

Open amount is original invoice amount minus POSTED allocations effective through the snapshot day. A posting on the due date is deducted before bucketing. VOID allocations never reduce open amount.

Use the as-of calendar month as the first period. Group separately by entity code, denomination, and due month. Due before the snapshot day is overdue. The entity's functional currency is excluded, as are cash, financing, derivatives and existing hedges.

For positive net AR less AP, report NET_INFLOW. For negative, report NET_OUTFLOW. For zero, report BALANCED. Currency and signs refer to invoice denomination.
# FinanceBenchmark scenario selection

Baseline cash flows come from versioned Aster scenario documents linked to imported Microsoft journal rows. Golden fixtures are isolated in fixture_only. Identify scenario and source coverage from validated provenance. The demonstration creates due dates, payment allocations and non-USD denominations. Starting the six-month horizon in November ends it in April even when the dataset also contains May.

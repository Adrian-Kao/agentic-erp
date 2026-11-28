from typing import Literal

from erp_agent.database.tables import ap, ar

DocumentType = Literal["AR", "AP"]
QueryName = Literal[
    "open_ar", "open_ap", "overdue_ar", "overdue_ap", "entity_list", "source_lineage"
]
QUERY_NAMES = ("open_ar", "open_ap", "overdue_ar", "overdue_ap", "entity_list", "source_lineage")
INVOICE_TABLES = {"AR": ar, "AP": ap}

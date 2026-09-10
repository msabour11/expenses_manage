from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    create_accounting_dimensions_for_doctype,
)


def execute():
    for doctype in ("Expenses Entry", "Expenses"):
        create_accounting_dimensions_for_doctype(doctype)

# Copyright (c) 2026, Mohamed AbdElsabour and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from expenses_manage.expenses_manage.doctype.expenses_entry.expenses_entry import (
    calculate_inclusive_taxes,
)
from expenses_manage.expenses_manage.report.expense_analysis.expense_analysis import (
    validate_filters,
)


class TestExpensesEntry(FrappeTestCase):
    def test_calculates_tax_included_amount(self):
        net_amount, taxes = calculate_inclusive_taxes(
            115,
            [{"account_head": "Input VAT", "rate": 15, "description": "VAT"}],
        )

        self.assertEqual(net_amount, 100)
        self.assertEqual(taxes[0]["tax_amount"], 15)

    def test_last_tax_line_absorbs_rounding_difference(self):
        net_amount, taxes = calculate_inclusive_taxes(
            100,
            [
                {"account_head": "Tax A", "rate": 10},
                {"account_head": "Tax B", "rate": 5},
            ],
        )

        self.assertEqual(net_amount, 86.96)
        self.assertEqual(sum(tax["tax_amount"] for tax in taxes), 13.04)

    def test_report_rejects_reversed_date_range(self):
        filters = frappe._dict(
            company="_Test Company",
            from_date="2026-02-01",
            to_date="2026-01-01",
        )

        self.assertRaises(frappe.ValidationError, validate_filters, filters)

    def test_report_defaults_group_by(self):
        filters = frappe._dict(
            company="_Test Company",
            from_date="2026-01-01",
            to_date="2026-01-31",
        )

        validate_filters(filters)

        self.assertEqual(filters.group_by, "Expense Account")

# Copyright (c) 2026, Mohamed AbdElsabour and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from expenses_manage.expenses_manage.report.expense_analysis.expense_analysis import (
    validate_filters,
)


class TestExpensesEntry(FrappeTestCase):
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

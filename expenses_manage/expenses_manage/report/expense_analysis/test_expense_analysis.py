from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from expenses_manage.expenses_manage.report.expense_analysis.expense_analysis import (
    get_data,
)


class TestExpenseAnalysis(FrappeTestCase):
    def test_branch_filter_is_applied_to_gl_entries(self):
        filters = frappe._dict(
            company="_Test Company",
            from_date="2026-01-01",
            to_date="2026-01-31",
            branch="Cairo",
            group_by="Expense Account",
        )

        with patch.object(frappe.db, "sql", return_value=[]) as sql:
            get_data(filters)

        query, values = sql.call_args.args[:2]
        self.assertIn("gle.branch = %(branch)s", query)
        self.assertEqual(values["branch"], "Cairo")

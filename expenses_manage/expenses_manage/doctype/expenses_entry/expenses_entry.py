# Copyright (c) 2026, Mohamed AbdElsabour and contributors
# For license information, please see license.txt

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
)
from erpnext.accounts.general_ledger import (
    make_gl_entries,
    make_reverse_gl_entries,
)
from erpnext.accounts.utils import get_balance_on
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.setup.utils import get_exchange_rate
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import flt


def calculate_inclusive_taxes(gross_amount, tax_rows, precision=2):
    """Return the net amount and tax allocations for a tax-inclusive amount."""
    valid_tax_rows = [
        tax for tax in tax_rows if flt(tax.get("rate")) and tax.get("account_head")
    ]
    if not valid_tax_rows:
        return flt(gross_amount, precision), []

    total_rate = sum(flt(tax.get("rate")) for tax in valid_tax_rows)
    if total_rate <= -100:
        frappe.throw(_("The total tax rate must be greater than -100%."))

    gross_amount = flt(gross_amount, precision)
    net_amount = flt(gross_amount / (1 + total_rate / 100), precision)
    allocated_tax = 0.0
    allocations = []

    for index, tax in enumerate(valid_tax_rows):
        rate = flt(tax.get("rate"))
        if index == len(valid_tax_rows) - 1:
            tax_amount = flt(gross_amount - net_amount - allocated_tax, precision)
        else:
            tax_amount = flt(net_amount * rate / 100, precision)

        if not tax_amount:
            continue

        allocated_tax += tax_amount
        allocations.append(
            {
                "account_head": tax.get("account_head"),
                "description": tax.get("description"),
                "rate": rate,
                "tax_amount": tax_amount,
            }
        )

    return net_amount, allocations


@frappe.whitelist()
def get_account_balance(account, company, posting_date=None, date=None):
    posting_date = posting_date or date
    if not posting_date:
        frappe.throw(_("Posting Date is required."))

    account_details = frappe.get_cached_value(
        "Account",
        account,
        ["company", "is_group", "disabled", "account_type"],
        as_dict=True,
    )
    if not account_details or account_details.company != company:
        frappe.throw(_("The selected account does not belong to this company."))
    if account_details.is_group or account_details.disabled:
        frappe.throw(_("The selected account is not available for payment."))
    if account_details.account_type not in ("Bank", "Cash"):
        frappe.throw(_("Account Paid From must be a Bank or Cash account."))

    return get_balance_on(account=account, date=posting_date, company=company)


class ExpensesEntry(AccountsController):
    """A direct expense payment from one cash/bank account.

    Amounts stored in ``amount`` and ``paid_amount`` are in company currency.
    ``paid_amount_in_account_currency`` stores the amount credited to the payment
    account when that account uses a foreign currency.
    """

    def validate(self):
        self.payment_type = "Expenses"
        self._set_payment_account_from_mode_of_payment()
        self._validate_payment_account()
        if self.default_cost_center:
            self._validate_cost_center(self.default_cost_center)
        if self.default_project:
            self._validate_project(self.default_project)
        self.apply_custom_taxes()
        self._validate_and_normalize_expense_rows()
        self._set_totals_and_exchange_rate()
        self.validate_company_in_accounting_dimension()

    def apply_custom_taxes(self):
        """Split selected tax-inclusive expense rows into net and tax rows."""
        if not self.custom_tax_template:
            self._restore_gross_amounts()
            self.custom_total_taxes_and_charges = 0
            return

        tax_rows = self._get_tax_template_rows()
        if not any(
            flt(tax.get("rate")) and tax.get("account_head") for tax in tax_rows
        ):
            frappe.throw(
                _("Tax Template {0} has no tax lines with an account and rate.").format(
                    frappe.bold(self.custom_tax_template)
                )
            )

        # Always rebuild generated rows so recalculation is idempotent.
        self.expenses = [
            row for row in self.expenses if not row.get("custom_is_tax_row")
        ]
        accounting_dimensions = get_accounting_dimensions()
        total_tax = 0.0

        for row in list(self.expenses):
            gross_amount = flt(row.get("custom_gross_amount"))

            if not row.get("custom_tax_included"):
                if gross_amount:
                    row.amount = gross_amount
                    row.custom_gross_amount = 0
                continue

            if not gross_amount:
                gross_amount = flt(row.amount)
                row.custom_gross_amount = gross_amount
            if not gross_amount:
                continue

            precision = row.precision("amount")
            net_amount, allocations = calculate_inclusive_taxes(
                gross_amount, tax_rows, precision
            )
            row.amount = net_amount

            for tax in allocations:
                tax_amount = tax["tax_amount"]
                total_tax += tax_amount
                tax_row = {
                    "account_paid_to": tax["account_head"],
                    "amount": tax_amount,
                    "cost_center": row.cost_center or self.default_cost_center,
                    "project": row.project or self.default_project,
                    "remarks": _("{0} @ {1}%").format(
                        tax.get("description") or tax["account_head"], tax["rate"]
                    ),
                    "custom_tax_included": 0,
                    "custom_is_tax_row": 1,
                    "custom_gross_amount": 0,
                }
                for dimension in accounting_dimensions:
                    tax_row[dimension] = row.get(dimension) or self.get(dimension)
                self.append("expenses", tax_row)

        self.custom_total_taxes_and_charges = flt(
            total_tax, self.precision("custom_total_taxes_and_charges")
        )

    def _get_tax_template_rows(self):
        try:
            template = frappe.get_doc(
                "Purchase Taxes and Charges Template", self.custom_tax_template
            )
        except frappe.DoesNotExistError:
            frappe.throw(
                _("Tax Template {0} not found.").format(
                    frappe.bold(self.custom_tax_template)
                )
            )

        if template.get("company") and template.company != self.company:
            frappe.throw(
                _("Tax Template {0} does not belong to company {1}.").format(
                    frappe.bold(self.custom_tax_template), frappe.bold(self.company)
                )
            )

        return [
            {
                "account_head": tax.account_head,
                "rate": tax.rate,
                "description": tax.description,
            }
            for tax in template.taxes
        ]

    def _restore_gross_amounts(self):
        restored_rows = []
        for row in self.expenses:
            if row.get("custom_is_tax_row"):
                continue
            if flt(row.get("custom_gross_amount")):
                row.amount = flt(row.custom_gross_amount, row.precision("amount"))
                row.custom_gross_amount = 0
            restored_rows.append(row)
        self.expenses = restored_rows

    def on_submit(self):
        self._post_gl_entries()

    def on_cancel(self):
        self.ignore_linked_doctypes = (
            "GL Entry",
            "Payment Ledger Entry",
            "Repost Accounting Ledger",
            "Repost Accounting Ledger Items",
        )
        super().on_cancel()
        self._post_gl_entries(cancel=True)

    def _set_payment_account_from_mode_of_payment(self):
        if not self.mode_of_payment:
            return

        enabled = frappe.get_cached_value(
            "Mode of Payment", self.mode_of_payment, "enabled"
        )
        if not enabled:
            frappe.throw(
                _("Mode of Payment {0} is disabled.").format(
                    frappe.bold(self.mode_of_payment)
                ),
                title=_("Disabled Mode of Payment"),
            )

        account = frappe.db.get_value(
            "Mode of Payment Account",
            {
                "parent": self.mode_of_payment,
                "company": self.company,
            },
            "default_account",
        )
        if not account:
            frappe.throw(
                _(
                    "Please set a default account for company {0} in Mode of Payment {1}."
                ).format(frappe.bold(self.company), frappe.bold(self.mode_of_payment)),
                title=_("Missing Account"),
            )

        self.account_paid_from = account

    def _validate_payment_account(self):
        account = self._get_account(self.account_paid_from, _("Account Paid From"))
        if account.account_type not in ("Bank", "Cash"):
            frappe.throw(
                _("Account Paid From must be a Bank or Cash account."),
                title=_("Invalid Payment Account"),
            )

        self.account_currency_from = account.account_currency or self.company_currency
        self.multi_currency = self.account_currency_from != self.company_currency
        self.currency = self.account_currency_from

    def _validate_and_normalize_expense_rows(self):
        if not self.expenses:
            frappe.throw(_("Please add at least one expense line."))

        accounting_dimensions = get_accounting_dimensions()
        for row in self.expenses:
            account = self._get_account(
                row.account_paid_to, _("Row #{0}: Expense Account").format(row.idx)
            )
            if not row.get("custom_is_tax_row") and account.root_type != "Expense":
                frappe.throw(
                    _("Row #{0}: Account {1} must be an Expense account.").format(
                        row.idx, frappe.bold(row.account_paid_to)
                    )
                )
            expense_account_currency = account.account_currency or self.company_currency
            if expense_account_currency != self.company_currency:
                frappe.throw(
                    _(
                        "Row #{0}: Expense account currency must be the company currency {1}."
                    ).format(row.idx, frappe.bold(self.company_currency))
                )
            if row.account_paid_to == self.account_paid_from:
                frappe.throw(
                    _(
                        "Row #{0}: Expense Account cannot be the payment account."
                    ).format(row.idx)
                )

            row.amount = flt(row.amount, row.precision("amount"))
            if row.amount <= 0:
                frappe.throw(
                    _("Row #{0}: Amount must be greater than zero.").format(row.idx)
                )

            row.cost_center = row.cost_center or self.default_cost_center
            if not row.cost_center:
                frappe.throw(_("Row #{0}: Cost Center is required.").format(row.idx))
            self._validate_cost_center(row.cost_center, row.idx)

            row.project = row.project or self.default_project
            if row.project:
                self._validate_project(row.project, row.idx)

            for dimension in accounting_dimensions:
                row.set(dimension, row.get(dimension) or self.get(dimension))

            row.account_currency = self.company_currency
            row.exchange_rate = 1
            row.amount_in_account_currency = row.amount
            row.exchange_rate_date = None
            row.currency_exchange_link = None

    def _validate_cost_center(self, cost_center, row_idx=None):
        cost_center_details = frappe.get_cached_value(
            "Cost Center",
            cost_center,
            ["company", "is_group", "disabled"],
            as_dict=True,
        )
        prefix = _("Row {0}: ").format(row_idx) if row_idx else ""
        if not cost_center_details:
            frappe.throw(
                _("{0}Cost Center {1} does not exist.").format(
                    prefix, frappe.bold(cost_center)
                )
            )
        if cost_center_details.company != self.company:
            frappe.throw(
                _("{0}Cost Center {1} does not belong to company {2}.").format(
                    prefix, frappe.bold(cost_center), frappe.bold(self.company)
                )
            )
        if cost_center_details.is_group:
            frappe.throw(
                _("{0}Cost Center {1} is a group. Select a leaf cost center.").format(
                    prefix, frappe.bold(cost_center)
                )
            )
        if cost_center_details.disabled:
            frappe.throw(
                _("{0}Cost Center {1} is disabled.").format(
                    prefix, frappe.bold(cost_center)
                )
            )

    def _validate_project(self, project, row_idx=None):
        project_company = frappe.db.get_value("Project", project, "company")
        prefix = _("Row {0}: ").format(row_idx) if row_idx else ""
        if not project_company:
            frappe.throw(
                _("{0}Project {1} does not exist.").format(prefix, frappe.bold(project))
            )
        if project_company != self.company:
            frappe.throw(
                _("{0}Project {1} does not belong to company {2}.").format(
                    prefix, frappe.bold(project), frappe.bold(self.company)
                )
            )

    def validate_company_in_accounting_dimension(self):
        if hasattr(AccountsController, "validate_company_in_accounting_dimension"):
            super().validate_company_in_accounting_dimension()
            return

        doc_field = DocType("DocField")
        accounting_dimension = DocType("Accounting Dimension")
        dimension_list = (
            frappe.qb.from_(accounting_dimension)
            .select(accounting_dimension.document_type)
            .join(doc_field)
            .on(doc_field.parent == accounting_dimension.document_type)
            .where(doc_field.fieldname == "company")
        ).run(as_list=True)

        dimension_list = sum(dimension_list, ["Project", "Cost Center"])
        self.validate_company(dimension_list)

        for child in self.get_all_children() or []:
            self.validate_company(dimension_list, child)

    def validate_company(self, dimension_list, child=None):
        if hasattr(AccountsController, "validate_company"):
            super().validate_company(dimension_list, child=child)
            return

        for dimension in dimension_list:
            if not child:
                dimension_value = self.get(frappe.scrub(dimension))
            else:
                dimension_value = child.get(frappe.scrub(dimension))

            if dimension_value:
                company = frappe.get_cached_value(dimension, dimension_value, "company")
                if company and company != self.company:
                    frappe.throw(
                        _("{0}: {1} does not belong to the Company: {2}").format(
                            dimension, frappe.bold(dimension_value), self.company
                        )
                    )

    def _set_totals_and_exchange_rate(self):
        self.total_debit = flt(
            sum(flt(row.amount) for row in self.expenses),
            self.precision("total_debit"),
        )
        self.paid_amount = self.total_debit

        if self.multi_currency:
            if not flt(self.exchange_rate):
                self.exchange_rate = get_exchange_rate(
                    self.account_currency_from,
                    self.company_currency,
                    self.posting_date,
                )
            if flt(self.exchange_rate) <= 0:
                frappe.throw(
                    _(
                        "A positive exchange rate is required for a foreign-currency payment account."
                    )
                )
            self.exchange_rate_date = self.posting_date
            self.paid_amount_in_account_currency = flt(
                self.paid_amount / flt(self.exchange_rate),
                self.precision("paid_amount_in_account_currency"),
            )
        else:
            self.exchange_rate = 1
            self.exchange_rate_date = None
            self.currency_exchange_link = None
            self.paid_amount_in_account_currency = self.paid_amount

        self.conversion_rate = self.exchange_rate

        if self.paid_amount <= 0:
            frappe.throw(_("Paid Amount must be greater than zero."))

    def _get_account(self, account_name, label):
        if not account_name:
            frappe.throw(_("{0} is required.").format(label))

        account = frappe.get_cached_value(
            "Account",
            account_name,
            [
                "company",
                "is_group",
                "disabled",
                "root_type",
                "account_type",
                "account_currency",
            ],
            as_dict=True,
        )
        if not account:
            frappe.throw(
                _("Account {0} does not exist.").format(frappe.bold(account_name))
            )
        if account.company != self.company:
            frappe.throw(
                _("Account {0} does not belong to company {1}.").format(
                    frappe.bold(account_name), frappe.bold(self.company)
                )
            )
        if account.is_group:
            frappe.throw(
                _("Account {0} is a group account.").format(frappe.bold(account_name))
            )
        if account.disabled:
            frappe.throw(
                _("Account {0} is disabled.").format(frappe.bold(account_name))
            )
        return account

    def get_gl_entries(self):
        expense_accounts = ", ".join(
            sorted({row.account_paid_to for row in self.expenses})
        )
        gl_entries = [
            self.get_gl_dict(
                {
                    "account": self.account_paid_from,
                    "against": expense_accounts,
                    "credit": self.paid_amount,
                    "credit_in_account_currency": self.paid_amount_in_account_currency,
                    "cost_center": self.default_cost_center,
                    "project": self.default_project,
                    "account_currency": self.account_currency_from,
                },
                account_currency=self.account_currency_from,
            )
        ]

        for row in self.expenses:
            gl_entries.append(
                self.get_gl_dict(
                    {
                        "account": row.account_paid_to,
                        "against": self.account_paid_from,
                        "debit": row.amount,
                        "debit_in_account_currency": row.amount,
                        "cost_center": row.cost_center,
                        "project": row.project,
                        "remarks": row.remarks or self.remarks,
                        "voucher_detail_no": row.name,
                        "account_currency": self.company_currency,
                    },
                    account_currency=self.company_currency,
                    item=row,
                )
            )

        return gl_entries

    def _post_gl_entries(self, cancel=False):
        if cancel:
            make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
            return

        merge_entries = frappe.db.get_single_value(
            "Accounts Settings", "merge_similar_account_heads"
        )
        make_gl_entries(
            self.get_gl_entries(),
            merge_entries=merge_entries,
            update_outstanding="No",
        )

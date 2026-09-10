# Copyright (c) 2026, Mohamed AbdElsabour and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.setup.utils import get_exchange_rate


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
        self._validate_and_normalize_expense_rows()
        self._set_totals_and_exchange_rate()
        self.validate_company_in_accounting_dimension()

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

        for row in self.expenses:
            account = self._get_account(
                row.account_paid_to, _("Row #{0}: Expense Account").format(row.idx)
            )
            if account.root_type != "Expense":
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

            row.account_currency = self.company_currency
            row.exchange_rate = 1
            row.amount_in_account_currency = row.amount
            row.exchange_rate_date = None
            row.currency_exchange_link = None

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

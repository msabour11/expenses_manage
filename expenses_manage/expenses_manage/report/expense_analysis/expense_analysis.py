from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

GROUP_BY_OPTIONS = {
    "Expense Account": {
        "select": "gle.account AS expense_account",
        "group_by": "gle.account",
        "order_by": "gle.account",
    },
    "Cost Center": {
        "select": "gle.cost_center",
        "group_by": "gle.cost_center",
        "order_by": "gle.cost_center",
    },
    "Project": {
        "select": "gle.project",
        "group_by": "gle.project",
        "order_by": "gle.project",
    },
    "Voucher": {
        "select": "gle.voucher_type, gle.voucher_no",
        "group_by": "gle.voucher_type, gle.voucher_no",
        "order_by": "gle.voucher_type, gle.voucher_no",
    },
}


def execute(filters=None):
    filters = frappe._dict(filters or {})
    validate_filters(filters)

    currency = frappe.get_cached_value("Company", filters.company, "default_currency")
    columns = get_columns(filters.group_by)
    data = get_data(filters)

    for row in data:
        row["currency"] = currency

    net_expense = sum(flt(row.get("net_expense")) for row in data)
    report_summary = [
        {
            "label": _("Net Expense"),
            "value": net_expense,
            "indicator": "Red" if net_expense > 0 else "Green",
            "datatype": "Currency",
            "currency": currency,
        },
        {
            "label": _("Total Debits"),
            "value": sum(flt(row.get("debit")) for row in data),
            "indicator": "Blue",
            "datatype": "Currency",
            "currency": currency,
        },
        {
            "label": _("Total Credits"),
            "value": sum(flt(row.get("credit")) for row in data),
            "indicator": "Orange",
            "datatype": "Currency",
            "currency": currency,
        },
    ]

    return columns, data, None, None, report_summary


def validate_filters(filters):
    if not filters.company:
        frappe.throw(_("Company is required."))
    if not filters.from_date or not filters.to_date:
        frappe.throw(_("From Date and To Date are required."))
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("From Date cannot be after To Date."))

    filters.group_by = filters.group_by or "Expense Account"
    if filters.group_by != "Detailed" and filters.group_by not in GROUP_BY_OPTIONS:
        frappe.throw(_("Invalid Group By value."))


def get_data(filters):
    conditions = [
        "gle.company = %(company)s",
        "gle.posting_date BETWEEN %(from_date)s AND %(to_date)s",
        "gle.is_cancelled = 0",
        "account.root_type = 'Expense'",
    ]
    query_values = {
        "company": filters.company,
        "from_date": filters.from_date,
        "to_date": filters.to_date,
    }

    if filters.account:
        account_bounds = frappe.db.get_value(
            "Account",
            {"name": filters.account, "company": filters.company},
            ["lft", "rgt"],
            as_dict=True,
        )
        if not account_bounds:
            frappe.throw(_("The selected Account does not belong to this Company."))
        conditions.extend(
            [
                "account.lft >= %(account_lft)s",
                "account.rgt <= %(account_rgt)s",
            ]
        )
        query_values.update(
            {
                "account_lft": account_bounds.lft,
                "account_rgt": account_bounds.rgt,
            }
        )

    for fieldname in ("branch", "cost_center", "project", "voucher_type"):
        if filters.get(fieldname):
            conditions.append(f"gle.{fieldname} = %({fieldname})s")
            query_values[fieldname] = filters.get(fieldname)

    where_clause = " AND ".join(conditions)

    if filters.group_by == "Detailed":
        return frappe.db.sql(
            f"""
				SELECT
					gle.posting_date,
					gle.account AS expense_account,
					gle.voucher_type,
					gle.voucher_no,
					gle.party_type,
					gle.party,
					gle.against,
					gle.cost_center,
					gle.project,
					gle.remarks,
					gle.debit,
					gle.credit,
					(gle.debit - gle.credit) AS net_expense
				FROM `tabGL Entry` gle
				INNER JOIN `tabAccount` account ON account.name = gle.account
				WHERE {where_clause}
				ORDER BY gle.posting_date, gle.creation, gle.name
			""",
            query_values,
            as_dict=True,
        )

    grouping = GROUP_BY_OPTIONS[filters.group_by]
    return frappe.db.sql(
        f"""
			SELECT
				{grouping['select']},
				SUM(gle.debit) AS debit,
				SUM(gle.credit) AS credit,
				SUM(gle.debit - gle.credit) AS net_expense
			FROM `tabGL Entry` gle
			INNER JOIN `tabAccount` account ON account.name = gle.account
			WHERE {where_clause}
			GROUP BY {grouping['group_by']}
			ORDER BY {grouping['order_by']}
		""",
        query_values,
        as_dict=True,
    )


def get_columns(group_by):
    currency_columns = [
        {
            "fieldname": "debit",
            "label": _("Debit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "fieldname": "credit",
            "label": _("Credit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "fieldname": "net_expense",
            "label": _("Net Expense"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
    ]

    if group_by == "Expense Account":
        return [
            link_column("expense_account", _("Expense Account"), "Account", 240)
        ] + currency_columns
    if group_by == "Cost Center":
        return [
            link_column("cost_center", _("Cost Center"), "Cost Center", 220)
        ] + currency_columns
    if group_by == "Project":
        return [link_column("project", _("Project"), "Project", 220)] + currency_columns
    if group_by == "Voucher":
        return [
            {"fieldname": "voucher_type", "label": _("Voucher Type"), "width": 150},
            {
                "fieldname": "voucher_no",
                "label": _("Voucher"),
                "fieldtype": "Dynamic Link",
                "options": "voucher_type",
                "width": 200,
            },
        ] + currency_columns

    return [
        {
            "fieldname": "posting_date",
            "label": _("Posting Date"),
            "fieldtype": "Date",
            "width": 110,
        },
        link_column("expense_account", _("Expense Account"), "Account", 220),
        {"fieldname": "voucher_type", "label": _("Voucher Type"), "width": 140},
        {
            "fieldname": "voucher_no",
            "label": _("Voucher"),
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",
            "width": 190,
        },
        {"fieldname": "party_type", "label": _("Party Type"), "width": 110},
        {
            "fieldname": "party",
            "label": _("Party"),
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "width": 180,
        },
        link_column("cost_center", _("Cost Center"), "Cost Center", 180),
        link_column("project", _("Project"), "Project", 160),
        {"fieldname": "against", "label": _("Against"), "width": 180},
        {"fieldname": "remarks", "label": _("Remarks"), "width": 260},
    ] + currency_columns


def link_column(fieldname, label, options, width):
    return {
        "fieldname": fieldname,
        "label": label,
        "fieldtype": "Link",
        "options": options,
        "width": width,
    }

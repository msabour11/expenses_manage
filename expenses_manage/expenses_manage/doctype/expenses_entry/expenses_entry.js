// Copyright (c) 2026, Mohamed AbdElsabour and contributors
// For license information, please see license.txt

frappe.ui.form.on("Expenses Entry", {
	setup(frm) {
		frm.set_query("mode_of_payment", () => ({
			filters: { enabled: 1 },
		}));

		frm.set_query("account_paid_from", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				account_type: ["in", ["Bank", "Cash"]],
			},
		}));

		frm.set_query("default_cost_center", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));

		frm.set_query("account_paid_to", "expenses", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				root_type: "Expense",
			},
		}));

		frm.set_query("cost_center", "expenses", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));

		frm.set_query("project", "expenses", () => ({
			filters: { company: frm.doc.company },
		}));
	},

	refresh(frm) {
		frm.events.show_general_ledger(frm);
		if (frm.doc.docstatus === 0) {
			calculate_totals(frm);
		}
	},

	show_general_ledger(frm) {
		if (frm.doc.docstatus > 0) {
			frm.add_custom_button(__("Ledger"), () => {
				frappe.route_options = {
					voucher_type: frm.doctype,
					voucher_no: frm.doc.name,
					from_date: frm.doc.posting_date,
					to_date: frm.doc.posting_date,
					company: frm.doc.company,
					group_by: "",
					show_cancelled_entries: frm.doc.docstatus === 2,
				};
				frappe.set_route("query-report", "General Ledger");
			});
		}
	},

	company(frm) {
		frm.set_value({
			mode_of_payment: null,
			account_paid_from: null,
			account_currency_from: null,
			default_cost_center: null,
			multi_currency: 0,
			exchange_rate: 1,
		});
		frm.clear_table("expenses");
		frm.refresh_field("expenses");
		calculate_totals(frm);
	},

	mode_of_payment(frm) {
		if (!frm.doc.mode_of_payment) {
			return;
		}

		const mode_of_payment = frm.doc.mode_of_payment;
		const company = frm.doc.company;
		erpnext.accounts.pos.get_payment_mode_account(frm, mode_of_payment, (account) => {
			if (frm.doc.mode_of_payment === mode_of_payment && frm.doc.company === company) {
				frm.set_value("account_paid_from", account);
			}
		});
	},

	account_paid_from(frm) {
		if (!frm.doc.account_paid_from) {
			frm.set_value({ account_currency_from: null, multi_currency: 0, exchange_rate: 1 });
			return;
		}

		frappe.db.get_value("Account", frm.doc.account_paid_from, "account_currency").then((r) => {
			const account_currency = r.message && r.message.account_currency;
			frm.set_value("account_currency_from", account_currency).then(() => {
				update_exchange_rate(frm);
			});
		});
	},

	posting_date(frm) {
		update_exchange_rate(frm);
	},

	exchange_rate(frm) {
		calculate_totals(frm);
	},

	default_cost_center(frm) {
		(frm.doc.expenses || []).forEach((row) => {
			if (!row.cost_center) {
				frappe.model.set_value(
					row.doctype,
					row.name,
					"cost_center",
					frm.doc.default_cost_center,
				);
			}
		});
	},
});

frappe.ui.form.on("Expenses", {
	expenses_add(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "cost_center", frm.doc.default_cost_center);
	},

	expenses_remove(frm) {
		calculate_totals(frm);
	},

	amount(frm) {
		calculate_totals(frm);
	},
});

function calculate_totals(frm) {
	const total = (frm.doc.expenses || []).reduce((sum, row) => sum + flt(row.amount), 0);
	const rate = flt(frm.doc.exchange_rate) || 1;

	frm.set_value("total_debit", total);
	frm.set_value("paid_amount", total);
	frm.set_value(
		"paid_amount_in_account_currency",
		frm.doc.multi_currency ? flt(total / rate) : total,
	);
}

function update_exchange_rate(frm) {
	if (!frm.doc.company || !frm.doc.account_currency_from || !frm.doc.posting_date) {
		return;
	}

	frappe.db.get_value("Company", frm.doc.company, "default_currency").then((r) => {
		const company_currency = r.message && r.message.default_currency;
		if (!company_currency) {
			return;
		}

		if (frm.doc.account_currency_from === company_currency) {
			frm.set_value({
				multi_currency: 0,
				exchange_rate: 1,
				exchange_rate_date: null,
				currency_exchange_link: null,
			}).then(() => calculate_totals(frm));
			return;
		}

		frm.set_value("multi_currency", 1);
		frappe.call({
			method: "erpnext.setup.utils.get_exchange_rate",
			args: {
				from_currency: frm.doc.account_currency_from,
				to_currency: company_currency,
				transaction_date: frm.doc.posting_date,
			},
			callback(r) {
				if (r.message) {
					frm.set_value({
						exchange_rate: r.message,
						exchange_rate_date: frm.doc.posting_date,
					}).then(() => calculate_totals(frm));
				}
			},
		});
	});
}

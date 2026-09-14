// Copyright (c) 2026, Mohamed AbdElsabour and contributors
// For license information, please see license.txt

frappe.ui.form.on("Expenses Entry", {
	setup(frm) {
		setup_accounting_dimensions(frm);

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

		frm.set_query("default_project", () => ({
			filters: { company: frm.doc.company },
		}));

		frm.set_query("custom_tax_template", () => ({
			filters: { company: frm.doc.company },
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
		const values = {
			mode_of_payment: null,
			account_paid_from: null,
			account_currency_from: null,
			default_cost_center: null,
			default_project: null,
			multi_currency: 0,
			exchange_rate: 1,
			custom_tax_template: null,
			custom_total_taxes_and_charges: 0,
		};
		(frm.expense_accounting_dimensions || []).forEach((dimension) => {
			values[dimension] = null;
		});
		frm.set_value(values);
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
			frm.set_value({
				account_currency_from: null,
				account_balance_from: 0,
				multi_currency: 0,
				exchange_rate: 1,
			});
			return;
		}

		frappe.db.get_value("Account", frm.doc.account_paid_from, "account_currency").then((r) => {
			const account_currency = r.message && r.message.account_currency;
			frm.set_value("account_currency_from", account_currency).then(() => {
				update_exchange_rate(frm);
				update_account_balance(frm);
			});
		});
	},

	posting_date(frm) {
		update_exchange_rate(frm);
		update_account_balance(frm);
	},

	exchange_rate(frm) {
		calculate_totals(frm);
	},

	default_cost_center(frm) {
		set_default_in_blank_rows(frm, "cost_center", frm.doc.default_cost_center);
	},

	default_project(frm) {
		set_default_in_blank_rows(frm, "project", frm.doc.default_project);
	},

	custom_tax_template(frm) {
		calculate_and_apply_taxes(frm);
	},
});

frappe.ui.form.on("Expenses", {
	expenses_add(frm, cdt, cdn) {
		const defaults = {
			cost_center: frm.doc.default_cost_center,
			project: frm.doc.default_project,
		};
		(frm.expense_accounting_dimensions || []).forEach((dimension) => {
			defaults[dimension] = frm.doc[dimension];
		});
		frappe.model.set_value(cdt, cdn, defaults);
	},

	expenses_remove(frm) {
		if (frm.doc.custom_tax_template) {
			calculate_and_apply_taxes(frm);
		} else {
			calculate_totals(frm);
		}
	},

	amount(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.custom_tax_included && !row.custom_is_tax_row) {
			frappe.model.set_value(cdt, cdn, "custom_gross_amount", flt(row.amount));
			calculate_and_apply_taxes(frm);
		} else {
			calculate_totals(frm);
		}
	},

	custom_tax_included(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.custom_is_tax_row) {
			frappe.model.set_value(cdt, cdn, "custom_tax_included", 0);
			return;
		}

		if (row.custom_tax_included) {
			if (!flt(row.custom_gross_amount)) {
				frappe.model.set_value(cdt, cdn, "custom_gross_amount", flt(row.amount));
			}
		} else if (flt(row.custom_gross_amount)) {
			frappe.model.set_value(cdt, cdn, "amount", flt(row.custom_gross_amount));
			frappe.model.set_value(cdt, cdn, "custom_gross_amount", 0);
		}

		calculate_and_apply_taxes(frm);
	},
});

function setup_accounting_dimensions(frm) {
	frappe.call({
		method: "erpnext.accounts.doctype.accounting_dimension.accounting_dimension.get_dimensions",
		callback(r) {
			const dimensions = (r.message && r.message[0]) || [];
			frm.expense_accounting_dimensions = dimensions.map(
				(dimension) => dimension.fieldname
			);

			frm.expense_accounting_dimensions.forEach((dimension) => {
				frm.cscript[dimension] = (doc, cdt, cdn) => {
					erpnext.utils.copy_value_in_all_rows(doc, cdt, cdn, "expenses", dimension);
				};
			});
		},
	});
}

function set_default_in_blank_rows(frm, fieldname, value) {
	(frm.doc.expenses || []).forEach((row) => {
		if (!row[fieldname]) {
			frappe.model.set_value(row.doctype, row.name, fieldname, value);
		}
	});
}

function calculate_totals(frm) {
	const total = (frm.doc.expenses || []).reduce((sum, row) => sum + flt(row.amount), 0);
	const rate = flt(frm.doc.exchange_rate) || 1;

	frm.set_value("total_debit", total);
	frm.set_value("paid_amount", total);
	frm.set_value(
		"paid_amount_in_account_currency",
		frm.doc.multi_currency ? flt(total / rate) : total
	);
}

function calculate_and_apply_taxes(frm) {
	if (!frm.doc.custom_tax_template) {
		restore_gross_amounts(frm);
		frm.set_value("custom_total_taxes_and_charges", 0);
		calculate_totals(frm);
		return;
	}

	const request_id = (frm.expense_tax_request_id || 0) + 1;
	const tax_template = frm.doc.custom_tax_template;
	frm.expense_tax_request_id = request_id;

	frappe.call({
		method: "frappe.client.get",
		args: {
			doctype: "Purchase Taxes and Charges Template",
			name: tax_template,
		},
		callback(r) {
			if (
				request_id !== frm.expense_tax_request_id ||
				frm.doc.custom_tax_template !== tax_template
			) {
				return;
			}

			const tax_rows = ((r.message && r.message.taxes) || []).filter(
				(tax) => flt(tax.rate) && tax.account_head
			);
			if (!tax_rows.length) {
				frappe.msgprint(
					__("Tax Template {0} has no tax lines with an account and rate.", [
						tax_template,
					])
				);
				return;
			}
			const total_rate = tax_rows.reduce((sum, tax) => sum + flt(tax.rate), 0);
			if (total_rate <= -100) {
				frappe.msgprint(__("The total tax rate must be greater than -100%."));
				return;
			}

			frm.doc.expenses = (frm.doc.expenses || []).filter(
				(row) => !row.custom_is_tax_row
			);
			let total_tax = 0;
			const generated_rows = [];

			(frm.doc.expenses || []).forEach((row) => {
				let gross_amount = flt(row.custom_gross_amount);
				if (!row.custom_tax_included) {
					if (gross_amount) {
						row.amount = gross_amount;
						row.custom_gross_amount = 0;
					}
					return;
				}

				if (!gross_amount) {
					gross_amount = flt(row.amount);
					row.custom_gross_amount = gross_amount;
				}
				if (!gross_amount) {
					return;
				}

				const amount_precision = precision("amount", row);
				const net_amount = flt(
					gross_amount / (1 + total_rate / 100),
					amount_precision
				);
				let row_tax_total = 0;

				tax_rows.forEach((tax, index) => {
					const tax_amount =
						index === tax_rows.length - 1
							? flt(gross_amount - net_amount - row_tax_total, amount_precision)
							: flt(
									(net_amount * flt(tax.rate)) / 100,
									amount_precision
								);
					if (!tax_amount) {
						return;
					}

					row_tax_total += tax_amount;
					total_tax += tax_amount;
					const tax_row = {
						account_paid_to: tax.account_head,
						amount: tax_amount,
						cost_center: row.cost_center || frm.doc.default_cost_center,
						project: row.project || frm.doc.default_project,
						remarks: `${tax.description || tax.account_head} @ ${flt(tax.rate)}%`,
						custom_tax_included: 0,
						custom_is_tax_row: 1,
						custom_gross_amount: 0,
					};
					(frm.expense_accounting_dimensions || []).forEach((dimension) => {
						tax_row[dimension] = row[dimension] || frm.doc[dimension];
					});
					generated_rows.push(tax_row);
				});

				row.amount = net_amount;
			});

			generated_rows.forEach((values) => {
				const row = frm.add_child("expenses");
				Object.assign(row, values);
			});

			frm.set_value(
				"custom_total_taxes_and_charges",
				flt(total_tax, precision("custom_total_taxes_and_charges", frm.doc))
			);
			frm.refresh_field("expenses");
			calculate_totals(frm);
		},
	});
}

function restore_gross_amounts(frm) {
	(frm.doc.expenses || []).forEach((row) => {
		if (!row.custom_is_tax_row && flt(row.custom_gross_amount)) {
			row.amount = flt(row.custom_gross_amount);
			row.custom_gross_amount = 0;
		}
	});
	frm.doc.expenses = (frm.doc.expenses || []).filter((row) => !row.custom_is_tax_row);
	frm.refresh_field("expenses");
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

function update_account_balance(frm) {
	if (!frm.doc.account_paid_from || !frm.doc.company || !frm.doc.posting_date) {
		frm.set_value("account_balance_from", 0);
		return;
	}

	frappe
		.call({
			method: "expenses_manage.expenses_manage.doctype.expenses_entry.expenses_entry.get_account_balance",
			args: {
				account: frm.doc.account_paid_from,
				posting_date: frm.doc.posting_date,
				company: frm.doc.company,
			},
		})
		.then((r) => {
			if (r.message !== undefined && frm.doc.account_paid_from) {
				frm.set_value("account_balance_from", r.message);
			}
		});
}

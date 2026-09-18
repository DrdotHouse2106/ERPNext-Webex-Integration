import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	"""Legt die Custom Fields an, die für die Webex-Telefonbuch-Synchronisation
	benötigt werden. Wird idempotent bei Installation und Migration ausgeführt."""
	create_custom_fields(get_custom_fields(), update=True)
	_backfill_brand_line_doctype()


def _backfill_brand_line_doctype():
	"""Füllt erp_brand_doctype an bereits vorhandenen "Webex Brand Line"-Einträgen
	nach, die vor Einführung dieses Feldes angelegt wurden - sonst bleibt das
	zugehörige Dynamic-Link-Feld ein reines Textfeld, da es ohne Ziel-Doctype
	nicht weiß, was es durchsuchen soll. Läuft bei jeder Migration erneut
	(günstig/idempotent), damit es sich auch nach einer Änderung des
	Marke-Felds in Webex Settings selbst korrigiert."""
	if not frappe.db.table_exists("Webex Brand Line"):
		return

	settings = frappe.get_single("Webex Settings")
	brand_fieldname = settings.customer_brand_fieldname
	if not brand_fieldname:
		return

	field = frappe.get_meta("Customer").get_field(brand_fieldname)
	target_doctype = field.options if field and field.fieldtype == "Link" else None

	frappe.db.sql(
		"update `tabWebex Brand Line` set erp_brand_doctype = %s where erp_brand_doctype is null or erp_brand_doctype != %s",
		(target_doctype, target_doctype or ""),
	)


def get_custom_fields():
	webex_id_field = {
		"fieldname": "webex_contact_id",
		"label": "Webex Kontakt-ID",
		"fieldtype": "Data",
		"read_only": 1,
		"hidden": 1,
		"no_copy": 1,
		"print_hide": 1,
		"description": "Interne ID des synchronisierten Eintrags im Webex-Organisationstelefonbuch.",
	}

	return {
		"Customer": [
			dict(webex_id_field, insert_after="customer_name"),
		],
		"Contact": [
			dict(webex_id_field, insert_after="last_name"),
		],
	}


def before_uninstall():
	"""Entfernt die von dieser App angelegten Custom Fields wieder."""
	for doctype in ("Customer", "Contact"):
		frappe.db.delete(
			"Custom Field",
			{"dt": doctype, "fieldname": "webex_contact_id"},
		)

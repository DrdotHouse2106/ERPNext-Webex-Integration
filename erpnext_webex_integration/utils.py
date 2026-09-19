"""Hilfsfunktionen: Telefonnummern normalisieren und zu Kunde/Kontakt zuordnen."""

import re
from datetime import timezone

import frappe


def parse_datetime_naive(value):
	"""Wandelt einen Webex-Zeitstempel (String oder datetime, meist mit Zeitzone
	wie "Z"/"+00:00") in ein naives UTC-Datetime um.

	Wichtig: Frappes Datetime-Felder landen als MySQL DATETIME-Spalte, die keine
	Zeitzonen-Suffixe unterstuetzt. Ein direkt aus einem zeitzonenbewussten String
	geparster Wert (z.B. ueber frappe.utils.get_datetime()) fuehrt beim Speichern
	sonst zu "Incorrect datetime value: '...+00:00'" (MySQLdb.OperationalError)."""
	if not value:
		return None
	dt = frappe.utils.get_datetime(value)
	if dt and dt.tzinfo is not None:
		dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
	return dt


def normalize_phone_number(number, default_country_code="+49"):
	"""Bringt eine Telefonnummer auf ein einheitliches, vergleichbares Format.

	Beispiele: "0170 123 45 67" -> "+4917012345"; "0049 170 1234567" -> "+491701234567"
	"""
	if not number:
		return None

	cleaned = re.sub(r"[^\d+]", "", str(number))
	if not cleaned:
		return None

	if cleaned.startswith("00"):
		cleaned = "+" + cleaned[2:]
	elif not cleaned.startswith("+"):
		prefix = (default_country_code or "+49").strip()
		cleaned = prefix + cleaned.lstrip("0")

	return cleaned


def last_significant_digits(number, digits=8):
	"""Liefert die letzten n Ziffern einer Nummer - dient als tolerantes Suchmuster,
	da Vorwahl/Landeskennzahl je nach Quelle unterschiedlich formatiert sein können."""
	normalized = normalize_phone_number(number)
	if not normalized:
		return None
	only_digits = re.sub(r"\D", "", normalized)
	return only_digits[-digits:] if len(only_digits) >= digits else only_digits


def find_party_by_phone(number):
	"""Sucht Kunde/Kontakt/Lead anhand einer Telefonnummer.

	Rückgabe: dict mit den Schlüsseln customer, contact, lead (jeweils Name oder None).
	Die Suche ist bewusst tolerant (Vergleich der letzten Ziffern), da Formatierungen
	von Rufnummern zwischen Webex und ERPNext-Stammdaten variieren können.
	"""
	result = {"customer": None, "contact": None, "lead": None}
	digits = last_significant_digits(number)
	if not digits or len(digits) < 5:
		return result

	contact_name = frappe.db.sql(
		"""
		select parent from `tabContact Phone`
		where phone like %s
		order by creation desc
		limit 1
		""",
		(f"%{digits}",),
	)
	if contact_name:
		contact = contact_name[0][0]
		result["contact"] = contact
		customer = frappe.db.sql(
			"""
			select link_name from `tabDynamic Link`
			where parenttype = 'Contact' and parent = %s and link_doctype = 'Customer'
			limit 1
			""",
			(contact,),
		)
		if customer:
			result["customer"] = customer[0][0]

	if not result["customer"]:
		customer_meta = frappe.get_meta("Customer")
		phone_fields = [f for f in ("mobile_no", "phone_no") if customer_meta.has_field(f)]
		if phone_fields:
			conditions = " or ".join(f"{f} like %s" for f in phone_fields)
			customer_match = frappe.db.sql(
				f"select name from `tabCustomer` where {conditions} limit 1",
				tuple(f"%{digits}" for _ in phone_fields),
			)
			if customer_match:
				result["customer"] = customer_match[0][0]

	if not result["customer"] and not result["contact"]:
		lead_match = frappe.db.sql(
			"""
			select name from `tabLead`
			where mobile_no like %s or phone like %s
			limit 1
			""",
			(f"%{digits}", f"%{digits}"),
		)
		if lead_match:
			result["lead"] = lead_match[0][0]

	return result


def get_primary_phone(doctype, docname):
	"""Ermittelt die primäre Rufnummer eines Customer- oder Contact-Datensatzes."""
	if doctype == "Customer":
		customer_meta = frappe.get_meta("Customer")
		for fieldname in ("mobile_no", "phone_no"):
			if customer_meta.has_field(fieldname):
				value = frappe.db.get_value("Customer", docname, fieldname)
				if value:
					return value
		return None

	if doctype == "Contact":
		phone = frappe.db.get_value(
			"Contact Phone",
			{"parent": docname, "is_primary_mobile_no": 1},
			"phone",
		)
		if not phone:
			phone = frappe.db.get_value(
				"Contact Phone",
				{"parent": docname, "is_primary_phone": 1},
				"phone",
			)
		if not phone:
			phone = frappe.db.get_value("Contact Phone", {"parent": docname}, "phone")
		return phone

	frappe.throw(f"Nicht unterstützter Doctype für Rufnummernermittlung: {doctype}")


def get_all_phones(doctype, docname):
	"""Liefert alle hinterlegten Rufnummern eines Customer- oder Contact-Datensatzes
	(Mobil UND Festnetz), für die Telefonbuch-Synchronisation - im Gegensatz zu
	get_primary_phone(), das für Click-to-Call bewusst nur eine Nummer liefert.

	Rückgabe: Liste von (rufnummer, typ) mit typ in ("mobile", "work")."""
	results = []

	if doctype == "Customer":
		customer_meta = frappe.get_meta("Customer")
		for fieldname, number_type in (("mobile_no", "mobile"), ("phone_no", "work")):
			if customer_meta.has_field(fieldname):
				value = frappe.db.get_value("Customer", docname, fieldname)
				if value:
					results.append((value, number_type))

		# Zusätzlich alle Nummern verknüpfter Kontakte einsammeln (z.B. das
		# Festnetztelefon des Hauptkontakts), damit Kunde und Kontakt nicht als
		# zwei getrennte Telefonbucheinträge landen (siehe _contacts_with_phone
		# in tasks.py, das solche verknüpften Kontakte deshalb überspringt).
		linked_contacts = frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": docname},
			pluck="parent",
		)
		for contact_name in linked_contacts:
			results.extend(get_all_phones("Contact", contact_name))

		return results

	if doctype == "Contact":
		rows = frappe.get_all(
			"Contact Phone",
			filters={"parent": docname},
			fields=["phone", "is_primary_mobile_no"],
		)
		for row in rows:
			if row.phone:
				results.append((row.phone, "mobile" if row.is_primary_mobile_no else "work"))
		return results

	frappe.throw(f"Nicht unterstützter Doctype für Rufnummernermittlung: {doctype}")

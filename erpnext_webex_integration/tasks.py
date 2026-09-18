"""Geplante Aufgaben: Anrufprotokoll abrufen, Telefonbuch synchronisieren."""

import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from erpnext_webex_integration import utils
from erpnext_webex_integration.webex_client import WebexAPIError, WebexClient

FREQUENCY_TO_TIMEDELTA = {
	"Stündlich": {"hours": 1},
	"Täglich": {"days": 1},
}

WEBEX_TOKEN_URL = "https://webexapis.com/v1/access_token"


# ----------------------------------------------------------------------
# Automatische Erneuerung des OAuth-Zugriffstokens
# ----------------------------------------------------------------------
def refresh_access_token():
	"""Erneuert das Zugriffstoken rechtzeitig vor Ablauf (Zugriffstoken: 14 Tage,
	Refresh-Token: 90 Tage). Wird taeglich per Scheduler ausgefuehrt."""
	import requests

	settings = frappe.get_single("Webex Settings")
	if not settings.client_id or not settings.get_password("refresh_token", raise_exception=False):
		return

	if settings.token_expires_on and get_datetime(settings.token_expires_on) > add_to_date(
		now_datetime(), days=2
	):
		return  # noch nicht faellig

	response = requests.post(
		WEBEX_TOKEN_URL,
		data={
			"grant_type": "refresh_token",
			"client_id": settings.client_id,
			"client_secret": settings.get_password("client_secret"),
			"refresh_token": settings.get_password("refresh_token"),
		},
		timeout=20,
	)
	if response.status_code >= 400:
		frappe.log_error(
			title="Webex Token-Erneuerung fehlgeschlagen",
			message=f"{response.status_code}: {response.text[:500]}",
		)
		return

	token_data = response.json()
	settings.access_token = token_data.get("access_token")
	if token_data.get("refresh_token"):
		settings.refresh_token = token_data.get("refresh_token")
	settings.token_expires_on = add_to_date(now_datetime(), seconds=token_data.get("expires_in", 1209600))
	settings.refresh_token_expires_on = add_to_date(
		now_datetime(), seconds=token_data.get("refresh_token_expires_in", 90 * 24 * 3600)
	)
	settings.save(ignore_permissions=True)
	frappe.db.commit()


# ----------------------------------------------------------------------
# Anrufprotokoll (CDR)
# ----------------------------------------------------------------------
def pull_call_history():
	settings = frappe.get_single("Webex Settings")
	if not settings.enabled or not settings.auto_pull_call_history:
		return

	lookback_minutes = settings.call_history_lookback_minutes or 60
	end_time = add_to_date(now_datetime(), minutes=-5)  # Webex verlangt: Reportzeit >= 5 Min. alt
	start_time = get_datetime(settings.last_call_history_sync) if settings.last_call_history_sync else None
	if not start_time or start_time >= end_time:
		start_time = add_to_date(end_time, minutes=-lookback_minutes)

	client = WebexClient(settings=settings)
	try:
		records = client.get_call_history(
			_to_webex_timestamp(start_time), _to_webex_timestamp(end_time)
		)
	except WebexAPIError as exc:
		frappe.log_error(title="Webex Anrufprotokoll-Abruf fehlgeschlagen", message=str(exc))
		return

	for record in records:
		_create_call_log_from_cdr(record, settings)

	frappe.db.set_single_value("Webex Settings", "last_call_history_sync", end_time)
	frappe.db.commit()


def _to_webex_timestamp(dt):
	return get_datetime(dt).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _create_call_log_from_cdr(record, settings):
	# Feldnamen der CDR-Antwort koennen je Mandant/API-Version variieren - defensiv auslesen.
	# "Correlation ID" verbindet laut Webex-CDR-Doku alle Anruf-Schenkel desselben
	# echten Anrufs - das ist dasselbe Konzept wie callSessionId im Webhook und
	# wird daher im selben Feld (call_session_id) abgelegt, um Webhook- und
	# CDR-Datensaetze desselben Anrufs zusammenzufuehren statt zu duplizieren.
	call_id = record.get("Call ID") or record.get("callId")
	call_session_id = record.get("Correlation ID") or record.get("correlationId")
	if not call_id and not call_session_id:
		return

	from_number = record.get("Calling number") or record.get("callingNumber")
	to_number = record.get("Called number") or record.get("calledNumber")
	direction_raw = (record.get("Direction") or record.get("direction") or "").lower()
	duration = record.get("Duration") or record.get("duration") or 0
	start_time = record.get("Start time") or record.get("startTime")
	answer_time = record.get("Answer time") or record.get("answerTime")

	existing_name = None
	if call_session_id:
		existing_name = frappe.db.get_value("Webex Call Log", {"call_session_id": call_session_id})
	if not existing_name and call_id:
		existing_name = frappe.db.get_value("Webex Call Log", {"call_id": call_id})
	if not existing_name and start_time:
		lookup_number = to_number if "incoming" in direction_raw else from_number
		existing_name = _find_recent_call_log_by_number_and_time(lookup_number, get_datetime(start_time))

	is_new = not existing_name
	call_log = (
		frappe.get_doc("Webex Call Log", existing_name) if existing_name else frappe.new_doc("Webex Call Log")
	)

	if call_id:
		call_log.call_id = call_id
	if call_session_id:
		call_log.call_session_id = call_session_id
	if is_new:
		call_log.source = "CDR-Abruf"
		call_log.direction = "Eingehend" if "incoming" in direction_raw else "Ausgehend"
	if answer_time and call_log.status in (None, "", "Klingelt", "Beendet"):
		call_log.status = "Verbunden"
	elif not answer_time and call_log.status in (None, "", "Klingelt"):
		call_log.status = "Verpasst"
	call_log.from_number = call_log.from_number or from_number
	call_log.to_number = call_log.to_number or to_number
	if start_time and not call_log.start_time:
		call_log.start_time = get_datetime(start_time)
	if not call_log.duration_seconds:
		try:
			call_log.duration_seconds = int(duration)
		except (TypeError, ValueError):
			pass
	call_log.raw_payload = json.dumps(record, indent=2, default=str)

	lookup_number = to_number if call_log.direction == "Eingehend" else from_number
	match = utils.find_party_by_phone(lookup_number) if lookup_number else None
	if match:
		call_log.customer = call_log.customer or match.get("customer")
		call_log.contact = call_log.contact or match.get("contact")
		call_log.lead = call_log.lead or match.get("lead")

	if is_new:
		call_log.insert(ignore_permissions=True)
	else:
		call_log.save(ignore_permissions=True)


def _find_recent_call_log_by_number_and_time(number, start_time, tolerance_minutes=3):
	"""Fallback-Abgleich, falls Webhook und CDR unterschiedliche IDs fuer denselben
	Anruf verwenden: gleiche Rufnummer der Gegenseite und Anrufbeginn innerhalb
	eines kurzen Zeitfensters gelten als derselbe Anruf."""
	digits = utils.last_significant_digits(number)
	if not digits:
		return None

	candidates = frappe.get_all(
		"Webex Call Log",
		filters=[
			["start_time", ">=", add_to_date(start_time, minutes=-tolerance_minutes)],
			["start_time", "<=", add_to_date(start_time, minutes=tolerance_minutes)],
		],
		or_filters=[["from_number", "like", f"%{digits}"], ["to_number", "like", f"%{digits}"]],
		pluck="name",
		limit=1,
	)
	return candidates[0] if candidates else None


# ----------------------------------------------------------------------
# Telefonbuch-Synchronisation
# ----------------------------------------------------------------------
def sync_phonebook(force=False):
	settings = frappe.get_single("Webex Settings")
	if not settings.enabled or not settings.auto_sync_phonebook:
		return

	if not force and not _phonebook_sync_due(settings):
		return

	client = WebexClient(settings=settings)

	if settings.sync_customers:
		for customer_name in _customers_with_phone():
			try:
				sync_single_customer(customer_name, client=client, settings=settings)
			except WebexAPIError as exc:
				frappe.log_error(
					title="Webex Telefonbuch-Sync (Kunde) fehlgeschlagen",
					message=f"{customer_name}: {exc}",
				)

	if settings.sync_contacts:
		for contact_name in _contacts_with_phone():
			try:
				sync_single_contact(contact_name, client=client, settings=settings)
			except WebexAPIError as exc:
				frappe.log_error(
					title="Webex Telefonbuch-Sync (Kontakt) fehlgeschlagen",
					message=f"{contact_name}: {exc}",
				)

	frappe.db.set_single_value("Webex Settings", "last_phonebook_sync", now_datetime())
	frappe.db.commit()


def _phonebook_sync_due(settings):
	if not settings.last_phonebook_sync:
		return True
	delta = FREQUENCY_TO_TIMEDELTA.get(settings.phonebook_sync_frequency, {"days": 1})
	next_due = add_to_date(get_datetime(settings.last_phonebook_sync), **delta)
	return now_datetime() >= next_due


def _customers_with_phone():
	meta = frappe.get_meta("Customer")
	phone_fields = [f for f in ("mobile_no", "phone_no") if meta.has_field(f)]
	if not phone_fields:
		return []
	conditions = " or ".join(f"{f} is not null and {f} != ''" for f in phone_fields)
	return frappe.db.sql_list(f"select name from `tabCustomer` where {conditions} and disabled = 0")


def _contacts_with_phone():
	return frappe.db.sql_list(
		"select distinct parent from `tabContact Phone` where phone is not null and phone != ''"
	)


def sync_single_customer(customer_name, client=None, settings=None):
	settings = settings or frappe.get_single("Webex Settings")
	client = client or WebexClient(settings=settings)
	customer = frappe.get_doc("Customer", customer_name)

	phone = utils.get_primary_phone("Customer", customer_name)
	normalized = utils.normalize_phone_number(phone, settings.default_country_code)
	if not normalized:
		return

	display_name = (settings.contact_display_format or "{customer_name} ({customer_id})").format(
		customer_name=customer.customer_name,
		customer_id=customer.name,
		first_name=customer.customer_name,
		last_name="",
		brand_abbr=_get_brand_abbr(customer_name, settings),
	)
	payload = _build_organization_contact_payload(display_name, normalized)

	if customer.get("webex_contact_id"):
		result = client.update_organization_contact(customer.webex_contact_id, payload)
	else:
		result = client.create_organization_contact(payload)
		contact_id = result.get("id")
		if contact_id:
			frappe.db.set_value("Customer", customer_name, "webex_contact_id", contact_id)


def sync_single_contact(contact_name, client=None, settings=None):
	settings = settings or frappe.get_single("Webex Settings")
	client = client or WebexClient(settings=settings)
	contact = frappe.get_doc("Contact", contact_name)

	phone = utils.get_primary_phone("Contact", contact_name)
	normalized = utils.normalize_phone_number(phone, settings.default_country_code)
	if not normalized:
		return

	customer_name = frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": contact_name, "link_doctype": "Customer"},
		"link_name",
	)
	full_name = " ".join(filter(None, [contact.first_name, contact.last_name])) or contact.name
	display_name = (settings.contact_display_format or "{customer_name} ({customer_id})").format(
		customer_name=customer_name or full_name,
		customer_id=customer_name or contact.name,
		first_name=contact.first_name or "",
		last_name=contact.last_name or "",
		brand_abbr=_get_brand_abbr(customer_name, settings) if customer_name else "",
	)
	payload = _build_organization_contact_payload(
		display_name, normalized, first_name=contact.first_name, last_name=contact.last_name
	)

	if contact.get("webex_contact_id"):
		client.update_organization_contact(contact.webex_contact_id, payload)
	else:
		result = client.create_organization_contact(payload)
		contact_id = result.get("id")
		if contact_id:
			frappe.db.set_value("Contact", contact_name, "webex_contact_id", contact_id)


def _get_brand_abbr(customer_name, settings):
	"""Liefert das Kürzel der Marke des Kunden (z.B. "GFV"), falls konfiguriert -
	sonst einen leeren String, damit {brand_abbr} im Anzeigeformat immer nutzbar ist."""
	brand_fieldname = settings.customer_brand_fieldname
	if not brand_fieldname or not customer_name:
		return ""

	brand = frappe.db.get_value("Customer", customer_name, brand_fieldname)
	if not brand:
		return ""

	return frappe.db.get_value("Webex Brand Line", {"brand": brand}, "abbreviation") or ""


def _build_organization_contact_payload(display_name, phone_number, first_name=None, last_name=None):
	# Schema orientiert sich an der SCIM-basierten Organization-Contacts-API von Webex.
	# Sollte das Feldschema im eigenen Tenant abweichen, hier anpassen (siehe README).
	return {
		"displayName": display_name,
		"name": {
			"givenName": first_name or display_name,
			"familyName": last_name or "",
		},
		"phoneNumbers": [{"value": phone_number, "type": "work", "primary": True}],
	}


# ----------------------------------------------------------------------
# Inkrementelle Synchronisation bei Aenderung von Kunde/Kontakt
# ----------------------------------------------------------------------
def on_customer_update(doc, method=None):
	settings = frappe.get_cached_doc("Webex Settings")
	if not settings.enabled or not settings.auto_sync_phonebook or not settings.sync_customers:
		return
	frappe.enqueue(
		"erpnext_webex_integration.tasks.sync_single_customer",
		queue="short",
		job_id=f"webex-sync-customer-{doc.name}",
		deduplicate=True,
		customer_name=doc.name,
	)


def on_contact_update(doc, method=None):
	settings = frappe.get_cached_doc("Webex Settings")
	if not settings.enabled or not settings.auto_sync_phonebook or not settings.sync_contacts:
		return
	frappe.enqueue(
		"erpnext_webex_integration.tasks.sync_single_contact",
		queue="short",
		job_id=f"webex-sync-contact-{doc.name}",
		deduplicate=True,
		contact_name=doc.name,
	)

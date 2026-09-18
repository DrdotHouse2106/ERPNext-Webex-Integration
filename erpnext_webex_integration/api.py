"""Whitelisted API-Endpunkte: Webhook-Empfang, Click-to-Call, OAuth-Anbindung, manuelle Trigger."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from erpnext_webex_integration import utils
from erpnext_webex_integration.webex_client import WebexAPIError, WebexClient

WEBEX_AUTHORIZE_URL = "https://webexapis.com/v1/authorize"
WEBEX_TOKEN_URL = "https://webexapis.com/v1/access_token"
OAUTH_SCOPES = (
	"spark:calls_write spark:calls_read spark-admin:calling_cdr_read "
	"Identity:contact spark:webhooks_write spark:webhooks_read "
	"spark-admin:people_read spark-admin:people_write "
	"spark-admin:telephony_config_read"
)

EVENT_TYPE_TO_STATUS = {
	"created": "Klingelt",
	"alerting": "Klingelt",
	"answered": "Verbunden",
	"resumed": "Verbunden",
	"held": "Verbunden",
	"disconnected": "Beendet",
	"deleted": "Beendet",
}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webex_webhook():
	"""Empfaengt Webex-Webhook-Ereignisse (Ressource telephony_calls) und
	dokumentiert sie als Webex Call Log. Muss immer schnell antworten, sonst
	deaktiviert Webex den Webhook nach mehreren Fehlversuchen."""
	raw_body = frappe.request.get_data()

	if not _verify_signature(raw_body):
		frappe.local.response.http_status_code = 401
		return {"error": "invalid signature"}

	try:
		payload = json.loads(raw_body or b"{}")
	except ValueError:
		frappe.local.response.http_status_code = 400
		return {"error": "invalid payload"}

	try:
		_handle_call_webhook_payload(payload)
	except Exception:
		frappe.log_error(
			title="Webex Webhook Verarbeitung fehlgeschlagen",
			message=frappe.get_traceback(),
		)

	return {"status": "ok"}


def _verify_signature(raw_body):
	settings = frappe.get_single("Webex Settings")
	secret = settings.get_password("webhook_secret", raise_exception=False)
	if not secret:
		# Kein Geheimnis konfiguriert: Signaturpruefung wird uebersprungen.
		return True

	signature = frappe.get_request_header("X-Spark-Signature")
	if not signature:
		return False

	expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha1).hexdigest()
	return hmac.compare_digest(expected, signature)


def _handle_call_webhook_payload(payload):
	if payload.get("resource") != "telephony_calls":
		return

	data = payload.get("data") or {}
	call_id = data.get("callId") or payload.get("id")
	if not call_id:
		return

	event_type = (data.get("eventType") or payload.get("event") or "").lower()
	status = EVENT_TYPE_TO_STATUS.get(event_type, "Beendet")

	existing_name = frappe.db.get_value("Webex Call Log", {"call_id": call_id, "source": "Webhook"})
	call_log = (
		frappe.get_doc("Webex Call Log", existing_name)
		if existing_name
		else frappe.new_doc("Webex Call Log")
	)

	call_log.call_id = call_id
	call_log.source = "Webhook"
	call_log.status = status
	call_log.raw_payload = json.dumps(payload, indent=2)

	if event_type in ("created", "alerting") and not call_log.start_time:
		call_log.start_time = frappe.utils.now_datetime()
	if event_type in ("disconnected", "deleted"):
		call_log.end_time = frappe.utils.now_datetime()

	_enrich_from_call_details(call_log, call_id)

	call_log.save(ignore_permissions=True)
	frappe.db.commit()  # notwendig, da Webhook-Aufrufe ausserhalb einer Request-Transaktion laufen


def _enrich_from_call_details(call_log, call_id):
	"""Versucht, Rufnummern ueber die Call-Details-API zu ergaenzen. Schlaegt dies fehl
	(z.B. weil das Service-Token keinen Zugriff auf die Anrufe dieses Benutzers hat),
	bleibt der Datensatz trotzdem mit den bisher bekannten Daten erhalten."""
	settings = frappe.get_single("Webex Settings")
	if not settings.enabled:
		return

	try:
		client = WebexClient(settings=settings)
		details = client.get_call_details(call_id)
	except WebexAPIError:
		return

	from_number = details.get("callingNumber") or details.get("callingParty", {}).get("number")
	to_number = details.get("calledNumber") or details.get("calledParty", {}).get("number")
	direction = details.get("direction")

	if from_number:
		call_log.from_number = from_number
	if to_number:
		call_log.to_number = to_number
	if direction:
		call_log.direction = "Eingehend" if str(direction).lower() == "incoming" else "Ausgehend"

	lookup_number = to_number if call_log.direction == "Eingehend" else from_number
	match = utils.find_party_by_phone(lookup_number) if lookup_number else None
	if match:
		call_log.customer = call_log.customer or match.get("customer")
		call_log.contact = call_log.contact or match.get("contact")
		call_log.lead = call_log.lead or match.get("lead")


@frappe.whitelist()
def click_to_call(doctype, docname):
	"""Wird vom Button 'Anruf starten' auf Kunde/Kontakt aufgerufen."""
	settings = frappe.get_single("Webex Settings")
	if not settings.enabled or not settings.enable_click_to_call:
		frappe.throw("Click-to-Call ist in den Webex-Einstellungen nicht aktiviert.")

	phone = utils.get_primary_phone(doctype, docname)
	if not phone:
		frappe.throw("Fuer diesen Datensatz ist keine Rufnummer hinterlegt.")

	normalized = utils.normalize_phone_number(phone, settings.default_country_code)
	_log_manual_call_start(doctype, docname, normalized)

	if settings.click_to_call_mode == "Webex Call-Control-API":
		try:
			client = WebexClient(settings=settings)
			_apply_brand_caller_id(client, settings, doctype, docname)
			client.dial(normalized)
			return {"mode": "api"}
		except WebexAPIError as exc:
			frappe.log_error(title="Webex Click-to-Call fehlgeschlagen", message=str(exc))
			# Fallback: lokale Anwendung per tel:-Link starten lassen.

	return {"mode": "tel", "tel_link": f"tel:{normalized}"}


def _apply_brand_caller_id(client, settings, doctype, docname):
	"""Setzt vor dem Anruf die zur Kunden-Marke passende Anrufer-ID, falls konfiguriert.

	Schlaegt dies fehl (z.B. weil keine Zuordnung existiert oder die Person nicht
	aufgeloest werden kann), wird der Anruf trotzdem ganz normal gestartet - nur
	eben mit der aktuell in Webex eingestellten Standard-Anrufer-ID."""
	brand_fieldname = settings.customer_brand_fieldname
	if not brand_fieldname:
		return

	customer_name = docname if doctype == "Customer" else _get_linked_customer(docname)
	if not customer_name:
		return

	brand = frappe.db.get_value("Customer", customer_name, brand_fieldname)
	if not brand:
		return

	phone_number = frappe.db.get_value("Webex Brand Line", {"brand": brand}, "phone_number")
	if not phone_number:
		return

	try:
		person_id = _get_cached_person_id(client, settings, frappe.session.user)
		if person_id:
			client.set_caller_id(person_id, phone_number)
	except WebexAPIError as exc:
		frappe.log_error(title="Webex Anrufer-ID setzen fehlgeschlagen", message=str(exc))


def _get_linked_customer(contact_name):
	return frappe.db.get_value(
		"Dynamic Link",
		{"parenttype": "Contact", "parent": contact_name, "link_doctype": "Customer"},
		"link_name",
	)


def _get_cached_person_id(client, settings, frappe_user):
	cache_key = f"webex_person_id:{frappe_user}"
	person_id = frappe.cache().get_value(cache_key)
	if person_id:
		return person_id

	user_email = frappe.db.get_value("User", frappe_user, "email") or frappe_user
	person_id = client.find_person_id_by_email(user_email, org_id=settings.org_id)
	if person_id:
		frappe.cache().set_value(cache_key, person_id, expires_in_sec=3600)
	return person_id


def _log_manual_call_start(doctype, docname, to_number):
	call_log = frappe.new_doc("Webex Call Log")
	call_log.source = "Manuell"
	call_log.direction = "Ausgehend"
	call_log.status = "Klingelt"
	call_log.to_number = to_number
	call_log.start_time = frappe.utils.now_datetime()
	call_log.user = frappe.session.user

	if doctype == "Customer":
		call_log.customer = docname
	elif doctype == "Contact":
		call_log.contact = docname
		customer = frappe.db.get_value(
			"Dynamic Link",
			{"parenttype": "Contact", "parent": docname, "link_doctype": "Customer"},
			"link_name",
		)
		if customer:
			call_log.customer = customer

	call_log.insert(ignore_permissions=True)
	return call_log.name


@frappe.whitelist()
def sync_phonebook_now():
	frappe.only_for("System Manager")
	from erpnext_webex_integration.tasks import sync_phonebook

	sync_phonebook(force=True)
	return {"status": "ok"}


@frappe.whitelist()
def pull_call_history_now():
	frappe.only_for("System Manager")
	from erpnext_webex_integration.tasks import pull_call_history

	pull_call_history()
	return {"status": "ok"}


@frappe.whitelist()
def import_brand_lines_from_webex():
	"""Liest alle Rufnummern der Organisation und legt/aktualisiert daraus
	"Webex Brand Line"-Einträge an (Marke = Location-Name, Rufnummer = zugehörige Nummer).

	Das genaue Feldschema der Webex-Antwort (phoneNumber/location/mainNumber) ist aus der
	API-Dokumentation abgeleitet, aber nicht gegen jeden Tenant verifiziert - deshalb wird
	die vollständige Rohliste zusätzlich zurückgegeben, damit Abweichungen sofort auffallen."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	client = WebexClient(settings=settings)

	try:
		numbers = client.list_phone_numbers(org_id=settings.org_id)
	except WebexAPIError as exc:
		frappe.throw(str(exc))

	created, updated, skipped = [], [], []
	for entry in numbers:
		location = entry.get("location") or {}
		brand = location.get("name")
		phone_number = entry.get("phoneNumber") or entry.get("phoneNumbers")
		if not brand or not phone_number:
			skipped.append(entry)
			continue

		if entry.get("mainNumber") is False and frappe.db.exists("Webex Brand Line", {"brand": brand}):
			# Falls eine Location mehrere Nummern hat, bevorzugen wir die Hauptnummer
			# und lassen bereits vorhandene Zuordnungen fuer diese Marke unangetastet.
			continue

		if frappe.db.exists("Webex Brand Line", brand):
			frappe.db.set_value("Webex Brand Line", brand, "phone_number", phone_number)
			updated.append(brand)
		else:
			frappe.get_doc(
				{"doctype": "Webex Brand Line", "brand": brand, "phone_number": phone_number}
			).insert(ignore_permissions=True)
			created.append(brand)

	frappe.db.commit()
	return {
		"created": created,
		"updated": updated,
		"skipped_count": len(skipped),
		"raw_sample": numbers[:3],
	}


@frappe.whitelist()
def debug_caller_id_settings():
	"""Zeigt die rohen Anrufer-ID-Einstellungen des aktuell verbundenen Webex-Benutzers an.
	Dient dazu, das tatsaechliche JSON-Schema zu verifizieren, falls set_caller_id()
	beim Click-to-Call mit "falsche Felder" fehlschlagen sollte."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	client = WebexClient(settings=settings)
	person_id = _get_cached_person_id(client, settings, frappe.session.user)
	if not person_id:
		frappe.throw(
			"Konnte keine Webex-Person-ID zu deiner E-Mail-Adresse finden. "
			"Stimmt deine ERPNext-Benutzer-E-Mail mit deiner Webex-E-Mail überein?"
		)
	try:
		return client.get_caller_id_settings(person_id)
	except WebexAPIError as exc:
		frappe.throw(str(exc))


# ----------------------------------------------------------------------
# OAuth-Anbindung: ERPNext übernimmt den kompletten Autorisierungs-Flow,
# damit kein manuelles Kopieren von Codes/Token nötig ist.
# ----------------------------------------------------------------------
@frappe.whitelist()
def webex_oauth_connect():
	"""Leitet den Benutzer zur Webex-Anmeldung/Freigabe weiter."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	if not settings.client_id or not settings.get_password("client_secret", raise_exception=False):
		frappe.throw(
			"Bitte zuerst Client ID und Client Secret in den Webex-Einstellungen eintragen und speichern."
		)

	state = frappe.generate_hash(length=20)
	frappe.cache().set_value(f"webex_oauth_state:{frappe.session.user}", state, expires_in_sec=600)

	params = {
		"client_id": settings.client_id,
		"response_type": "code",
		"redirect_uri": settings.get_oauth_redirect_uri(),
		"scope": OAUTH_SCOPES,
		"state": state,
	}
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = f"{WEBEX_AUTHORIZE_URL}?{urlencode(params)}"


@frappe.whitelist()
def webex_oauth_callback(code=None, state=None, error=None, **kwargs):
	"""Nimmt die Weiterleitung von Webex entgegen und tauscht den Code gegen Tokens."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")

	if error:
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = _settings_url(f"webex_error={error}")
		return

	expected_state = frappe.cache().get_value(f"webex_oauth_state:{frappe.session.user}")
	if not code or not state or state != expected_state:
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = _settings_url("webex_error=invalid_state")
		return

	frappe.cache().delete_value(f"webex_oauth_state:{frappe.session.user}")

	try:
		token_data = _exchange_code_for_tokens(settings, code)
	except WebexAPIError:
		frappe.log_error(title="Webex OAuth Token-Tausch fehlgeschlagen", message=frappe.get_traceback())
		frappe.local.response["type"] = "redirect"
		frappe.local.response["location"] = _settings_url("webex_error=token_exchange_failed")
		return

	_store_tokens(settings, token_data)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = _settings_url("webex_connected=1")


def _settings_url(query):
	return f"{frappe.utils.get_url()}/app/webex-settings?{query}"


def _exchange_code_for_tokens(settings, code):
	payload = {
		"grant_type": "authorization_code",
		"client_id": settings.client_id,
		"client_secret": settings.get_password("client_secret"),
		"code": code,
		"redirect_uri": settings.get_oauth_redirect_uri(),
	}
	response = requests.post(WEBEX_TOKEN_URL, data=payload, timeout=20)
	if response.status_code >= 400:
		raise WebexAPIError(f"Token-Tausch fehlgeschlagen ({response.status_code}): {response.text[:500]}")
	return response.json()


def _store_tokens(settings, token_data):
	settings.access_token = token_data.get("access_token")
	settings.refresh_token = token_data.get("refresh_token")
	settings.token_expires_on = add_to_date(now_datetime(), seconds=token_data.get("expires_in", 1209600))
	settings.refresh_token_expires_on = add_to_date(
		now_datetime(), seconds=token_data.get("refresh_token_expires_in", 90 * 24 * 3600)
	)
	settings.save(ignore_permissions=True)
	frappe.db.commit()

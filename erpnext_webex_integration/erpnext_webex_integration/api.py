"""Whitelisted API-Endpunkte: Webhook-Empfang, Click-to-Call, manuelle Trigger."""

import hashlib
import hmac
import json

import frappe

from erpnext_webex_integration import utils
from erpnext_webex_integration.webex_client import WebexAPIError, WebexClient

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
			client.dial(normalized)
			return {"mode": "api"}
		except WebexAPIError as exc:
			frappe.log_error(title="Webex Click-to-Call fehlgeschlagen", message=str(exc))
			# Fallback: lokale Anwendung per tel:-Link starten lassen.

	return {"mode": "tel", "tel_link": f"tel:{normalized}"}


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

"""Whitelisted API-Endpunkte: Webhook-Empfang, Click-to-Call, OAuth-Anbindung, manuelle Trigger."""

import hashlib
import hmac
import json
from urllib.parse import urlencode

import frappe
import requests
from frappe.utils import add_to_date, get_datetime, now_datetime

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
	call_session_id = data.get("callSessionId")
	if not call_id:
		return

	event_type = (data.get("eventType") or payload.get("event") or "").lower()
	status = EVENT_TYPE_TO_STATUS.get(event_type, "Beendet")

	# callSessionId identifiziert den gesamten echten Anruf (ueber alle beteiligten
	# Personen/Geraete hinweg), waehrend callId nur einen einzelnen Anruf-Schenkel
	# meint. Wird z.B. eine Hunt Group von mehreren Kollegen gleichzeitig geklingelt,
	# feuert der Webhook pro Person - ueber callSessionId landet das trotzdem in
	# einem einzigen Datensatz statt in mehreren Duplikaten.
	existing_name = None
	if call_session_id:
		existing_name = frappe.db.get_value("Webex Call Log", {"call_session_id": call_session_id})
	if not existing_name:
		existing_name = frappe.db.get_value("Webex Call Log", {"call_id": call_id})

	is_new = not existing_name
	call_log = frappe.get_doc("Webex Call Log", existing_name) if existing_name else frappe.new_doc("Webex Call Log")

	previous_status = call_log.status if not is_new else None

	call_log.call_id = call_id
	call_log.call_session_id = call_session_id or call_log.call_session_id
	if is_new:
		call_log.source = "Webhook"
	call_log.status = status
	call_log.raw_payload = json.dumps(payload, indent=2)

	# "personality" gibt an, ob dieser Anruf-Schenkel bei uns ankam (terminator)
	# oder von uns ausging (originator) - zuverlässiger als ein Ratespiel anhand
	# einer separaten API-Abfrage.
	personality = (data.get("personality") or "").lower()
	if personality == "terminator":
		call_log.direction = "Eingehend"
	elif personality == "originator":
		call_log.direction = "Ausgehend"

	# "remoteParty.number" ist im Webhook-Payload direkt enthalten - das ist die
	# Nummer der Gegenseite (bei uns eingehend: die Kundennummer) und muss nicht
	# erst ueber eine weitere API-Abfrage ermittelt werden.
	remote_number = (data.get("remoteParty") or {}).get("number")
	if remote_number:
		if call_log.direction == "Ausgehend":
			call_log.to_number = call_log.to_number or remote_number
		else:
			call_log.from_number = call_log.from_number or remote_number

	if data.get("created") and not call_log.start_time:
		call_log.start_time = utils.parse_datetime_naive(data["created"])
	if data.get("disconnected"):
		call_log.end_time = utils.parse_datetime_naive(data["disconnected"])

	duration_start = data.get("answered") or data.get("created")
	if duration_start and data.get("disconnected"):
		call_log.duration_seconds = int(
			(
				utils.parse_datetime_naive(data["disconnected"])
				- utils.parse_datetime_naive(duration_start)
			).total_seconds()
		)

	# Bestes-Bemuehen-Anreicherung ueber die Call-Details-API (kann fehlschlagen,
	# z.B. ohne Zugriff auf die Anrufe dieses Benutzers - dann bleibt es bei den
	# bereits aus dem Webhook-Payload bekannten Daten).
	_enrich_from_call_details(call_log, call_id)

	lookup_number = remote_number or (
		call_log.to_number if call_log.direction == "Eingehend" else call_log.from_number
	)
	if lookup_number:
		match = utils.find_party_by_phone(lookup_number)
		if match:
			call_log.customer = call_log.customer or match.get("customer")
			call_log.contact = call_log.contact or match.get("contact")
			call_log.lead = call_log.lead or match.get("lead")

	call_log.save(ignore_permissions=True)
	frappe.db.commit()  # notwendig, da Webhook-Aufrufe ausserhalb einer Request-Transaktion laufen

	# Screen-Pop: nur beim Uebergang zu "Klingelt" ausloesen (nicht bei jedem
	# weiteren Ereignis desselben Anrufs), damit nicht mehrfach benachrichtigt wird.
	if status == "Klingelt" and previous_status != "Klingelt":
		try:
			_notify_incoming_call(payload, call_log)
		except Exception:
			frappe.log_error(title="Webex Screen-Pop fehlgeschlagen", message=frappe.get_traceback())


def _notify_incoming_call(payload, call_log):
	"""Benachrichtigt per Frappe-Realtime (Desk-Popup) den Benutzer, dem der Anruf
	zugestellt wird - ermittelt ueber die "actorId" im Webhook-Payload (die Webex-
	Person, bei der der Anruf ankommt), umgerechnet auf den passenden ERPNext-
	Benutzer per E-Mail-Abgleich. Faellt beim ersten mit dieser App verbundenen
	Benutzer (aktuell z.B. Marcel) natuerlich zusammen; sobald weitere Kollegen
	ihre eigene Webex-Verbindung freigeben (Roadmap), greift dieselbe Logik ohne
	Codeaenderung auch fuer sie, da actorId je Anruf ohnehin die richtige Person nennt."""
	actor_id = payload.get("actorId")
	if not actor_id:
		return

	frappe_user = _get_frappe_user_for_webex_person(actor_id)
	if not frappe_user:
		return

	if call_log.customer:
		customer_name = frappe.db.get_value("Customer", call_log.customer, "customer_name") or call_log.customer
		subtitle = f"Kunde: {customer_name} ({call_log.customer})"
	elif call_log.contact:
		subtitle = f"Kontakt: {call_log.contact}"
	elif call_log.lead:
		subtitle = f"Lead: {call_log.lead}"
	else:
		subtitle = "Unbekannter Anrufer"

	frappe.publish_realtime(
		"webex_incoming_call",
		{
			"from_number": call_log.from_number,
			"subtitle": subtitle,
			"customer": call_log.customer,
			"contact": call_log.contact,
			"lead": call_log.lead,
			"call_log": call_log.name,
		},
		user=frappe_user,
	)


def _get_frappe_user_for_webex_person(actor_id):
	"""Umkehrung von _get_cached_person_id(): ermittelt zu einer Webex-Person-ID
	die E-Mail-Adresse und darueber den passenden ERPNext-Benutzer."""
	cache_key = f"webex_person_email:{actor_id}"
	email = frappe.cache().get_value(cache_key)
	if not email:
		settings = frappe.get_single("Webex Settings")
		try:
			client = WebexClient(settings=settings)
			person = client.get_person(actor_id)
		except WebexAPIError:
			return None
		emails = person.get("emails") or []
		email = emails[0] if emails else None
		if email:
			frappe.cache().set_value(cache_key, email, expires_in_sec=3600)

	if not email:
		return None
	return frappe.db.get_value("User", {"email": email})


def _enrich_from_call_details(call_log, call_id):
	"""Versucht, fehlende Rufnummern/Richtung ueber die Call-Details-API zu ergaenzen.
	Ueberschreibt nie bereits aus dem Webhook-Payload bekannte Werte, sondern
	fuellt nur Luecken. Schlaegt die Abfrage fehl, bleibt der Datensatz trotzdem
	mit den bisher bekannten Daten erhalten."""
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
		call_log.from_number = call_log.from_number or from_number
	if to_number:
		call_log.to_number = call_log.to_number or to_number
	if direction and not call_log.direction:
		call_log.direction = "Eingehend" if str(direction).lower() == "incoming" else "Ausgehend"


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

	brand_value = frappe.db.get_value("Customer", customer_name, brand_fieldname)
	if not brand_value:
		return

	phone_number = frappe.db.get_value("Webex Brand Line", {"erp_brand_value": brand_value}, "phone_number")
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
	"""Stoesst den Telefonbuch-Sync als Hintergrundjob an, statt synchron im
	Web-Request zu laufen - bei vielen Kunden/Kontakten (inkl. Drosselung gegen
	das Webex-Rate-Limit) ueberschreitet das sonst den Request-Timeout
	("Zeitüberschreitung der Anfrage"). Das Ergebnis kommt per Realtime-Event
	("webex_phonebook_sync_done") zurueck, sobald der Job fertig ist."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	if not settings.enabled:
		return {"status": "skipped", "reason": "Integration ist nicht aktiviert."}

	frappe.enqueue(
		"erpnext_webex_integration.tasks.run_phonebook_sync_background",
		queue="long",
		job_id=f"webex-manual-phonebook-sync-{frappe.session.user}",
		deduplicate=True,
		user=frappe.session.user,
	)
	return {"status": "queued"}


@frappe.whitelist()
def preview_phonebook_sync():
	"""Berechnet, was ein echter Sync tun würde (anlegen/aktualisieren, mit welchem
	Anzeigenamen/welcher Nummer) - ohne Webex überhaupt zu kontaktieren."""
	frappe.only_for("System Manager")
	from erpnext_webex_integration.tasks import (
		_contacts_with_phone,
		_customers_with_phone,
		build_contact_sync_entry,
		build_customer_sync_entry,
	)

	settings = frappe.get_single("Webex Settings")
	entries = []

	if settings.sync_customers:
		for name in _customers_with_phone():
			entry = build_customer_sync_entry(name, settings)
			if entry:
				entries.append(entry)

	if settings.sync_contacts:
		for name in _contacts_with_phone():
			entry = build_contact_sync_entry(name, settings)
			if entry:
				entries.append(entry)

	return entries


@frappe.whitelist()
def list_current_organization_contacts():
	"""Liest den aktuellen Stand des Webex-Organisationstelefonbuchs (read-only,
	verändert nichts) - damit man vor dem ersten Sync prüfen kann, ob dort schon
	etwas (z.B. manuell angelegte Einträge) existiert."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	client = WebexClient(settings=settings)
	try:
		return client.list_organization_contacts(org_id=settings.org_id)
	except WebexAPIError as exc:
		frappe.throw(str(exc))


@frappe.whitelist()
def pull_call_history_now():
	"""Stoesst den Anrufprotokoll-Abruf als Hintergrundjob an, statt synchron im
	Web-Request zu laufen - bei einem groesseren Rueckstand (12h-Haeppchen inkl.
	Drosselung/Retry gegen Webex' Rate-Limit) ueberschreitet das sonst den
	Request-Timeout. Das Ergebnis kommt per Realtime-Event
	("webex_call_history_pull_done") zurueck, sobald der Job fertig ist."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	if not settings.enabled:
		return {"status": "skipped", "reason": "Integration ist nicht aktiviert."}

	frappe.enqueue(
		"erpnext_webex_integration.tasks.run_call_history_pull_background",
		queue="long",
		job_id=f"webex-manual-call-history-pull-{frappe.session.user}",
		deduplicate=True,
		user=frappe.session.user,
	)
	return {"status": "queued"}


@frappe.whitelist()
def import_brand_lines_from_webex():
	"""Liest alle Rufnummern der Organisation und legt/aktualisiert daraus
	"Webex Brand Line"-Einträge an.

	Bei diesem Kunden ist jede Marke als eigene Hunt Group eingerichtet
	(alle Nummern liegen unter derselben Location "Hauptstandort", siehe Export
	aus Control Hub). Die Marke ergibt sich daher aus dem Namen der Hunt Group,
	der einer Nummer zugewiesen ist (Feld "owner" in der Webex-Antwort), nicht
	aus der Location. Das genaue Feldschema für "owner" ist aus der API-Doku-
	Struktur abgeleitet, aber nicht gegen jeden Tenant verifiziert - deshalb
	werden alle Einträge mit einer Zuweisung zusätzlich als Rohdaten zurückgegeben,
	damit Abweichungen sofort auffallen."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Webex Settings")
	client = WebexClient(settings=settings)

	try:
		numbers = client.list_phone_numbers(org_id=settings.org_id)
	except WebexAPIError as exc:
		frappe.throw(str(exc))

	created, updated, skipped = [], [], []
	assigned_entries = []
	for entry in numbers:
		phone_number = entry.get("phoneNumber")
		owner = entry.get("owner") or entry.get("assignedTo") or {}
		owner_type = (owner.get("type") or "").upper()
		owner_first = (owner.get("firstName") or "").strip()
		owner_last = (owner.get("lastName") or "").strip()

		# Bei Hunt Groups liefert Webex "Hunt Group" immer als firstName und den
		# eigentlichen (Marken-)Namen als lastName - z.B. firstName="Hunt Group",
		# lastName="Federkugel.store". Der reine Markenname ist daher lastName.
		if owner_first.lower() == "hunt group" and owner_last:
			owner_name = owner_last
		else:
			owner_name = (owner.get("name") or "").strip() or " ".join(
				filter(None, [owner_first, owner_last])
			).strip()

		if owner_name:
			assigned_entries.append(entry)

		is_hunt_group = "HUNT" in owner_type
		if not phone_number or not owner_name or not is_hunt_group:
			skipped.append(phone_number or owner_name or "(unbekannt)")
			continue

		brand = owner_name

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
		"debug_entries_with_owner": assigned_entries[:5],
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


@frappe.whitelist()
def list_recent_errors(limit=100):
	"""Liefert die letzten Fehler dieser Integration aus dem Standard-Fehlerprotokoll
	(Error Log), gefiltert auf Einträge, deren Titel mit "Webex" beginnt - so muss man
	zur Fehlersuche nicht erst im allgemeinen Fehlerprotokoll danach suchen.

	Der Titel-Feldname im Error-Log-Doctype hieß je nach Frappe-Version "method" oder
	"title" - wird daher dynamisch ermittelt statt fest anzunehmen."""
	frappe.only_for("System Manager")

	meta = frappe.get_meta("Error Log")
	title_field = "title" if meta.has_field("title") else "method"

	rows = frappe.get_all(
		"Error Log",
		filters={title_field: ["like", "Webex%"]},
		fields=["name", "creation", f"{title_field} as title", "error"],
		order_by="creation desc",
		limit_page_length=frappe.utils.cint(limit) or 100,
	)
	for row in rows:
		error_text = row.error or ""
		# Bei einem Python-Traceback steht die eigentliche Fehlermeldung (Exception-
		# Typ + Text) immer in der letzten nicht-leeren Zeile - nicht am Anfang. Eine
		# reine Kürzung auf die ersten N Zeichen (wie zuvor) zeigte daher nur den
		# Aufrufpfad und schnitt genau die eigentliche Ursache ab.
		lines = [line for line in error_text.strip().splitlines() if line.strip()]
		row.summary = lines[-1] if lines else ""
		if len(error_text) > 3000:
			row.error = "... (Anfang gekürzt, vollständig über 'Öffnen') ...\n" + error_text[-3000:]
	return rows


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

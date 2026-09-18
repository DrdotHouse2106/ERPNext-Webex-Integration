"""Dünner Client für die von dieser App benötigten Webex-REST-APIs.

Verwendete Endpunkte (Stand: developer.webex.com, September 2026):

- Dial (Click-to-Call):        POST {api_base}/telephony/calls/dial          (Scope: spark:calls_write)
- Anrufdetails:                GET  {api_base}/telephony/calls/{callId}      (Scope: spark:calls_read)
- Detailliertes Anrufprotokoll: GET  {cdr_base}/cdr_feed                      (Scope: spark-admin:calling_cdr_read)
- Organisations-Telefonbuch:    GET  {contacts_base}/contacts/organizations/{orgId}/contacts/search
                                (Scope: Identity:contact oder Identity:SCIM; orgId ist Pfad-, kein Query-Parameter)
- Webhooks:                     {api_base}/webhooks                          (Scope: spark-admin:webhooks_write)

Wichtig: cdr_feed liegt auf einem eigenen Host (analytics.webexapis.com bzw. regional
z.B. analytics-calling-eu.webexapis.com), nicht auf webexapis.com - daher der eigene
Einstellungs-Wert "cdr_api_base_url". Organization Contacts liegt ganz normal unter
{api_base}/v1, nur mit dem Pfad-Segment "/contacts/organizations/{orgId}/..." davor
(per "Try It" auf developer.webex.com verifiziert).
"""

import re

import frappe
import requests


class WebexAPIError(Exception):
	def __init__(self, message, status_code=None, response_body=None):
		super().__init__(message)
		self.status_code = status_code
		self.response_body = response_body


class WebexClient:
	def __init__(self, settings=None, access_token_override=None):
		self.settings = settings or frappe.get_single("Webex Settings")
		self.api_base_url = (self.settings.webex_api_base_url or "https://webexapis.com/v1").rstrip("/")
		self.cdr_base_url = (self.settings.cdr_api_base_url or "https://analytics.webexapis.com/v1").rstrip("/")
		# Organization Contacts liegt unter {api_base_url}/contacts/organizations/{orgId}/...
		# - also ganz normal unter "/v1", nur mit dem Pfad-Segment "/contacts/..." davor.
		self.contacts_base_url = self.api_base_url
		self.access_token = access_token_override or self.settings.get_password(
			"access_token", raise_exception=False
		)
		self.debug = bool(getattr(self.settings, "debug_logging", 0))

	def _headers(self):
		if not self.access_token:
			frappe.throw("In den Webex-Einstellungen ist kein Zugriffstoken hinterlegt.")
		return {
			"Authorization": f"Bearer {self.access_token}",
			"Content-Type": "application/json",
		}

	def _request(self, method, url, params=None, json=None, timeout=20):
		try:
			response = requests.request(
				method, url, headers=self._headers(), params=params, json=json, timeout=timeout
			)
		except requests.RequestException as exc:
			raise WebexAPIError(f"Verbindung zu Webex fehlgeschlagen: {exc}") from exc

		if self.debug:
			frappe.logger("erpnext_webex_integration").debug(
				f"{method} {url} -> {response.status_code}: {response.text[:2000]}"
			)

		if response.status_code >= 400:
			raise WebexAPIError(
				f"Webex-API-Fehler ({response.status_code}) bei {url}: {response.text[:500]}",
				status_code=response.status_code,
				response_body=response.text,
			)

		if not response.content:
			return {}
		try:
			return response.json()
		except ValueError:
			return {}

	# ------------------------------------------------------------------
	# Click-to-Call / Call Control
	# ------------------------------------------------------------------
	def dial(self, destination, endpoint_id=None):
		payload = {"destination": destination}
		if endpoint_id:
			payload["endpointId"] = endpoint_id
		return self._request("POST", f"{self.api_base_url}/telephony/calls/dial", json=payload)

	def get_call_details(self, call_id):
		return self._request("GET", f"{self.api_base_url}/telephony/calls/{call_id}")

	# ------------------------------------------------------------------
	# Detailliertes Anrufprotokoll (CDR)
	# ------------------------------------------------------------------
	def get_call_history(self, start_time, end_time, locations=None, max_records=1000):
		params = {"startTime": start_time, "endTime": end_time, "max": max_records}
		if locations:
			params["locations"] = locations

		try:
			result = self._request("GET", f"{self.cdr_base_url}/cdr_feed", params=params)
		except WebexAPIError as exc:
			# HTTP 451: Webex nennt bei regionalen Organisationen (z.B. EU) direkt den
			# richtigen Host in der Fehlermeldung - diesen automatisch uebernehmen und
			# dauerhaft speichern, statt dass die Basis-URL manuell angepasst werden muss.
			corrected_base = self._extract_redirect_host(exc)
			if not corrected_base or corrected_base == self.cdr_base_url:
				raise
			self.cdr_base_url = corrected_base
			frappe.db.set_single_value("Webex Settings", "cdr_api_base_url", corrected_base)
			frappe.db.commit()
			result = self._request("GET", f"{self.cdr_base_url}/cdr_feed", params=params)

		if isinstance(result, list):
			return result
		return result.get("items", [])

	@staticmethod
	def _extract_redirect_host(exc):
		if exc.status_code != 451 or not exc.response_body:
			return None
		match = re.search(r'should go to URL:\s*"?(https?://[^\s",}]+)', exc.response_body)
		if not match:
			return None
		host = match.group(1).rstrip("/")
		if not host.endswith("/v1"):
			host = f"{host}/v1"
		return host

	# ------------------------------------------------------------------
	# Rufnummern der Organisation (fuer Marken-Import)
	# ------------------------------------------------------------------
	def list_phone_numbers(self, org_id=None):
		"""Listet alle Rufnummern der Organisation inkl. Zuweisung (owner).

		Scope: spark-admin:telephony_config_read. Die Marke ergibt sich aus dem
		Namen der Hunt Group, der eine Nummer zugewiesen ist (Feld "owner"),
		nicht aus der Location - siehe import_brand_lines_from_webex()."""
		params = {"orgId": org_id} if org_id else None
		result = self._request("GET", f"{self.api_base_url}/telephony/config/numbers", params=params)
		if isinstance(result, list):
			return result
		return result.get("phoneNumbers") or result.get("items") or []

	# ------------------------------------------------------------------
	# Personen-Lookup und Anrufer-ID (fuer Marken-abhaengige Absendernummer)
	# ------------------------------------------------------------------
	def find_person_id_by_email(self, email, org_id=None):
		params = {"email": email}
		if org_id:
			params["orgId"] = org_id
		result = self._request("GET", f"{self.api_base_url}/people", params=params)
		items = result.get("items") if isinstance(result, dict) else None
		return items[0].get("id") if items else None

	def get_person(self, person_id):
		"""Umkehrung von find_person_id_by_email() - liefert u.a. die E-Mail-Adresse
		zu einer Webex-Person-ID (z.B. dem "actorId" aus einem Webhook-Payload),
		um das Screen-Pop-Ereignis dem richtigen ERPNext-Benutzer zuzustellen."""
		return self._request("GET", f"{self.api_base_url}/people/{person_id}")

	def get_caller_id_settings(self, person_id):
		return self._request("GET", f"{self.api_base_url}/people/{person_id}/features/callerId")

	def set_caller_id(self, person_id, phone_number):
		"""Setzt die Anrufer-ID fuer den naechsten ausgehenden Anruf dieser Person.

		Das Schema (selected/customNumber) ist aus der Webex-API-Dokumentationsstruktur
		abgeleitet, aber nicht live gegen jeden Tenant verifiziert. Bei Fehlern zuerst
		get_caller_id_settings() aufrufen und das tatsaechliche Antwortformat mit diesem
        Payload vergleichen."""
		payload = {"selected": "CUSTOM", "customNumber": phone_number}
		return self._request("PUT", f"{self.api_base_url}/people/{person_id}/features/callerId", json=payload)

	# ------------------------------------------------------------------
	# Organisations-Telefonbuch (Organization Contacts)
	# ------------------------------------------------------------------
	# Bestaetigter Endpunkt (developer.webex.com/admin/docs/api/v1/organization-contacts):
	#   GET https://webexapis.com/contacts/organizations/{orgId}/contacts/search
	# orgId ist Pfad-Parameter (nicht Query!), Host/Praefix "/contacts/..." weicht
	# vom normalen "/v1/..." der uebrigen API ab. Create/Update/Delete folgen
	# vermutlich demselben Pfad-Schema, sind aber (noch) nicht gegen die echte
	# API verifiziert - bei Fehlern zuerst hier pruefen.
	def _require_org_id(self, org_id):
		if not org_id:
			frappe.throw(
				"Für das Organisations-Telefonbuch muss die Webex Organisations-ID in "
				"den Webex-Einstellungen hinterlegt sein."
			)
		return org_id

	def list_organization_contacts(self, org_id=None, keyword=None, params=None):
		org_id = self._require_org_id(org_id or self.settings.org_id)
		query = dict(params or {})
		if keyword is not None:
			query["keyword"] = keyword
		result = self._request(
			"GET", f"{self.contacts_base_url}/contacts/organizations/{org_id}/contacts/search", params=query
		)
		if isinstance(result, list):
			return result
		return result.get("result") or result.get("items") or []

	def create_organization_contact(self, payload, org_id=None):
		org_id = self._require_org_id(org_id or self.settings.org_id)
		return self._request(
			"POST", f"{self.contacts_base_url}/contacts/organizations/{org_id}/contacts", json=payload
		)

	def update_organization_contact(self, contact_id, payload, org_id=None):
		org_id = self._require_org_id(org_id or self.settings.org_id)
		return self._request(
			"PUT",
			f"{self.contacts_base_url}/contacts/organizations/{org_id}/contacts/{contact_id}",
			json=payload,
		)

	def delete_organization_contact(self, contact_id, org_id=None):
		org_id = self._require_org_id(org_id or self.settings.org_id)
		return self._request(
			"DELETE", f"{self.contacts_base_url}/contacts/organizations/{org_id}/contacts/{contact_id}"
		)

	# ------------------------------------------------------------------
	# Webhooks
	# ------------------------------------------------------------------
	def list_webhooks(self):
		result = self._request("GET", f"{self.api_base_url}/webhooks")
		return result.get("items", [])

	def create_webhook(self, name, target_url, resource, event, secret=None):
		payload = {
			"name": name,
			"targetUrl": target_url,
			"resource": resource,
			"event": event,
		}
		if secret:
			payload["secret"] = secret
		return self._request("POST", f"{self.api_base_url}/webhooks", json=payload)

	def delete_webhook(self, webhook_id):
		return self._request("DELETE", f"{self.api_base_url}/webhooks/{webhook_id}")

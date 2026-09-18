"""Dünner Client für die von dieser App benötigten Webex-REST-APIs.

Verwendete Endpunkte (Stand: developer.webex.com, September 2026):

- Dial (Click-to-Call):        POST {api_base}/telephony/calls/dial          (Scope: spark:calls_write)
- Anrufdetails:                GET  {api_base}/telephony/calls/{callId}      (Scope: spark:calls_read)
- Detailliertes Anrufprotokoll: GET  {cdr_base}/cdr_feed                      (Scope: spark-admin:calling_cdr_read)
- Organisations-Telefonbuch:    {api_base}/organization/contacts             (Scope: Identity:contact oder Identity:SCIM)
- Webhooks:                     {api_base}/webhooks                          (Scope: spark-admin:webhooks_write)

Wichtig: cdr_feed liegt auf einem eigenen Host (analytics.webexapis.com), nicht auf
webexapis.com - daher der eigene Einstellungs-Wert "cdr_api_base_url".
"""

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
		result = self._request("GET", f"{self.cdr_base_url}/cdr_feed", params=params)
		if isinstance(result, list):
			return result
		return result.get("items", [])

	# ------------------------------------------------------------------
	# Organisations-Telefonbuch (Organization Contacts)
	# ------------------------------------------------------------------
	def create_organization_contact(self, payload):
		return self._request("POST", f"{self.api_base_url}/organization/contacts", json=payload)

	def update_organization_contact(self, contact_id, payload):
		return self._request(
			"PUT", f"{self.api_base_url}/organization/contacts/{contact_id}", json=payload
		)

	def delete_organization_contact(self, contact_id):
		return self._request("DELETE", f"{self.api_base_url}/organization/contacts/{contact_id}")

	def list_organization_contacts(self, params=None):
		result = self._request("GET", f"{self.api_base_url}/organization/contacts", params=params)
		if isinstance(result, list):
			return result
		return result.get("Contacts") or result.get("items") or []

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

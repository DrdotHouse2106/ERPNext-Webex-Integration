import frappe
from frappe.model.document import Document


class WebexSettings(Document):
	def validate(self):
		self.webhook_url = self.get_webhook_target_url()
		self.oauth_redirect_uri = self.get_oauth_redirect_uri()

	def get_webhook_target_url(self):
		site_url = frappe.utils.get_url().rstrip("/")
		return f"{site_url}/api/method/erpnext_webex_integration.api.webex_webhook"

	def get_oauth_redirect_uri(self):
		site_url = frappe.utils.get_url().rstrip("/")
		return f"{site_url}/api/method/erpnext_webex_integration.api.webex_oauth_callback"

	@frappe.whitelist()
	def test_connection(self):
		"""Prueft, ob das hinterlegte Zugriffstoken funktioniert.

		Nutzt bewusst GET /webhooks (Scope spark:webhooks_read) statt /people/me
		(Scope spark:people_read), da letzterer Scope von dieser Integration
		nicht angefordert wird (Prinzip der minimalen Rechte)."""
		from erpnext_webex_integration.webex_client import WebexAPIError, WebexClient

		client = WebexClient(settings=self)
		try:
			webhooks = client.list_webhooks()
		except WebexAPIError as exc:
			frappe.throw(str(exc))
		return {
			"display_name": f"{len(webhooks)} Webhook(s) registriert",
		}

	@frappe.whitelist()
	def register_call_webhook(self):
		"""Legt bei Webex einen Webhook fuer Echtzeit-Anrufereignisse an."""
		from erpnext_webex_integration.webex_client import WebexAPIError, WebexClient

		if not self.webhook_secret:
			frappe.throw("Bitte zuerst ein Webhook-Geheimnis vergeben und speichern.")

		client = WebexClient(settings=self)
		try:
			result = client.create_webhook(
				name="ERPNext Webex Integration - Anrufereignisse",
				target_url=self.get_webhook_target_url(),
				resource="telephony_calls",
				event="all",
				secret=self.get_password("webhook_secret"),
			)
		except WebexAPIError as exc:
			frappe.throw(str(exc))
		return result

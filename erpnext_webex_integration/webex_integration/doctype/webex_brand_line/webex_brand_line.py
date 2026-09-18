import frappe
from frappe.model.document import Document


class WebexBrandLine(Document):
	def validate(self):
		self.erp_brand_doctype = self._get_target_doctype()
		if not self.erp_brand_value:
			# Bestmoegliche Vorbelegung (deckt den Fall ab, in dem die Marke bei
			# Webex und in ERPNext gleich heisst, z.B. "FranceTec") - wird nur
			# gesetzt, wenn noch keine bewusste Auswahl getroffen wurde.
			self.erp_brand_value = self.brand

	def _get_target_doctype(self):
		settings = frappe.get_cached_doc("Webex Settings")
		brand_fieldname = settings.customer_brand_fieldname
		if not brand_fieldname:
			return None
		field = frappe.get_meta("Customer").get_field(brand_fieldname)
		if field and field.fieldtype == "Link":
			return field.options
		return None

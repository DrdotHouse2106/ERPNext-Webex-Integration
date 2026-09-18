frappe.ui.form.on("Contact", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button(
			__("Anruf starten"),
			() => erpnext_webex_integration.click_to_call(frm.doctype, frm.doc.name),
			__("Webex")
		);
	},
});

frappe.ui.form.on("Webex Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Verbindung testen"), () => {
			frappe.call({
				method: "test_connection",
				doc: frm.doc,
				freeze: true,
				callback: (r) => {
					if (r.message) {
						frappe.msgprint({
							title: __("Verbindung erfolgreich"),
							message: __("Angemeldet als: {0}", [r.message.display_name || "?"]),
							indicator: "green",
						});
					}
				},
			});
		});

		frm.add_custom_button(__("Anrufprotokoll-Webhook registrieren"), () => {
			frappe.call({
				method: "register_call_webhook",
				doc: frm.doc,
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Webhook registriert."), indicator: "green" });
				},
			});
		});

		frm.add_custom_button(__("Telefonbuch jetzt synchronisieren"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.sync_phonebook_now",
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Synchronisation gestartet."), indicator: "green" });
					frm.reload_doc();
				},
			});
		});

		frm.add_custom_button(__("Anrufprotokoll jetzt abrufen"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.pull_call_history_now",
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Abruf gestartet."), indicator: "green" });
					frm.reload_doc();
				},
			});
		});
	},
});

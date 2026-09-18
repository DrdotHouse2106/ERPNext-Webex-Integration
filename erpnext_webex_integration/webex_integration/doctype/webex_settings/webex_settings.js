frappe.ui.form.on("Webex Settings", {
	refresh(frm) {
		if (window.erpnext_webex_integration && typeof erpnext_webex_integration.show_oauth_result === "function") {
			erpnext_webex_integration.show_oauth_result();
		}

		frm.add_custom_button(__("Mit Webex verbinden"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Bitte zuerst speichern, damit Client ID/Secret gesichert sind."));
				return;
			}
			window.location.href = "/api/method/erpnext_webex_integration.api.webex_oauth_connect";
		}).addClass("btn-primary");

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

		frm.add_custom_button(__("Zufälliges Webhook-Geheimnis erzeugen"), () => {
			const bytes = new Uint8Array(24);
			window.crypto.getRandomValues(bytes);
			const secret = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
			frm.set_value("webhook_secret", secret);
			frappe.show_alert({
				message: __("Geheimnis erzeugt – bitte speichern und danach den Webhook (neu) registrieren."),
				indicator: "blue",
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

		frm.add_custom_button(__("Marken-Rufnummern von Webex importieren"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.import_brand_lines_from_webex",
				freeze: true,
				callback: (r) => {
					const res = r.message || {};
					frappe.msgprint({
						title: __("Import abgeschlossen"),
						message: __("Neu: {0}<br>Aktualisiert: {1}<br>Übersprungen: {2}<br><br>Beispiel-Rohdaten:<br><pre>{3}</pre>", [
							(res.created || []).join(", ") || "-",
							(res.updated || []).join(", ") || "-",
							res.skipped_count,
							JSON.stringify(res.raw_sample, null, 2),
						]),
						indicator: "green",
					});
				},
			});
		});

		frm.add_custom_button(__("Anrufer-ID-Einstellungen anzeigen (Debug)"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.debug_caller_id_settings",
				freeze: true,
				callback: (r) => {
					frappe.msgprint({
						title: __("Anrufer-ID-Einstellungen"),
						message: `<pre>${JSON.stringify(r.message, null, 2)}</pre>`,
						indicator: "blue",
					});
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

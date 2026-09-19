frappe.ui.form.on("Webex Settings", {
	refresh(frm) {
		if (window.erpnext_webex_integration && typeof erpnext_webex_integration.show_oauth_result === "function") {
			erpnext_webex_integration.show_oauth_result();
		}

		frappe.model.with_doctype("Customer", () => {
			const options = frappe
				.get_meta("Customer")
				.fields.filter((df) => !df.is_virtual && df.fieldname)
				.map((df) => ({
					value: df.fieldname,
					label: `${df.label || df.fieldname} (${df.fieldname})`,
				}));
			const field = frm.fields_dict.customer_brand_fieldname;
			if (field && typeof field.set_data === "function") {
				field.set_data(options);
			} else {
				frm.set_df_property("customer_brand_fieldname", "options", options);
				frm.refresh_field("customer_brand_fieldname");
			}
		});

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

		// Gruppe: Webhook
		frm.add_custom_button(__("Zufälliges Geheimnis erzeugen"), () => {
			const bytes = new Uint8Array(24);
			window.crypto.getRandomValues(bytes);
			const secret = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
			frm.set_value("webhook_secret", secret);
			frappe.show_alert({
				message: __("Geheimnis erzeugt – bitte speichern und danach den Webhook (neu) registrieren."),
				indicator: "blue",
			});
		}, __("Webhook"));

		frm.add_custom_button(__("Webhook registrieren"), () => {
			frappe.call({
				method: "register_call_webhook",
				doc: frm.doc,
				freeze: true,
				callback: () => {
					frappe.show_alert({ message: __("Webhook registriert."), indicator: "green" });
				},
			});
		}, __("Webhook"));

		// Gruppe: Telefonbuch
		frm.add_custom_button(__("Vorschau (sendet nichts an Webex)"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.preview_phonebook_sync",
				freeze: true,
				callback: (r) => {
					const entries = r.message || [];
					if (!entries.length) {
						frappe.msgprint(__("Keine Kunden/Kontakte mit Rufnummer gefunden (oder Sync für Kunden/Kontakte ist deaktiviert)."));
						return;
					}
					let rows = entries
						.map(
							(e) =>
								`<tr><td>${e.doctype}</td><td>${frappe.utils.escape_html(e.docname)}</td><td>${frappe.utils.escape_html(e.display_name)}</td><td>${e.phone_number}</td><td>${e.action}</td></tr>`
						)
						.join("");
					frappe.msgprint({
						title: __("Vorschau: {0} Einträge würden gesendet", [entries.length]),
						wide: true,
						message: `<div style="max-height:60vh;overflow:auto"><table class="table table-bordered">
							<thead><tr><th>Typ</th><th>ID</th><th>Anzeigename</th><th>Rufnummer</th><th>Aktion</th></tr></thead>
							<tbody>${rows}</tbody>
						</table></div>`,
						indicator: "blue",
					});
				},
			});
		}, __("Telefonbuch"));

		frm.add_custom_button(__("Aktuelle Einträge in Webex anzeigen"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.list_current_organization_contacts",
				freeze: true,
				callback: (r) => {
					const contacts = r.message || [];
					frappe.msgprint({
						title: __("Aktuell {0} Einträge im Webex-Telefonbuch", [contacts.length]),
						wide: true,
						message: `<pre style="max-height:60vh;overflow:auto">${JSON.stringify(contacts, null, 2)}</pre>`,
						indicator: "blue",
					});
				},
			});
		}, __("Telefonbuch"));

		frm.add_custom_button(__("Jetzt synchronisieren"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.sync_phonebook_now",
				freeze: true,
				callback: (r) => {
					const res = r.message || {};
					if (res.status === "skipped") {
						frappe.msgprint(__("Übersprungen: {0}", [res.reason]));
						return;
					}
					frappe.msgprint({
						title: __("Synchronisation abgeschlossen"),
						message: __(
							"Kunden erfolgreich: {0}{1}<br>Kontakte erfolgreich: {2}{3}",
							[
								res.customers_ok,
								res.customers_failed && res.customers_failed.length
									? ` (fehlgeschlagen: ${res.customers_failed.join(", ")})`
									: "",
								res.contacts_ok,
								res.contacts_failed && res.contacts_failed.length
									? ` (fehlgeschlagen: ${res.contacts_failed.join(", ")})`
									: "",
							]
						),
						indicator:
							(res.customers_failed && res.customers_failed.length) ||
							(res.contacts_failed && res.contacts_failed.length)
								? "orange"
								: "green",
					});
					frm.reload_doc();
				},
			});
		}, __("Telefonbuch"));

		frm.add_custom_button(__("Marken-Rufnummern importieren"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.import_brand_lines_from_webex",
				freeze: true,
				callback: (r) => {
					const res = r.message || {};
					frappe.msgprint({
						title: __("Import abgeschlossen"),
						message: __("Neu: {0}<br>Aktualisiert: {1}<br>Übersprungen: {2}<br><br>Zugewiesene Nummern (Rohdaten):<br><pre>{3}</pre>", [
							(res.created || []).join(", ") || "-",
							(res.updated || []).join(", ") || "-",
							res.skipped_count,
							JSON.stringify(res.debug_entries_with_owner, null, 2),
						]),
						indicator: "green",
					});
				},
			});
		}, __("Telefonbuch"));

		// Gruppe: Anrufprotokoll
		frm.add_custom_button(__("Jetzt abrufen"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.pull_call_history_now",
				freeze: true,
				callback: (r) => {
					const res = r.message || {};
					if (res.status === "skipped") {
						frappe.msgprint(__("Übersprungen: {0}", [res.reason]));
					} else if (res.status === "error") {
						frappe.msgprint({
							title: __("Abruf fehlgeschlagen"),
							message: res.message,
							indicator: "red",
						});
					} else if (res.status === "partial") {
						frappe.msgprint({
							title: __("Teilweise abgerufen ({0} Datensätze)", [res.fetched]),
							message: res.message,
							indicator: "orange",
						});
						frm.reload_doc();
					} else {
						frappe.show_alert({
							message: __("{0} Datensätze von Webex erhalten (Zeitraum {1} – {2}).", [
								res.fetched,
								res.from,
								res.to,
							]),
							indicator: "green",
						});
						frm.reload_doc();
					}
				},
			});
		}, __("Anrufprotokoll"));

		// Gruppe: Debug
		frm.add_custom_button(__("Anrufer-ID-Einstellungen anzeigen"), () => {
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
		}, __("Debug"));

		frm.add_custom_button(__("Fehlerprotokoll anzeigen"), () => {
			frappe.call({
				method: "erpnext_webex_integration.api.list_recent_errors",
				freeze: true,
				callback: (r) => {
					const rows = r.message || [];
					if (!rows.length) {
						frappe.msgprint(__("Keine Webex-bezogenen Fehler im Protokoll gefunden."));
						return;
					}
					const body = rows
						.map((row) => {
							const link = `/app/error-log/${encodeURIComponent(row.name)}`;
							return `<tr>
								<td style="white-space:nowrap">${frappe.datetime.str_to_user(row.creation)}</td>
								<td>${frappe.utils.escape_html(row.title || "")}</td>
								<td>
									<div style="color:#c0392b;font-weight:600">${frappe.utils.escape_html(row.summary || "")}</div>
									<details>
										<summary style="cursor:pointer">${__("Vollständiger Traceback")}</summary>
										<pre style="white-space:pre-wrap;margin:0;font-size:11px">${frappe.utils.escape_html(row.error || "")}</pre>
									</details>
								</td>
								<td><a href="${link}" target="_blank">${__("Öffnen")}</a></td>
							</tr>`;
						})
						.join("");
					frappe.msgprint({
						title: __("Letzte Webex-Fehler ({0})", [rows.length]),
						wide: true,
						message: `<div style="max-height:65vh;overflow:auto"><table class="table table-bordered">
							<thead><tr><th>Zeitpunkt</th><th>Titel</th><th>Meldung</th><th></th></tr></thead>
							<tbody>${body}</tbody>
						</table></div>`,
						indicator: "red",
					});
				},
			});
		}, __("Debug"));
	},
});

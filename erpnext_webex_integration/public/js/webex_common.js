window.erpnext_webex_integration = window.erpnext_webex_integration || {};

erpnext_webex_integration.show_oauth_result = function () {
	const params = new URLSearchParams(window.location.search);
	if (params.has("webex_connected")) {
		frappe.show_alert({ message: __("Erfolgreich mit Webex verbunden."), indicator: "green" });
	} else if (params.has("webex_error")) {
		frappe.msgprint({
			title: __("Verbindung fehlgeschlagen"),
			message: __("Fehler: {0}. Bitte erneut versuchen.", [params.get("webex_error")]),
			indicator: "red",
		});
	} else {
		return;
	}
	params.delete("webex_connected");
	params.delete("webex_error");
	const newQuery = params.toString();
	const newUrl = window.location.pathname + (newQuery ? `?${newQuery}` : "");
	window.history.replaceState({}, "", newUrl);
};

// Screen-Pop: zeigt bei eingehendem Anruf (Realtime-Event vom Server, siehe
// api._notify_incoming_call) sofort einen Hinweis mit Kunde/Kontakt und einem
// Link zum Datensatz - noch bevor der Anruf angenommen wird.
frappe.ready(function () {
	if (!frappe.realtime || window.erpnext_webex_integration._screen_pop_bound) {
		return;
	}
	window.erpnext_webex_integration._screen_pop_bound = true;

	frappe.realtime.on("webex_incoming_call", function (data) {
		const route = data.customer
			? `/app/customer/${encodeURIComponent(data.customer)}`
			: data.contact
			? `/app/contact/${encodeURIComponent(data.contact)}`
			: data.lead
			? `/app/lead/${encodeURIComponent(data.lead)}`
			: null;

		const link = route ? ` <a href="${route}" target="_blank">${__("Datensatz öffnen")}</a>` : "";

		frappe.show_alert(
			{
				message: __("Anruf von {0} – {1}{2}", [data.from_number || __("unbekannt"), data.subtitle, link]),
				indicator: "orange",
			},
			20
		);
	});
});

erpnext_webex_integration.click_to_call = function (doctype, docname) {
	frappe.call({
		method: "erpnext_webex_integration.api.click_to_call",
		args: { doctype: doctype, docname: docname },
		freeze: true,
		freeze_message: __("Anruf wird vorbereitet..."),
		callback: function (r) {
			if (!r.message) {
				return;
			}
			if (r.message.mode === "tel") {
				window.open(r.message.tel_link, "_self");
			} else if (r.message.mode === "api") {
				frappe.show_alert({
					message: __("Anruf wird über Webex gestartet..."),
					indicator: "green",
				});
			}
		},
	});
};

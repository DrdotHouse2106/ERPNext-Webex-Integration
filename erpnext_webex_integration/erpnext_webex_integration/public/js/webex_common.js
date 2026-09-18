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

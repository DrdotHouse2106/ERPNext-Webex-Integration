window.erpnext_webex_integration = window.erpnext_webex_integration || {};

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

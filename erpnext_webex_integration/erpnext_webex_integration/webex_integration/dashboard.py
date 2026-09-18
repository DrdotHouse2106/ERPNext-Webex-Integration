def get_customer_dashboard_data(data):
	data.setdefault("transactions", []).append(
		{"label": "Webex", "items": ["Webex Call Log"]}
	)
	data.setdefault("non_standard_fieldnames", {})["Webex Call Log"] = "customer"
	return data


def get_contact_dashboard_data(data):
	data.setdefault("transactions", []).append(
		{"label": "Webex", "items": ["Webex Call Log"]}
	)
	data.setdefault("non_standard_fieldnames", {})["Webex Call Log"] = "contact"
	return data

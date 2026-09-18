app_name = "erpnext_webex_integration"
app_title = "ERPNext Webex Integration"
app_publisher = "DrdotHouse2106"
app_description = "Dokumentiert Anrufe und synchronisiert Telefonnummern zwischen ERPNext und Cisco Webex Calling."
app_email = "drdothouse@gmail.com"
app_license = "MIT"
app_icon_url = "/assets/erpnext_webex_integration/images/webex-icon.svg"
required_apps = ["frappe"]

# Client-seitige Skripte (Buttons "Anruf starten" etc.)
# ---------------------------------------------------
app_include_js = ["public/js/webex_common.js"]

doctype_js = {
    "Customer": "public/js/customer.js",
    "Contact": "public/js/contact.js",
}

# Verknüpfungen im Formular anzeigen (Reiter "Verbindungen")
# -----------------------------------------------------------
override_doctype_dashboards = {
    "Customer": "erpnext_webex_integration.webex_integration.dashboard.get_customer_dashboard_data",
    "Contact": "erpnext_webex_integration.webex_integration.dashboard.get_contact_dashboard_data",
}

# Custom Fields (Webex-Kontakt-ID) beim Installieren/Migrieren anlegen
# ----------------------------------------------------------------------
after_install = "erpnext_webex_integration.install.after_install"
after_migrate = "erpnext_webex_integration.install.after_install"
before_uninstall = "erpnext_webex_integration.install.before_uninstall"

# Automatische Synchronisation bei Änderungen an Kunde/Kontakt
# ---------------------------------------------------------------
doc_events = {
    "Customer": {
        "on_update": "erpnext_webex_integration.tasks.on_customer_update",
        "on_trash": "erpnext_webex_integration.tasks.on_customer_trash",
    },
    "Contact": {
        "on_update": "erpnext_webex_integration.tasks.on_contact_update",
        "on_trash": "erpnext_webex_integration.tasks.on_contact_trash",
    },
}

# Geplante Aufgaben
# -------------------
# - Anrufprotokoll (CDR) alle 15 Minuten abrufen (Webex hält Daten nur 48h vor)
# - Telefonbuch-Synchronisation stündlich anstoßen (die Frequenz wird zusätzlich
#   in den Webex-Einstellungen gesteuert: stündlich oder täglich)
scheduler_events = {
    "cron": {
        "*/15 * * * *": [
            "erpnext_webex_integration.tasks.pull_call_history",
        ],
    },
    "hourly": [
        "erpnext_webex_integration.tasks.sync_phonebook",
    ],
    "daily": [
        "erpnext_webex_integration.tasks.refresh_access_token",
    ],
}

# Whitelisted API-Methoden werden direkt über erpnext_webex_integration.api.*
# aufgerufen (siehe api.py) - hier ist kein zusätzlicher Eintrag nötig.

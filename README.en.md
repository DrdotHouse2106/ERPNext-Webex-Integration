# ERPNext Webex Integration

A [Frappe](https://frappeframework.com/)/[ERPNext](https://erpnext.com/) app that connects **Cisco Webex Calling** (including Webex-Calling-based resellers such as Placetel) with ERPNext.

> Deutsche Version (Standard): [README.md](README.md)

## Features

- **Call documentation**: incoming and outgoing calls are automatically logged as *Webex Call Log* records in ERPNext and, where the phone number allows a match, linked to a **Customer**, **Contact** or **Lead**. Every log entry can be annotated with call notes.
  - via **webhooks** (real-time, `telephony_calls` resource), and/or
  - via periodic polling of Webex's **Detailed Call History (CDR)** API.
  - Both sources are matched to the same real call via `callSessionId` (webhook) / `Correlation ID` (CDR) and merged into a single record — so a hunt group ringing several colleagues at once doesn't create duplicate entries.
- **Phonebook sync**: customer and contact phone numbers are pushed into the **Webex organization directory**, including the customer ID in the display name (e.g. `Acme Corp (CUST-00042)`), so incoming calls immediately show who is calling.
- **Click-to-call**: an "Anruf starten" (Start call) button on Customer and Contact. Defaults to a `tel:` link (opens the local Webex app, no admin rights required), with an optional server-side mode using the Webex Call Control API.
- Every call/sync operation is logged for traceability (raw payloads, error log).

## Requirements

- A running **ERPNext/Frappe site** (version 14 or 15) with bench access.
- A **Webex Calling tenant** (directly with Cisco Webex, or via a reseller such as **Placetel**).
- A **Webex Integration** (OAuth app) registered at [developer.webex.com/my-apps](https://developer.webex.com/my-apps).

### Which Webex permissions do I need?

| Feature | Required scope | Required role |
|---|---|---|
| Click-to-call (`tel:` link) | none (client-side) | any user |
| Click-to-call (Call Control API, dial) | `spark:calls_write` | user with a Webex Calling license |
| Fetch call details (webhook enrichment) | `spark:calls_read` | any user |
| Call history sync (CDR) | `spark-admin:calling_cdr_read` | Full or Read-only Administrator **and** the admin role "Webex Calling Detailed Call History API access" enabled in Control Hub. **Important:** you cannot assign this role to yourself in Control Hub (the checkbox is greyed out on your own account) – a **different** administrator has to assign it to you. |
| Phonebook sync (Organization Contacts) | `Identity:contact` | Full Administrator of the organization |
| Webhook registration | `spark:webhooks_write` (add `spark:webhooks_read` to inspect existing ones) | user with a Webex Calling license |

With **Full Administrator** rights all features are generally available. On reseller tenants (e.g. Placetel) some admin capabilities may remain with the reseller — check under Control Hub → *Users → your account → Roles* if in doubt.

## Installation

```bash
bench get-app https://github.com/DrdotHouse2106/ERPNext-Webex-Integration.git
bench --site <your-site> install-app erpnext_webex_integration
bench --site <your-site> migrate
```

## Setup

The full OAuth login runs directly through ERPNext — no manual copying of codes/tokens required.

1. **Create a Webex Integration** at [developer.webex.com/my-apps](https://developer.webex.com/my-apps) with the scopes listed above. Enter a placeholder under "Redirect URI(s)" for now (corrected in step 3).
2. In ERPNext, open **Webex Settings** (search the awesomebar):
   - Enable *Integration aktiviert* (Enabled).
   - Enter the **Client ID** and **Client Secret** from the Webex Integration, plus the organization ID.
   - Save.
3. The **"OAuth Redirect-URI"** field now shows an address like `https://your-site.example.com/api/method/erpnext_webex_integration.api.webex_oauth_callback`. Enter this URL **exactly** as a Redirect URI in the Webex Integration (replacing the placeholder from step 1) and save there too.
4. Back in ERPNext, click **"Mit Webex verbinden"** (Connect with Webex) → log in / approve on Webex → you're redirected back to ERPNext automatically. Access and refresh tokens are stored automatically and will be **refreshed automatically** from now on (daily scheduled job, well ahead of the 14-day expiry).
5. Enable the modules you want (call history, phonebook sync, click-to-call) and save.
6. For real-time call events: set a webhook secret, save, then use the **"Anrufprotokoll-Webhook registrieren"** button to register the webhook with Webex.
7. Do a first test run using **"Test connection"**, **"Sync phonebook now"** and **"Pull call history now"**.

> Quick one-off test without setting up OAuth: paste a 12h personal access token from the Webex developer docs directly into the *Zugriffstoken* field. For ongoing operation you still need the OAuth flow (steps 1–4), since only that gets refreshed automatically.

## Known limitations

- Phone-number-to-customer matching compares the last digits of a number (tolerant to formatting differences) rather than a strict E.164 match, so duplicate numbers across customers can cause mismatches.
- The JSON schema of Webex's *Organization Contacts* API may differ slightly by tenant/API version. After initial setup, verify via **"Test connection"** / a test entry that fields (`displayName`, `phoneNumbers`, …) are mapped correctly, and adjust `tasks.py` (`_build_organization_contact_payload`) if needed.
- Click-to-call via the Call Control API currently uses a shared service token, so the call is technically placed from the token owner's devices, not individually per agent. True per-agent click-to-call requires adding per-user OAuth (see Roadmap).
- **Brand-based caller ID** (DocType *Webex Brand Line*, `customer_brand_fieldname` field in Webex Settings): automatically sets the outgoing caller ID matching the customer's brand before dialing, via Webex's "Configure Caller ID Settings for a Person" API. The JSON schema (`selected`/`customNumber`) is derived from Webex's documentation but not verified against every tenant — use the **"Anrufer-ID-Einstellungen anzeigen (Debug)"** button in Webex Settings to inspect the actual format and adjust `webex_client.py` (`set_caller_id`) if needed. Currently only works for the one OAuth-connected user (see point above).

## Roadmap
- [ ] Per-user OAuth so click-to-call and call history are correctly attributed to each agent.
- [ ] Real-time screen-pop / desk notification on incoming calls.
- [ ] ERPNext workspace with reporting (calls per customer/agent, response times).

## License

[MIT](LICENSE)

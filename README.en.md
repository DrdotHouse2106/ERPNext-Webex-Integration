# ERPNext Webex Integration

A [Frappe](https://frappeframework.com/)/[ERPNext](https://erpnext.com/) app that connects **Cisco Webex Calling** (including Webex-Calling-based resellers such as Placetel) with ERPNext.

> Deutsche Version (Standard): [README.md](README.md)

## Features

- **Call documentation**: incoming and outgoing calls are automatically logged as *Webex Call Log* records in ERPNext and, where the phone number allows a match, linked to a **Customer**, **Contact** or **Lead**. Every log entry can be annotated with call notes.
  - via **webhooks** (real-time, `telephony_calls` resource), and/or
  - via periodic polling of Webex's **Detailed Call History (CDR)** API.
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
| Call history sync (CDR) | `spark-admin:calling_cdr_read` | Full or Read-only Administrator **and** the admin role "Webex Calling Detailed Call History API access" enabled in Control Hub |
| Phonebook sync (Organization Contacts) | `Identity:contact` (or `Identity:SCIM`) | Full Administrator of the organization |
| Webhook registration | `spark-admin:webhooks_write` | Administrator |

With **Full Administrator** rights all features are generally available. On reseller tenants (e.g. Placetel) some admin capabilities may remain with the reseller — check under Control Hub → *Users → your account → Roles* if in doubt.

## Installation

```bash
bench get-app https://github.com/DrdotHouse2106/ERPNext-Webex-Integration.git
bench --site <your-site> install-app erpnext_webex_integration
bench --site <your-site> migrate
```

## Setup

1. **Create a Webex Integration** at [developer.webex.com/my-apps](https://developer.webex.com/my-apps) with the scopes listed above.
2. **Obtain an access token**: complete the OAuth flow once (e.g. via the developer portal's "Try It" feature or your own script) and store the resulting access token in ERPNext under **Webex Settings**.
   > Note: OAuth access tokens expire after 14 days. For continuous operation the refresh token should be renewed regularly (e.g. via a scheduled script) — this is not yet automated in this version (see Roadmap).
3. In **Webex Settings** (search the awesomebar):
   - Enable *Integration aktiviert* (Enabled).
   - Enter the access token and organization ID.
   - Enable the modules you want (call history, phonebook sync, click-to-call).
4. For real-time call events: set a webhook secret, save, then use the **"Register call webhook"** button (`register_call_webhook()`) to register the webhook with Webex.
5. Do a first test run using **"Test connection"**, **"Sync phonebook now"** and **"Pull call history now"**.

## Known limitations

- Phone-number-to-customer matching compares the last digits of a number (tolerant to formatting differences) rather than a strict E.164 match, so duplicate numbers across customers can cause mismatches.
- The JSON schema of Webex's *Organization Contacts* API may differ slightly by tenant/API version. After initial setup, verify via **"Test connection"** / a test entry that fields (`displayName`, `phoneNumbers`, …) are mapped correctly, and adjust `tasks.py` (`_build_organization_contact_payload`) if needed.
- Click-to-call via the Call Control API currently uses a shared service token, so the call is technically placed from the token owner's devices, not individually per agent. True per-agent click-to-call requires adding per-user OAuth (see Roadmap).
- OAuth token refresh is not yet automated.

## Roadmap

- [ ] Automatic refresh of the OAuth access token.
- [ ] Per-user OAuth so click-to-call and call history are correctly attributed to each agent.
- [ ] Real-time screen-pop / desk notification on incoming calls.
- [ ] ERPNext workspace with reporting (calls per customer/agent, response times).

## License

[MIT](LICENSE)

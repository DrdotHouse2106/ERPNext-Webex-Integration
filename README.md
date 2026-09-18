# ERPNext Webex Integration

Eine [Frappe](https://frappeframework.com/)/[ERPNext](https://erpnext.com/)-App, die **Cisco Webex Calling** (bzw. Webex-Calling-basierte Anbieter wie Placetel) mit ERPNext verbindet.

> English version: [README.en.md](README.en.md)

## Funktionen

- **Anrufe dokumentieren**: Ein- und ausgehende Telefonate werden automatisch als *Webex Call Log*-Einträge in ERPNext angelegt und, sofern anhand der Rufnummer möglich, direkt einem **Kunden**, **Kontakt** oder **Lead** zugeordnet. Jeder Eintrag kann um eine Gesprächsnotiz ergänzt werden.
  - Über **Webhooks** (Echtzeit, Ressource `telephony_calls`) und/oder
  - über den regelmäßigen Abruf des **detaillierten Anrufprotokolls (CDR)** von Webex.
- **Telefonbuch-Synchronisation**: Rufnummern von Kunden und Kontakten werden ins **Webex-Organisationstelefonbuch** übertragen – inklusive Kundennummer im Anzeigenamen (z. B. `Mustermann GmbH (CUST-00042)`), damit bei eingehenden Anrufen sofort erkennbar ist, wer anruft.
- **Click-to-Call**: Button "Anruf starten" auf Kunde und Kontakt. Standardmäßig per `tel:`-Link (öffnet die lokale Webex-App, funktioniert ohne Admin-Rechte), optional serverseitig über die Webex Call-Control-API.
- Jeder Anruf-/Sync-Vorgang wird nachvollziehbar protokolliert (Rohdaten, Fehlerprotokoll).

## Voraussetzungen

- Eine laufende **ERPNext-/Frappe-Site** (Version 14 oder 15) mit Bench-Zugriff.
- Ein **Webex-Calling-Tenant** (z. B. direkt bei Cisco Webex oder über einen Reseller wie **Placetel**).
- Eine **Webex-Integration** (OAuth-App), registriert unter [developer.webex.com/my-apps](https://developer.webex.com/my-apps).

### Welche Rechte braucht mein Webex-Benutzer?

| Funktion | Benötigter Scope | Erforderliche Rolle |
|---|---|---|
| Click-to-Call (`tel:`-Link) | keiner (Client-seitig) | jeder Benutzer |
| Click-to-Call (Call-Control-API, Dial) | `spark:calls_write` | Benutzer mit Webex-Calling-Lizenz |
| Anrufdetails abrufen (Webhook-Anreicherung) | `spark:calls_read` | jeder Benutzer |
| Anrufprotokoll-Abruf (CDR) | `spark-admin:calling_cdr_read` | Full- oder Read-only-Administrator **und** in Control Hub zusätzlich die Admin-Rolle „Webex Calling Detailed Call History API access" aktiviert |
| Telefonbuch-Synchronisation (Organization Contacts) | `Identity:contact` | Full-Administrator der Organisation |
| Webhook-Registrierung | `spark:webhooks_write` (zum Prüfen zusätzlich `spark:webhooks_read`) | Benutzer mit Webex-Calling-Lizenz |

Mit **vollen Administratorrechten** (Full Administrator) stehen grundsätzlich alle Funktionen zur Verfügung. Bei Reseller-Tenants (z. B. Placetel) kann es sein, dass einzelne Admin-Funktionen beim Reseller verbleiben – im Zweifel in Control Hub unter *Benutzer → eigenes Konto → Rollen* prüfen.

## Installation

```bash
bench get-app https://github.com/DrdotHouse2106/ERPNext-Webex-Integration.git
bench --site <deine-site> install-app erpnext_webex_integration
bench --site <deine-site> migrate
```

## Einrichtung

Der komplette OAuth-Login läuft direkt über ERPNext – kein manuelles Kopieren von Codes/Token nötig.

1. **Webex-Integration anlegen**: Unter [developer.webex.com/my-apps](https://developer.webex.com/my-apps) eine neue *Integration* erstellen und die oben genannten Scopes aktivieren. Bei "Redirect URI(s)" noch einen Platzhalter eintragen (wird in Schritt 3 korrigiert).
2. In ERPNext **Webex Settings** öffnen (im Awesomebar suchen):
   - *Integration aktiviert* einschalten.
   - **Client ID** und **Client Secret** aus der Webex-Integration eintragen, Organisations-ID eintragen.
   - Speichern.
3. Das Feld **"OAuth Redirect-URI"** zeigt jetzt eine Adresse wie `https://deine-site.example.com/api/method/erpnext_webex_integration.api.webex_oauth_callback`. Diese URL **exakt** in der Webex-Integration unter "Redirect URI(s)" hinterlegen (den Platzhalter aus Schritt 1 ersetzen) und dort speichern.
4. Zurück in ERPNext auf **"Mit Webex verbinden"** klicken → Login/Freigabe bei Webex → automatische Weiterleitung zurück nach ERPNext. Zugriffs- und Refresh-Token werden automatisch gespeichert und ab jetzt **automatisch erneuert** (täglicher Scheduler-Job, rechtzeitig vor Ablauf nach 14 Tagen).
5. Gewünschte Module (Anrufprotokoll, Telefonbuch-Sync, Click-to-Call) aktivieren und speichern.
6. Für Echtzeit-Anrufereignisse: Webhook-Geheimnis vergeben, speichern, dann über den Button **"Anrufprotokoll-Webhook registrieren"** den Webhook bei Webex anlegen.
7. Erster Testlauf über die Buttons **"Verbindung testen"**, **"Telefonbuch jetzt synchronisieren"** und **"Anrufprotokoll jetzt abrufen"**.

> Alternative für einen schnellen Einmal-Test ohne OAuth-Integration: Ein 12h-Personal-Access-Token von der Webex-Entwicklerdoku direkt ins Feld *Zugriffstoken* eintragen. Für den Dauerbetrieb ist aber der OAuth-Flow (Schritte 1–4) nötig, da nur so automatisch erneuert wird.

## Bekannte Einschränkungen

- Die Zuordnung Rufnummer → Kunde/Kontakt erfolgt über einen Vergleich der letzten Ziffern (tolerant gegenüber Formatierungsunterschieden), nicht über eine exakte E.164-Normalisierung. Bei Rufnummern-Duplikaten über mehrere Kunden hinweg kann es zu Fehlzuordnungen kommen.
- Das JSON-Schema der Webex-*Organization-Contacts*-API kann sich je Tenant/API-Version leicht unterscheiden. Bitte nach der Ersteinrichtung über **"Verbindung testen"** bzw. einen Testeintrag prüfen, ob die Felder (`displayName`, `phoneNumbers`, …) korrekt übernommen werden, und `tasks.py` (`_build_organization_contact_payload`) bei Abweichungen anpassen.
- Click-to-Call über die Call-Control-API nutzt aktuell ein gemeinsames Service-Token; der Anruf wird dadurch technisch vom Token-Besitzer aus aufgebaut, nicht individuell je Mitarbeiter. Für echtes Click-to-Call je Agent ist eine Erweiterung um Pro-Benutzer-OAuth nötig (siehe Roadmap).

## Roadmap
- [ ] Pro-Benutzer-OAuth-Anbindung, damit Click-to-Call und Anrufprotokoll korrekt dem jeweiligen Mitarbeiter zugeordnet werden.
- [ ] Screen-Pop / Desk-Benachrichtigung bei eingehendem Anruf in Echtzeit.
- [ ] ERPNext-Workspace mit Auswertungen (Anrufe pro Kunde/Mitarbeiter, Reaktionszeiten).

## Lizenz

[MIT](LICENSE)

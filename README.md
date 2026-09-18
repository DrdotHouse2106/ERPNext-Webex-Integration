# ERPNext Webex Integration

Eine [Frappe](https://frappeframework.com/)/[ERPNext](https://erpnext.com/)-App, die **Cisco Webex Calling** (bzw. Webex-Calling-basierte Anbieter wie Placetel) mit ERPNext verbindet.

> English version: [README.en.md](README.en.md)

## Funktionen

- **Anrufe dokumentieren**: Ein- und ausgehende Telefonate werden automatisch als *Webex Call Log*-Einträge in ERPNext angelegt und, sofern anhand der Rufnummer möglich, direkt einem **Kunden**, **Kontakt** oder **Lead** zugeordnet. Jeder Eintrag kann um eine Gesprächsnotiz ergänzt werden.
  - Über **Webhooks** (Echtzeit, Ressource `telephony_calls`) und/oder
  - über den regelmäßigen Abruf des **detaillierten Anrufprotokolls (CDR)** von Webex.
  - Beide Quellen werden über `callSessionId` (Webhook) bzw. `Correlation ID` (CDR) demselben echten Anruf zugeordnet und in einem Datensatz zusammengeführt – auch wenn z. B. mehrere Kollegen einer Hunt Group gleichzeitig geklingelt werden, entsteht kein doppelter Eintrag.
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
7. Erster Testlauf über die Buttons **"Verbindung testen"** und **"Anrufprotokoll jetzt abrufen"**.
8. **Vor dem ersten Telefonbuch-Sync** (Gruppe "Telefonbuch"): erst **"Aktuelle Einträge in Webex anzeigen"** (rein lesend, verändert nichts) prüfen, ob dort schon manuell angelegte Kontakte existieren, dann **"Vorschau (sendet nichts an Webex)"** kontrollieren, was der Sync anlegen/aktualisieren würde. Erst danach **"Jetzt synchronisieren"** tatsächlich ausführen. Wichtig: Das Organisationstelefonbuch ist **global für alle Webex-Nutzer** sichtbar, und die Zuordnung läuft über eine intern gespeicherte ID (`webex_contact_id`) – beim allerersten Sync wird daher jeder Kunde/Kontakt als **neuer** Eintrag angelegt, auch wenn in Webex bereits ein inhaltlich passender (aber nicht darüber verknüpfter) Kontakt existiert.

> Alternative für einen schnellen Einmal-Test ohne OAuth-Integration: Ein 12h-Personal-Access-Token von der Webex-Entwicklerdoku direkt ins Feld *Zugriffstoken* eintragen. Für den Dauerbetrieb ist aber der OAuth-Flow (Schritte 1–4) nötig, da nur so automatisch erneuert wird.

## Bekannte Einschränkungen

- Der CDR-Host (`analytics.webexapis.com`) ist regionsabhängig – bei EU-Organisationen antwortet Webex mit HTTP 451 und nennt darin die korrekte URL (z.B. `analytics-calling-eu.webexapis.com`). Diese wird beim nächsten Abruf automatisch erkannt und in den Einstellungen übernommen.

- Die Zuordnung Rufnummer → Kunde/Kontakt erfolgt über einen Vergleich der letzten Ziffern (tolerant gegenüber Formatierungsunterschieden), nicht über eine exakte E.164-Normalisierung. Bei Rufnummern-Duplikaten über mehrere Kunden hinweg kann es zu Fehlzuordnungen kommen.
- Die Webex-*Organization-Contacts*-API liegt unter `/contacts/organizations/{orgId}/contacts/...` (nicht unter `/v1/...`); `orgId` muss in Webex Settings hinterlegt sein. Das Lesen (`GET .../search`) ist gegen die offizielle Doku verifiziert; das JSON-Schema für Anlegen/Aktualisieren (`displayName`, `phoneNumbers`, …) ist dagegen noch nicht live bestätigt. Bitte nach der Ersteinrichtung über **"Vorschau"** und einen einzelnen Testeintrag prüfen und `tasks.py` (`_build_organization_contact_payload`) bei Abweichungen anpassen.
- Click-to-Call über die Call-Control-API nutzt aktuell ein gemeinsames Service-Token; der Anruf wird dadurch technisch vom Token-Besitzer aus aufgebaut, nicht individuell je Mitarbeiter. Für echtes Click-to-Call je Agent ist eine Erweiterung um Pro-Benutzer-OAuth nötig (siehe Roadmap).
- **Marken-Anrufer-ID** (DocType *Webex Brand Line*, Feld `customer_brand_fieldname` in Webex Settings): Setzt vor dem Anruf automatisch die zur Kunden-Marke passende Anrufer-ID über die Webex-API "Configure Caller ID Settings for a Person". Das JSON-Schema (`selected`/`customNumber`) ist aus der Webex-Dokumentation abgeleitet, aber nicht gegen jeden Tenant verifiziert – über den Button **"Anrufer-ID-Einstellungen anzeigen (Debug)"** in Webex Settings lässt sich das tatsächliche Format prüfen und `webex_client.py` (`set_caller_id`) bei Abweichungen anpassen. Funktioniert aktuell nur für den einen per OAuth verbundenen Benutzer (siehe Punkt oben).
  - Der Name der Marke bei Webex (z.B. Hunt-Group-Name) muss nicht identisch mit der Bezeichnung in ERPNext sein: Über das Feld **"Zugeordneter Datensatz in ERPNext"** an jeder *Webex Brand Line*-Zeile wählt man den passenden Datensatz gezielt aus (durchsuchbar, wie ein normales Link-Feld) statt sich auf exakt gleiche Schreibweise zu verlassen.
- Ist ein Kontakt über einen Dynamic Link mit einem Kunden verknüpft, werden seine Rufnummern in den **Telefonbuch-Eintrag des Kunden** eingerechnet, statt einen zweiten, separaten Eintrag für dieselbe Person anzulegen. Nur Kontakte ohne Kunden-Verknüpfung werden eigenständig synchronisiert.
- Die Telefonbuch-Synchronisation überträgt **alle** am Kunden/Kontakt hinterlegten Rufnummern (Mobil und Festnetz, falls beide gepflegt sind) als separate Einträge desselben Webex-Kontakts.
- Wird ein Kunde/Kontakt mit hinterlegtem `webex_contact_id` **gelöscht oder mit einem anderen zusammengeführt** (Merge), wird der zugehörige Webex-Telefonbuch-Eintrag automatisch entfernt (`on_trash`-Hook), damit keine verwaisten Einträge im organisationsweiten Telefonbuch zurückbleiben.

## Roadmap
- [ ] Pro-Benutzer-OAuth-Anbindung, damit Click-to-Call und Anrufprotokoll korrekt dem jeweiligen Mitarbeiter zugeordnet werden.
- [ ] Screen-Pop / Desk-Benachrichtigung bei eingehendem Anruf in Echtzeit.
- [ ] ERPNext-Workspace mit Auswertungen (Anrufe pro Kunde/Mitarbeiter, Reaktionszeiten).

## Lizenz

[MIT](LICENSE)

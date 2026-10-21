# Fremde Bestandteile

*[English version](THIRD_PARTY_NOTICES.en.md)*

Das Projekt selbst steht unter der MIT-Lizenz ([LICENSE](LICENSE)). Es liefert
aber auch fremden Code aus, im Repo und im Docker-Image. Für diesen gelten
weiter die Lizenzen seiner Urheber. Hier steht, was wo steckt und wo der
jeweilige Lizenztext liegt. Wer das Projekt oder das Image weitergibt, gibt
diese Texte mit.

## In der Weboberfläche

Diese Bibliotheken liegen fertig im Repo und werden von der Anwendung selbst
ausgeliefert. Geholt und gegen einen SRI-Hash geprüft werden sie
mit `server/bin/vendor.sh`, ihre Lizenztexte ebenso.

| Bibliothek | Version | Lizenz | Lizenztext |
| --- | --- | --- | --- |
| Bootstrap | 5.3.8 | MIT | `server/static/vendor/bootstrap/LICENSE.txt` |
| Popper (steckt in `bootstrap.bundle.min.js`) | 2.11.8 | MIT | `server/static/vendor/bootstrap/LICENSE-popper.txt` |
| Tom Select | 2.4.3 | Apache-2.0 | `server/static/vendor/tom-select/LICENSE.txt` |
| SortableJS | 1.15.6 | MIT | `server/static/vendor/sortablejs/LICENSE.txt` |

Tom Select bündelt außerdem `@orchidjs/sifter` und `@orchidjs/unicode-variants`.
Beide stehen ebenfalls unter Apache-2.0, und der Lizenztext ist derselbe. Keines
der Pakete bringt eine NOTICE-Datei mit.

**Geändert** ist an diesen Dateien nur eines: Der Verweis auf die Source-Map am
Dateiende ist durch einen Vermerk ersetzt, denn die Karten werden nicht
mitgeliefert. Der Vermerk steht in jeder betroffenen Datei.

## Im Code des Servers

Der Köder unter `/admin/` (`server/accounts/honeypot.py`) und das Modell
der Anmeldeversuche (`server/accounts/models.py`) gehen auf das Paket
django-admin-honeypot 1.2.0 zurück (MIT, Copyright (c) Derek Payton). Der
Code ist umgeschrieben und gekürzt, die Idee und die Tabelle bleiben. Der
Lizenztext liegt in `server/accounts/LICENSE-admin-honeypot.txt`.

## In der Zähl-App

Die Zähl-App wird aus npm-Paketen gebaut. Welche davon im ausgelieferten
JavaScript stecken, ermittelt der Build selbst (`app/bin/licenses.mjs`).
Er schreibt ihre Namen, Versionen und vollen Lizenztexte, samt NOTICE-Dateien,
in `third-party-licenses.txt` neben die App. Im Betrieb liegt sie
unter `/app/third-party-licenses.txt`. Fehlt einem Paket der Lizenztext, bricht der
Build ab.

Dazu gehören vor allem Dexie (Apache-2.0, mit NOTICE), Svelte und Workbox
(beide MIT). Die maßgebliche Liste ist die Datei, nicht dieser Absatz.

Die Pakete, die nur zum Bauen und Testen gebraucht werden (Vite, Vitest,
Prettier, Playwright …), werden nicht ausgeliefert. Ausnahme sind die kleinen
Helfer von Vite und `vite-plugin-pwa`, die im Bündel landen. Sie stehen deshalb
ebenfalls in der Datei.

## Im Docker-Image

- **Python-Pakete:** Jedes Paket bringt seinen Lizenztext in seinem
  `*.dist-info`-Ordner mit (`/app/.venv/lib/python3.*/site-packages/`). Die
  Lizenzen sind fast durchweg MIT, BSD oder Apache-2.0. Zwei Ausnahmen:
  - `psycopg` und seine C-Fassung `psycopg-c` stehen unter LGPL-3.0. Beide
    werden unverändert als eigene Pakete installiert und lassen sich gegen eine
    andere Fassung tauschen, wie die LGPL es verlangt. Die C-Fassung wird beim
    Bauen des Image aus ihrem Quelltext übersetzt und bündelt nichts. Die
    Bibliothek libpq, über die beide mit PostgreSQL sprechen, kommt aus Debian
    (siehe unten).
  - `pyphen` (die Silbentrennung von WeasyPrint) steht zur Wahl unter GPL-2.0+,
    LGPL-2.1+ oder MPL-1.1. Hier gilt die **MPL-1.1**. Auch pyphen wird
    unverändert als eigenes Paket installiert. Seine Wörterbücher tragen eigene
    Lizenzvermerke in `pyphen/dictionaries/`.
- **Systempakete** aus dem Basis-Image und aus `apt` (Pango, HarfBuzz, libpq, die
  Schrift DejaVu): Ihre Lizenztexte liegen unter `/usr/share/doc/<paket>/copyright`.
- **Statische Dateien fremder Pakete:** `collectstatic` sammelt beim Bauen auch,
  was Django und das Django REST Framework mitbringen, darunter für dessen
  durchklickbare Oberfläche Bootstrap 3 (MIT), die Glyphicons Halflings (nur mit
  Bootstrap 3 nutzbar) und Font Awesome (SIL OFL 1.1 für die Schrift, MIT für
  das CSS). Im Betrieb ist diese Oberfläche abgeschaltet, die Dateien liegen
  aber unter `/app/staticfiles/` bei. Ihre Lizenztexte stehen in den Paketen
  unter `site-packages/`.
- Diese Datei, ihre englische Fassung und `LICENSE` liegen im Image unter `/app/`.

## Marken

„Compeso“ und „WinTICKET“ sind Marken ihrer jeweiligen Inhaber. Dieses Projekt
steht in keiner Verbindung zu ihnen.

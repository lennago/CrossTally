# Deployment

*[English version](DEPLOYMENT.en.md)*

Der kurze Weg zu einer laufenden Version steht in der README
([„Betrieb mit Docker“](README.md#betrieb-mit-docker)). Dieser Text ist das Nachschlagewerk dazu. Vorn steht der
Betrieb in der Reihenfolge, in der man ihn braucht: was läuft, was der Rechner
braucht, das erste Einrichten, Backups, Aktualisieren, Betreiber und
Datenschutz. Hinten steht, was nur für Forks und Mitwirkende zählt. Zum
Entwickeln braucht es das alles nicht. Dort bleibt es bei uv, SQLite
und `runserver`.

## Was läuft

```text
compose.yaml
├── db      PostgreSQL 18, eigenes Volume, Zustandsprüfung
├── web     dieses Projekt als ein Image: Django + die gebaute Zähl-App
└── proxy   Caddy, macht TLS und sonst nichts
```

**Ein Image, vier Stufen** (`Dockerfile`): Node baut die PWA, eine zweite
Stufe die Python-Umgebung, eine dritte stellt die Dateien der Anwendung
zusammen, und die letzte nimmt nur die Ergebnisse mit. Node und der Compiler
bleiben zurück. Die App ist kein eigener Dienst, sondern ein Satz Dateien. Ein
zweites Image wäre eine zweite Wahrheit über denselben Build. Die Anwendung
läuft als Benutzer mit der uid 1000. Ihren Code und ihre Pakete kann sie lesen,
aber nicht ändern.

**Mit PostgreSQL spricht psycopg in seiner C-Fassung**, gebaut gegen das libpq
des Systems. Mit der reinen Python-Fassung brauchten Seiten, die viel lesen, bis
zu doppelt so lang. Fehlt die C-Fassung, bricht der Build ab.

**Die Dateien liefert Django selbst aus**, über WhiteNoise (`wsgi.py`): die
statischen Dateien unter `/static/` und die Zähl-App unter `/app/`. Damit muss
der Proxy kein Verzeichnis mit der Anwendung teilen. Der Pfad `/app/` ist
Pflicht, denn ein Service Worker steuert nur den Bereich, unter dem er selbst
liegt.

**Der Proxy begrenzt die Größe eines Request-Bodys** auf 12 MB
(`request_body` im `Caddyfile`). Sonst füllt ein übergroßer Upload die Platte,
denn Djangos eigene Grenzen lassen die Dateianteile ungebremst. Die 12 MB
liegen über dem größten erlaubten Upload (10 MB, `MAX_UPLOAD_MB`
in `server/django_project/validation.py`). Wer den in einem Fork hochsetzt,
zieht die Grenze im `Caddyfile` mit.

**Die Lizenzen reisen mit.** Das Image enthält unter `/app/` die
eigene `LICENSE` und `THIRD_PARTY_NOTICES.md` in beiden Sprachen, dazu die Lizenztexte aller
fremden Bestandteile: die der Bibliotheken unter `static/vendor/`, die der
Zähl-App in `pwa/third-party-licenses.txt` (schreibt der Build, im Browser
unter der Adresse `/app/third-party-licenses.txt`) und die der Python-Pakete
in ihren `*.dist-info`-Ordnern.

Die Protokolle der drei Dienste sammelt Docker als Dateien
unter `/var/lib/docker/containers/`. Die `compose.yaml` begrenzt sie auf fünf
Dateien von je 20 MB je Dienst, sonst wüchsen sie über Monate auf Gigabyte, mit
Adressen und Pfaden darin. `docker compose logs web` zeigt damit die letzten
Tage.

## Was der Rechner braucht

Die Last ist klein. Es zählen wenige Geräte zugleich, und ihre Anfragen sind
kurz. Rechenzeit kosten die Auswertungen, die PDFs und große Importe.

| | Mindestens | Bequem |
| --- | --- | --- |
| Prozessor | 2 Kerne, x86-64 | 4 Kerne, x86-64 |
| Arbeitsspeicher | 2 GB | 4 GB |
| Platte | SSD, 20 GB | SSD, 32 GB |

Ein gebrauchter Büro-PC oder ein Mini-PC genügt. Erprobt ist der Stapel nur
auf x86-64.

**Arbeitsspeicher:** Im Leerlauf belegen die drei Dienste zusammen rund 500 MB,
fast alles davon der Anwendungsserver mit seinen vier Arbeitern. Ein Arbeiter
braucht frisch rund 70 MB. PDFs und große Importpläne lassen ihn deutlich
wachsen, ein Importplan mit 600 Zeilen in einer Messung um rund 170 MB. Diesen
Speicher gibt er nicht wieder her. Deshalb startet gunicorn jeden Arbeiter nach
rund 500 Anfragen neu, zwischen zwei Anfragen, ohne dass jemand etwas merkt.
Eng wird es auf dem kleinsten Rechner, wenn mehrere Arbeiter zugleich große
Importe oder PDFs bearbeiten. Dann senkt `GUNICORN_WORKERS=2` den Bedarf, und
die Zähl-App wartet, solange zwei solche Anfragen laufen.

**Prozessor:** Die Zeit einer Seite vergeht in Python und nicht in der
Datenbank. Es zählt deshalb, wie schnell der einzelne Kern ist. Mehr Kerne
helfen erst, wenn mehrere Führungskräfte zugleich auswerten.

**Platte:** Die drei Images belegen rund 0,9 GB, und nach einem Update liegt
das alte noch daneben. Datenbank und Backups fallen dagegen kaum ins Gewicht.
Wichtiger als die Größe ist die Art: PostgreSQL schreibt oft und in kleinen
Stücken, deshalb eine SSD.

**Das Image wird nur für amd64 gebaut.** Ein ARM-Rechner wie ein Raspberry Pi
braucht ein selbst gebautes Image (siehe „Selbst bauen“), ein 64-bittiges
System, mindestens 4 GB Arbeitsspeicher und eine SSD. Auf der SD-Karte gibt
Postgres unter den vielen kleinen Schreibvorgängen auf.

## Erstes Mal einrichten

### Voraussetzungen

- **Ein Rechner** wie oben, mit Linux, Docker und dem Compose-Plugin
  (Version 2, also `docker compose`).
- **Ein Name und ein Zertifikat.** Mit einer eigenen Domain holt Caddy das
  Zertifikat selbst, über das ACME-Verfahren, etwa bei Let's Encrypt. Dafür
  zeigt ein DNS-Eintrag (A oder AAAA) auf die öffentliche Adresse des Rechners,
  und die Ports 80 und 443 sind aus dem Internet erreichbar: in der Firewall
  offen und im Router weitergeleitet. Steht der Rechner nur im internen Netz,
  siehe „Das Zertifikat“.
- **Ein Mailserver** ist freiwillig. Ohne ihn bekommt ein neues Konto gleich ein
  Passwort, und „Passwort vergessen?“ fehlt (siehe „Mails“).

### Schritte

```bash
git clone https://github.com/lennago/crosstally.git && cd crosstally
cp .env.example .env                      # ausfüllen, siehe „Alle Werte der .env“
mkdir -p backups && sudo chown 1000:1000 backups
docker compose pull
docker compose up -d --no-build
docker compose exec web python manage.py createsuperuser
```

Aus dem Repo gebraucht werden nur `compose.yaml`, `Caddyfile` und das
Backup-Skript. Das Image kommt fertig aus der Registry. `--no-build` hält
compose davon ab, nach der Bauanleitung in der `compose.yaml` selbst zu bauen
(siehe „Selbst bauen“). In `VERSION` nennt die Vorlage die jüngste Version.
Welche Werte es sonst gibt, steht unter „Aktualisieren“.

Der Ordner `backups` gehört dem Benutzer des Containers (uid 1000). Legt compose
ihn beim ersten Start selbst an, gehört er `root`, und der Knopf „Backup
erstellen“ scheitert am Schreibrecht. Deshalb wird er vor dem ersten Start
angelegt.

Die Datenbank wird bei jedem Start migriert (`bin/start.sh`). Das erste Konto
ist ein Administrator, alle weiteren entstehen in der Oberfläche. Die
Verwaltung liegt unter dem Pfad aus `ADMIN_URL`. Ohne Anmeldung antwortet er
mit 404. Erst auf der Website anmelden, dann führt im Kontomenü der Eintrag
„Verwaltung“ dorthin. Ihn sehen nur Administratoren.

### Das Zertifikat

**Ohne HTTPS gibt es keine Zähl-App.** Service Worker laufen nur im sicheren
Kontext, also auch keine Installation und kein Offlinebetrieb. `SITE_ADDRESS`
entscheidet, wer TLS macht:

| Lage | `SITE_ADDRESS` | Was TLS macht |
| --- | --- | --- |
| Rechner mit öffentlichem Namen | `crosstally.example.de` | Caddy holt das Zertifikat selbst |
| Ein Proxy davor, der schon ein Zertifikat hat | `:80` | der Proxy, etwa `tailscale serve` |

Steht der Rechner nur im internen Netz, erreicht ihn keine öffentliche
Zertifizierungsstelle, und Caddy bekommt für seinen Namen kein Zertifikat. Drei
Wege führen trotzdem zu HTTPS:

- **Ein Proxy davor, der schon ein Zertifikat hat.** Das kann der Reverse Proxy
  des Hauses sein oder [Tailscale](https://tailscale.com/kb/1312/serve), das
  einem Rechner im eigenen Tailnet ein echtes Zertifikat ausstellt (in der
  Adminkonsole müssen dafür die HTTPS-Zertifikate freigeschaltet sein). Dann
  gilt `SITE_ADDRESS=:80`, dazu die drei Werte für einen Proxy davor (siehe
  unten und „Was leicht schiefgeht“).
- **Eine öffentliche Domain mit der DNS-Prüfung.** Die Zertifizierungsstelle
  prüft dann einen Eintrag im DNS statt einer Verbindung zum Rechner. Das
  braucht ein Caddy mit dem Modul des DNS-Anbieters, also ein eigenes Image für
  den Dienst `proxy`.
- **Die eigene Zertifizierungsstelle von Caddy**, mit `tls internal` im
  Caddyfile. Dann muss jedes Gerät, auch jedes Handy, dem Wurzelzertifikat von
  Caddy vertrauen. Es liegt im Container
  unter `/data/caddy/pki/authorities/local/root.crt`. Auf Handys ist das
  mühsam, und die Änderung am Caddyfile muss bei jedem `git pull` mitkommen.

Mit Tailscale auf demselben Rechner sieht das so aus:

```bash
tailscale serve --bg --https=443 http://localhost:80
tailscale serve status     # zeigt die Adresse
```

Dazu in die `.env`: `SITE_ADDRESS=:80`, `LISTEN_ADDRESS=127.0.0.1`,
`TRUSTED_PROXIES=172.16.0.0/12` und `PROXY_COUNT=1`, außerdem der Tailnet-Name
in `EXTRA_ALLOWED_HOSTS` und `CSRF_TRUSTED_ORIGINS`. Die Datei wird beim Start
gelesen, wirksam wird sie also erst mit `docker compose up -d`.

### Ein eigener Proxy statt Caddy

Wer schon einen Proxy hat (Traefik, nginx …), nimmt den Dienst `proxy` aus
der `compose.yaml` und hängt Port 8000 des Dienstes `web` dahinter. Den Port
veröffentlicht `web` nicht. Läuft der Proxy auf dem Rechner selbst, bekommt der
Dienst dafür die Zeile `ports: ["127.0.0.1:8000:8000"]`. Ein Proxy in Docker
kommt stattdessen ins Netz von compose. Der Proxy setzt `X-Forwarded-Proto` und
in `X-Forwarded-For` die Adresse des Aufrufers. `BEHIND_TLS_PROXY=true` bleibt,
und `PROXY_COUNT` zählt die Stationen vor dem eigenen Proxy, 0, wenn er direkt
aus dem Netz erreicht wird.

### Alle Werte der `.env`

Gemeint ist die `.env` neben der `compose.yaml`. Die Vorlage `.env.example`
nennt jeden Wert mit einer kurzen Erklärung, und was auskommentiert ist, hat
eine brauchbare Vorgabe. Zum Entwickeln gibt es eine eigene, `server/.env`
aus `server/.env.example`. Die beiden haben nichts miteinander zu tun.

| Variable | Pflicht | Vorgabe | Wofür |
| --- | --- | --- | --- |
| `SECRET_KEY` | ja | | Signiert Sitzungen und Tokens. Lang und zufällig. |
| `BACKUP_KEY` | ja | | Der Schlüssel, mit dem jedes Backup verschlüsselt wird. Mindestens 32 Zeichen, siehe „Backups“. |
| `ADMIN_URL` | ja | | Pfad der Verwaltung, ein eigener mit `/` am Ende. Leer, mit `/` am Anfang oder ohne `/` am Ende, der Platzhalter der Vorlage und ein Pfad unter oder über einem, der ohne Anmeldung offen ist (`admin/`, `accounts/`, `api/` …), halten den Start an. Ohne Anmeldung antwortet der Pfad mit 404. Unter `/admin/` liegt nur der Köder. |
| `POSTGRES_PASSWORD` | ja | | Passwort der Datenbank im Compose-Netz, lang und zufällig wie die beiden darüber. Die Datenbank übernimmt es einmal, beim ersten Start. Wer es später ändert, ändert es dort mit `ALTER USER` mit. |
| `POSTGRES_DB`, `POSTGRES_USER` | | `crosstally` | Name der Datenbank und ihres Benutzers. |
| `SITE_ADDRESS` | ja | `:80` | Was Caddy bedient: die eigene Domain. Die Vorgabe `:80` gilt nur mit einem Proxy davor, siehe „Das Zertifikat“. |
| `EXTRA_ALLOWED_HOSTS` | ja | | Der Name, unter dem die Anwendung aufgerufen wird. Mehrere mit Komma. |
| `CSRF_TRUSTED_ORIGINS` | empfohlen | | Dieselbe Adresse mit `https://` davor. Hinter Caddy vergleicht Django die Herkunft eines Formulars mit der eigenen Adresse, Formulare gehen also auch ohne. Nötig wird der Eintrag, wenn ein Proxy unterwegs Schema oder Namen ändert. Mehrere mit Komma. |
| `BEHIND_TLS_PROXY` | | `false` | `true`, sobald ein Proxy TLS macht, mit Caddy also immer. Die Vorlage setzt es. |
| `DEBUG` | | `False` | Im Betrieb immer `False`. |
| `VERSION` | | `latest` | Welches Image läuft. Die Vorlage nennt die jüngste Version, siehe „Aktualisieren“. |
| `APP_VERSION` | | | Nur beim Bauen auf dem Rechner selbst, und nur für einen eigenen Namen der Fassung im Kontomenü. Ohne Eintrag nimmt der Build den Stand des Checkouts (`lokal-1a2b3c4`). Zur Laufzeit gilt die Nummer, die das Image selbst trägt. Ein Eintrag hier verdeckt sie nicht. |
| `GUNICORN_WORKERS` | | `4` | Arbeiter des Anwendungsservers. Jeder bedient eine Anfrage zur Zeit, siehe „Was der Rechner braucht“. |
| `LISTEN_ADDRESS` | | | Auf welcher Adresse des Rechners Caddy hört. Leer heißt auf allen. Mit einem Proxy **vor** Caddy auf demselben Rechner `127.0.0.1`. |
| `PROXY_COUNT` | | `0` | Stationen **vor** Caddy, siehe „Was leicht schiefgeht“. |
| `TRUSTED_PROXIES` | | | Wem Caddy die Kopfzeilen `X-Forwarded-*` glaubt. Leer heißt niemandem. Mehrere mit Leerzeichen. Nur mit einem Proxy **vor** Caddy, siehe „Was leicht schiefgeht“. |
| `HSTS_SECONDS` | | `300` | Wie lange der Browser nur HTTPS zulässt. Zum Anfangen kurz, später ein Jahr (`31536000`). |
| `CONN_MAX_AGE` | | `60` | Sekunden, die eine Verbindung zur Datenbank offen bleibt. |
| `LANGUAGE_CODE` | | `de` | Sprache des Servers und seiner Adressen, `de` oder `en`. Siehe „Sprache und Zeit“. |
| `TIME_ZONE` | | `Europe/Berlin` | Zeitzone der Anzeige, ein Name aus der IANA-Liste. |
| `EMAIL_HOST` | | | SMTP-Server für Einladungen, „Passwort vergessen?“, die Bestätigung einer neuen E-Mail-Adresse und die Benachrichtigungen bei Änderungen am Konto. Leer: Es geht keine Mail hinaus, siehe „Mails“. |
| `EMAIL_PORT` | | `587` | Mit `EMAIL_USE_SSL=true` ist die Vorgabe `465`. |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | | | Anmeldung am SMTP-Server. |
| `EMAIL_USE_TLS` | | `true` | STARTTLS, der übliche Weg auf Port 587. |
| `EMAIL_USE_SSL` | | `false` | Implizites TLS auf Port 465. Schaltet STARTTLS von selbst ab. |
| `DEFAULT_FROM_EMAIL` | mit `EMAIL_HOST` | `webmaster@localhost` | Absender der Mails. Ohne ihn startet die Anwendung mit Server nicht. |

Die Verbindung zur Datenbank und `BACKUP_DIR` setzt die `compose.yaml` selbst.
In die `.env` gehören sie nicht.

### Mails

**E-Mail** geht nur über SMTP hinaus, wenn `DEBUG` aus ist und `EMAIL_HOST`
einen Server nennt. Sonst schreibt die Anwendung die Mail auf die
Standardausgabe, also ins Log des Containers (`docker compose logs web`). Wer
die Mail sehen will, wie sie ankommt, nimmt Mailpit (siehe „Zum Ausprobieren
ohne Domain“). Ob ein eingetragener Server annimmt, zeigt ein Testversand:

```bash
docker compose exec web python manage.py sendtestemail du@example.de
```

**Ohne Server** verschwindet von den Seiten, was eine Mail verspricht:
„Passwort vergessen?“ (die Adresse antwortet mit 404), die Einladung und
„Einladung erneut senden“. Ein neues Konto bekommt sein erstes Passwort gleich
beim Anlegen. Ein vergessenes setzt ein Administrator in der Verwaltung neu
(Konto öffnen, „Passwort zurücksetzen“).

**Mit Server** ist die Einladung der Normalfall: Die Person bekommt einen Link
und setzt ihr Passwort selbst. Jede Person erfährt per Mail, wenn ihr Konto
geändert, deaktiviert oder gelöscht, ihr Passwort neu gesetzt oder ein Passkey
angelegt oder entfernt wird. Eine neue E-Mail-Adresse gilt erst, wenn die Person
sie über den Link in der Mail bestätigt hat. Je E-Mail-Adresse und je Adresse,
von der die Anfrage kommt, sind fünf Anfragen „Passwort vergessen?“ in der
Stunde möglich. Antwortet ein eingetragener Server nicht, gilt die Änderung
trotzdem, und die Einladung lässt sich auf der Kontoseite erneut senden.

### Die Texte der Mails

Was in den Mails steht, legt ein Administrator in der Verwaltung fest,
unter **Organisation → Mailtexte**: je Mail Betreff und Text, auf Deutsch und
Englisch. Ein leeres Feld heißt: der mitgelieferte Text, mit seinen
Verbesserungen beim nächsten Update. Welche Platzhalter eine Mail kennt
(etwa `{{ link }}`), steht auf ihrer Seite, darunter eine Vorschau. Je Mail
entscheidet der Haken **nennt, wer es ausgelöst hat**, ob der Name der Person in
der Mail steht, die das Konto angelegt, geändert oder gelöscht hat. Das Backup
nimmt die Texte mit.

## Zum Ausprobieren ohne Domain

Auf dem eigenen Rechner genügt Docker (unter Windows Docker Desktop):

```bash
cp .env.example .env     # ausfüllen wie unter „Alle Werte der .env“, dazu
                         # SITE_ADDRESS=localhost
docker compose pull && docker compose up -d --no-build
```

Die Weboberfläche läuft damit unter `https://localhost`. Das Zertifikat stellt
Caddy sich dafür selbst aus, der Browser warnt deshalb beim ersten Aufruf. Die
Zähl-App auf dem Handy braucht ein Zertifikat, dem das Handy traut, etwa über
Tailscale (siehe „Das Zertifikat“).

**Mails ansehen, ohne dass etwas hinausgeht:** [Mailpit](https://mailpit.axllent.org/)
nimmt als SMTP-Server jede Mail an, verschickt keine und zeigt sie im Browser,
so wie ein Mailprogramm sie darstellt. Es kommt in das Netz, das compose für
die Dienste anlegt. Das heißt wie der Ordner mit der `compose.yaml`,
dahinter `_default` (`docker network ls` zeigt es):

```bash
docker run -d --rm --name mailpit --network <ordner>_default \
  -p 127.0.0.1:8025:8025 axllent/mailpit
```

Dazu in die `.env`:

```bash
EMAIL_HOST=mailpit
EMAIL_PORT=1025
EMAIL_USE_TLS=false
DEFAULT_FROM_EMAIL=absender@example.de
```

Nach `docker compose up -d` stehen die Mails unter `http://localhost:8025`,
auch die von „Passwort vergessen?“ samt dem Link darin. Ob ein echter Server
sie annimmt und ob sie im Spam landen, zeigt Mailpit nicht. Dafür braucht es
einen echten Versand, und oft genügt dafür das eigene Postfach mit SMTP-Zugang.

## Backups

Es gibt sie **zweimal**, und das mit Absicht:

| | `bin/backup.sh` | Knopf in der Verwaltung |
| --- | --- | --- |
| Was | Abbild der Datenbank (`pg_dump`, `.sql.gz.enc`) | Datenstand als JSON (`dumpdata`, `.json.gz.enc`) |
| Wer | cron oder Hand, auf dem Rechner selbst | ein Administrator im Browser |
| Wozu | der Rückweg, wenn die Datenbank kaputt ist | der Rückweg, wenn die **Daten** falsch sind |

Beide schreiben nach `./backups`, beide behalten die sieben jüngsten Stände.
Nach einem Einspielen über die Verwaltung liegt dazu das Backup von vorher,
als Rückweg. Ein hochgeladenes Logo liegt in der Datenbank und ist in beiden
Ständen dabei.

### Jedes Backup ist verschlüsselt

Ein Backup enthält die Konten mit ihren Passwort-Hashes und alles, was die
Häuser gezählt haben. Es wird kopiert und auf anderen Rechnern aufgehoben, und
dort passt niemand mehr darauf auf. Verschlüsselt wird deshalb jedes, und zwar
mit dem `BACKUP_KEY` aus der `.env`:

```bash
openssl rand -base64 48      # das Ergebnis als BACKUP_KEY in die .env
```

- **Ohne den Schlüssel startet die Anwendung nicht**, ebenso wenig mit einem,
  der kürzer ist als 32 Zeichen. Nur das Backup zu verweigern wäre leiser: ein
  nächtlicher Lauf, der scheitert, fällt an dem Tag auf, an dem das Backup
  gebraucht wird.
- **Der Schlüssel gehört auch an einen Ort außerhalb dieses Rechners.** Geht er
  verloren, ist kein Backup mehr lesbar, und niemand kann das ändern. Wer die
  Backups auf einen anderen Rechner kopiert, legt den Schlüssel nicht daneben.
- **Ein neuer Schlüssel liest die alten Backups nicht.** Wer ihn wechselt, hebt
  den alten auf, bis die Backups von davor herausgealtert sind.
- **Verschlüsselt ist nicht signiert.** Ein falscher Schlüssel und eine
  beschädigte Datei fallen auf, denn im Inneren steckt gzip mit seiner
  Prüfsumme. Wer in den Ordner schreiben darf, könnte aber einen Stand gegen
  einen älteren tauschen. Eine eigene Datei ohne den Schlüssel nimmt das
  Einspielen dagegen nicht an (siehe „Zurückspielen“).

Das Format ist das von OpenSSL: erst gzip, dann AES-256 mit einem Schlüssel,
den PBKDF2 aus dem `BACKUP_KEY` ableitet. Ein Backup lässt sich damit auch
lesen, wenn es die Anwendung nicht mehr gibt, auf jedem Rechner mit OpenSSL:

```bash
export BACKUP_KEY='…'        # nicht als Argument: das läse jeder auf dem Rechner mit
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -pass env:BACKUP_KEY \
    -in backups/stand_<zeit>.json.gz.enc | gunzip > stand.json
```

### Das Abbild der Datenbank

```bash
./server/bin/backup.sh       # das Skript wechselt selbst in den Ordner mit der compose.yaml
```

Täglich läuft es am einfachsten über die crontab von `root` (`sudo crontab -e`),
mit dem Pfad, in dem das Repo liegt:

```cron
15 3 * * * /opt/crosstally/server/bin/backup.sh
```

`BEHALTEN=14 ./server/bin/backup.sh` behält vierzehn Abbilder statt sieben,
und `ZIEL=/pfad ./server/bin/backup.sh` schreibt in einen anderen Ordner.
Wer `BEHALTEN` ändert, passt den Satz zu den sieben jüngsten Backups im
Datenschutztext an (Verwaltung, „Erscheinungsbild und Betreiber“).

Vor dem Abbild räumt das Skript auf: `manage.py cleanup` löscht abgelaufene
Sitzungen und Anmeldetoken der Zähl-App, Fehlversuche, sobald die halbe Stunde
der Sperre um ist, die Anfragen „Passwort vergessen?“ und die Versuche am Köder
unter `/admin/` nach 30 Tagen und das Protokoll der Verwaltung nach einem Jahr.
All das sammelt sich sonst ohne Ende. Wer das Skript nicht täglich laufen lässt,
ruft den Befehl selbst auf (`docker compose exec web python manage.py cleanup`).
Die Fristen stehen auch im Datenschutztext: Der Text hängt an diesem Lauf.

`pg_dump` läuft im Container `db`, denn seine Version muss zur Datenbank
passen. Verschlüsselt wird in einem eigenen Container aus dem Image von `web`,
und der Schlüssel verlässt ihn dabei nicht. `web` selbst muss dafür nicht
laufen: Das Abbild entsteht auch, wenn die Anwendung steht oder nach einer
gescheiterten Migration nicht startet, und gerade dann braucht man es. Bricht
der Dump ab oder fehlt der Schlüssel, bleibt keine Datei zurück. Wer das Skript
aufruft, muss in den Ordner schreiben dürfen: `root` oder der Benutzer, dem der
Ordner gehört. Das Abbild ist nur für ihn lesbar.

### Außer Haus

Backups auf derselben Platte wie die Datenbank helfen nicht, wenn die Platte
ausfällt oder der Rechner verloren geht. Eine Kopie gehört deshalb regelmäßig
auf einen anderen Rechner, etwa gleich nach dem nächtlichen Abbild:

```bash
rsync -a /opt/crosstally/backups/ sicherung@nas.example.de:crosstally/
```

Die Dateien sind verschlüsselt und dürfen auf fremdem Speicher liegen. Der
Schlüssel gehört nicht daneben, sondern an einen dritten Ort, etwa in einen
Passwortmanager. Dorthin gehört auch eine Kopie der `.env`, denn in ihr
stehen `BACKUP_KEY` und `POSTGRES_PASSWORD`, und ohne sie lässt sich der Betrieb
auf einem neuen Rechner nicht wieder aufsetzen.

### Zurückspielen

**Ein JSON-Stand über die Verwaltung**, wenn die Daten falsch sind und der
Rechner heil ist: In der Verwaltung unter den Backups den Stand wählen und mit
Tippbestätigung einspielen. Die Verwaltung sichert vorher den jetzigen Stand.
Dabei gilt:

- **Nicht, solange eine Zählung läuft.** Die Zähl-Apps halten Zeilen des
  jetzigen Stands. Nach dem Einspielen könnten sie Gezähltes nicht mehr
  zuordnen. Erst abschließen oder zurück in die Vorbereitung. Über die Konsole
  (`loaddata`) greift die Sperre nicht. Das ist der Weg fürs Entwickeln.
- **Danach ist jeder abgemeldet.** Die Sitzungen der Weboberfläche liegen in der
  Datenbank und sind danach weg. Jedes Erneuerungstoken der Zähl-App, das vorher
  galt, steht danach auf der Sperrliste, auch eines, das der Stand nicht kennt.
  Das Zugangstoken läuft binnen einer Stunde aus, dann fragt die App nach der
  Anmeldung. Was dort noch nicht gesendet ist, bleibt liegen und geht nach der
  Anmeldung raus. Die Skripte unter `server/bin/` melden ebenso jeden ab,
  sobald der Stand Konten trägt.
- **Konten, Passwörter und Passkeys sind die aus dem Stand.** Wer seither ein
  Passwort geändert, einen Passkey entfernt oder ein Konto deaktiviert hat, tut
  das noch einmal. Sonst meldet sich etwa ein verlorenes Gerät mit seinem alten
  Passkey wieder an.
- **Ein älterer Stand wird nachgezogen.** Felder, die es nicht mehr gibt, werden
  übergangen (`loaddata --ignorenonexistent`). Was eine Migration seither
  umgestellt hat, etwa die Inventurarten an den Hausartikeln oder die Einheit an
  den Ergebnissen, schreibt das Einspielen nach denselben Regeln um. Das gilt in
  der Verwaltung wie in `manage.py backup read` und den Skripten
  unter `server/bin/`. Die Datei selbst bleibt, wie sie ist. Hat ein Haus darin
  zwei Inventuren am selben Tag, lässt sich der Stand nicht einspielen, denn je
  Haus und Tag gibt es nur eine.
- **Ein Stand, der ein Modell nennt, das es nicht mehr gibt, lässt sich nicht
  einspielen.** Die Verwaltung meldet es und lässt die Datenbank unverändert.
  So ein Stand ist Archiv: Er lässt sich nur mit dem Code seiner Zeit lesen.
- **Ein unverschlüsselter Stand wird nicht eingespielt**, solange ein Schlüssel
  gesetzt ist, und im Betrieb ist immer einer gesetzt. So eine Datei kann
  jeder hingelegt haben, der in den Ordner schreiben darf, samt eigener Konten.
  Ein eigener alter Stand kommt über den Schlüssel herein, siehe unten.
- **Ein Stand liegt beim Einspielen ganz im Speicher**, in etwa vierfacher
  Größe. Erst ab einigen hundert MB bräuchte der Container mehr als die 2 GB
  aus „Was der Rechner braucht“.

**Ein eigener unverschlüsselter Stand**, etwa aus der Entwicklung
ohne `BACKUP_KEY`, wird vorher mit dem Schlüssel verschlüsselt. Danach steht er
in der Verwaltung wie jeder andere zum Einspielen bereit:

```bash
docker compose exec web sh -c \
    'python manage.py backup store /backups/stand_<zeit> < /backups/stand_<zeit>.json'
docker compose exec web rm /backups/stand_<zeit>.json
```

`backup store` schreibt den Stand mit dem `BACKUP_KEY` verschlüsselt daneben,
als `stand_<zeit>.json.gz.enc`. Die zweite Zeile nimmt die unverschlüsselte
Datei weg. Auf der Konsole gibt `manage.py backup read` eine unverschlüsselte
Datei nur mit `--plaintext` aus, und die Skripte unter `server/bin/` nehmen
dasselbe Wort vor dem Pfad. Damit sagt, wer den Rechner bedient, dass die Datei
die eigene ist.

**Das Abbild mit `psql`**, wenn die Datenbank kaputt ist oder als Rückweg nach
einem Update:

```bash
docker compose stop web
docker compose exec db dropdb -U crosstally --force crosstally
docker compose exec db createdb -U crosstally crosstally
docker compose run -d --no-deps --name crosstally-einspielen --entrypoint sleep web infinity
docker exec -i crosstally-einspielen python manage.py backup decrypt \
    < backups/crosstally-<stand>.sql.gz.enc |
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U crosstally crosstally
docker exec crosstally-einspielen python manage.py sign_out_everyone
docker rm -f crosstally-einspielen
docker compose up -d --no-build
```

Entschlüsselt wird in einem eigenen Container aus dem Image von `web`,
denn `web` steht in dem Moment. Der Container selbst tut nichts (`sleep`), die
Befehle laufen mit `docker exec` darin. Was ein Container
mit `docker compose run` ausgibt, schreibt Docker in sein Log, und dort stünde sonst
das ganze Abbild im Klartext. Was ein Befehl mit `docker exec` ausgibt, geht nur
in die Leitung zu `psql`. `backup decrypt` gibt erst weiter, wenn das Abbild
ganz und der Schlüssel der richtige ist. Ein halbes Abbild käme sonst schon in
der Datenbank an. `ON_ERROR_STOP` hält beim ersten Fehler an, sonst liefe `psql`
weiter, und der Fehler stünde irgendwo in Hunderten Zeilen Ausgabe. Was seit dem
Backup gezählt oder gepflegt wurde, ist danach weg. Wer andere Werte
für `POSTGRES_USER` oder `POSTGRES_DB` gesetzt hat, setzt sie hier ein.

**Danach ist jeder abgemeldet** (`sign_out_everyone`). Das Abbild trägt die
Sitzungen der Website und die Anmeldungen der Zähl-App von seinem Zeitpunkt, und
sie gälten sonst wieder, auch was seither ausgesperrt wurde. Der Befehl beendet
jede Sitzung und sperrt jedes Erneuerungstoken aus dem Abbild. Ein Gerät, das
sich erst nach dem Abbild angemeldet oder seine Anmeldung erneuert hat, steht
nicht darin, und sein Token kennt der Server nicht. Es muss sich beim nächsten
Erneuern neu anmelden, spätestens nach einer Stunde. Konten, Passwörter und
Passkeys sind die aus dem Abbild: Wer seither ein Passwort geändert, einen
Passkey entfernt oder ein Konto deaktiviert hat, tut das noch einmal.

**Auf einem neuen Rechner**, wenn der alte weg ist:

1. Den Rechner einrichten wie unter „Erstes Mal einrichten“, aber mit der
   alten `.env`. Sie trägt `BACKUP_KEY`, `POSTGRES_PASSWORD` und die `VERSION`, die
   zuletzt lief.
2. Die Backups in den Ordner `backups` kopieren und ihn dem Benutzer des
   Containers geben (`sudo chown -R 1000:1000 backups`).
3. Statt `createsuperuser` das jüngste Abbild einspielen wie oben. Die Konten
   kommen mit.
4. Den Namen im DNS auf den neuen Rechner zeigen lassen. Das Zertifikat holt
   Caddy neu, und die Passkeys gelten weiter, solange der Name gleich bleibt.

## Aktualisieren

Auf dem Server, wo das Image aus der Registry kommt:

```bash
./server/bin/backup.sh           # vorher ein Backup
git pull                         # holt compose.yaml und Caddyfile mit
# VERSION in der .env anheben, dann:
docker compose pull
docker compose up -d --no-build
```

Die Daten liegen im Volume der Datenbank, nicht im Image. Ein neues Image lässt
sie stehen. Vier Dinge gehören trotzdem dazu:

- **Vorher ein Backup, und nicht, solange eine Zählung läuft.** Beim Start
  läuft `migrate` und ändert das Schema, wo die neue Fassung es verlangt.
  Migrationen, die Daten nachziehen, brauchen keine Handarbeit. Was doch
  Handarbeit verlangt, kündigt das Changelog als Bruch an (`feat!:`
  oder `fix!:`).
- **Hat `git pull` das `Caddyfile` geändert, braucht der Proxy einen neuen
  Container:** `docker compose up -d --force-recreate --no-build proxy`. Caddy
  liest die Datei nur beim Start, und `docker compose up -d` lässt einen
  Container stehen, dessen Einstellungen gleich blieben. Ein bloßes `restart`
  reicht nicht: der Container hält die alte Datei, und unter Docker Desktop
  startet er danach gar nicht mehr.
- **Nie `docker compose down -v`.** Das `-v` löscht die Volumes und damit
  die Datenbank.
- **Nach einem Release, das Migrationen zusammenlegt** (das Changelog sagt
  es), einmal je genannter App:
  `docker compose exec web python manage.py migrate <app> --prune`. Das räumt
  die Vermerke der alten Dateien aus der Datenbank. Bleiben sie stehen, gilt
  eine spätere Migration mit demselben Namen als schon angewandt und läuft nie.

Welche Werte `VERSION` annehmen kann:

| Gemeint ist | `VERSION` |
| --- | --- |
| der Arbeitsstand, also der letzte Push auf main | `main` |
| ein bestimmter Stand davon | `main-1a2b3c4` |
| eine bestimmte Version | `0.3.0`, oder `0.3` für die jüngste 0.3.x |
| die letzte veröffentlichte Version (die Vorgabe der `compose.yaml`) | `latest` |

Im Betrieb steht am besten eine feste Version in der `.env`. Dann entscheidet
der Eintrag, was läuft, und nicht der Zeitpunkt des letzten `pull`. Ausrollen
heißt, die Zahl zu ändern. Welche Tags es gibt, steht unter „Packages“ im Repo,
die jüngste Version auch in der Vorlage `.env.example`. Welche Fassung gerade
läuft, steht im Kontomenü der Weboberfläche ganz unten. Die Zähl-App auf den
Handys prüft stündlich und beim Zurückkommen aus dem Hintergrund, ob es eine
neue Fassung gibt, und meldet sich mit einem Hinweis in der Leiste. Geladen wird
sie erst auf Klick, damit kein Entwurf verlorengeht.

### Der Rückweg

**Der Rückweg ist die alte Zahl, solange die neue Fassung keine Migration
mitbrachte.** Hat sie das Schema geändert, kennt die alte Fassung es nicht mehr.
Dann führt der Weg über das Abbild von vorher (siehe „Zurückspielen“) und
danach die alte `VERSION` in der `.env`.

### Wenn der Start scheitert

`bin/start.sh` migriert bei jedem Start. Scheitert eine Migration, startet der
Anwendungsserver nicht, und Docker versucht es nach einer Minute wieder
(`restart: unless-stopped`). Das sieht so aus: `docker compose ps` zeigt `web`
immer wieder frisch gestartet, die Website antwortet nicht, und die Zähl-App
zeigt „Offline“. Die Ursache steht am Anfang von `docker compose logs web`.

Zwei Gründe kommen vor. Ein Wert in der `.env` fehlt oder passt nicht: Dann
sagt es die Meldung, und nach der Korrektur reicht `docker compose up -d`. Oder
eine Migration verlangt vorher Handarbeit an den Daten. Dann nennt die Meldung,
was aufzuräumen ist, und das Changelog hat es als Bruch angekündigt.
Aufgeräumt wird mit der **alten Fassung**: alte `VERSION` eintragen, starten,
die Daten richten, dann die neue Zahl eintragen und noch einmal starten. Wer
sichergehen will, spielt vor dem zweiten Anlauf das Abbild von vorher ein.

### Eine neue Hauptversion von PostgreSQL

Eine neue Hauptversion liest das Datenverzeichnis der alten nicht. Dependabot
schlägt sie deshalb nicht vor (`.github/dependabot.yml`), und der Wechsel kommt
bewusst, mit einem Release, das ihn als Bruch ankündigt. Neue
Unterversionen kommen mit `docker compose pull` von selbst. Der Wechsel selbst:

```bash
docker compose stop proxy                    # ab hier erreicht niemand mehr die Anwendung
./server/bin/backup.sh                       # ein frisches Abbild
docker compose run --rm --no-deps -T --entrypoint sh web -c \
    'python manage.py backup decrypt > /dev/null && echo lesbar' < backups/crosstally-<stand>.sql.gz.enc
docker compose down                          # ohne -v
docker volume rm <ordner>_db_data            # erst jetzt: das Datenverzeichnis der alten Version
git pull                                     # die compose.yaml mit der neuen Hauptversion
# VERSION in der .env anheben, dann:
docker compose pull
docker compose up -d --wait db               # nur die Datenbank, noch leer
docker compose run -d --no-deps --name crosstally-einspielen --entrypoint sleep web infinity
docker exec -i crosstally-einspielen python manage.py backup decrypt \
    < backups/crosstally-<stand>.sql.gz.enc |
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U crosstally crosstally
docker rm -f crosstally-einspielen
docker compose up -d --no-build              # erst jetzt web und proxy
```

Vom Abbild bis zum Einspielen erreicht niemand die Anwendung. Was in der Zeit
ankäme, wäre danach weg. Auch `web` startet erst, wenn die Daten zurück sind.
Sonst würde es zuerst eine leere Datenbank einrichten, und die wäre von außen
schon zu sehen. Der Name des Volumes steht in `docker volume ls`. Gelöscht wird
es erst, wenn das Abbild nachweislich lesbar ist. Die Probe verwirft das Abbild
im Container und gibt nur „lesbar“ aus. Stünde `> /dev/null` außerhalb, ginge
das entschlüsselte Abbild durch die Ausgabe des Containers in das Log, das
Docker für jeden Container schreibt. Eingespielt wird deshalb wie oben
über `docker exec`. Abgemeldet werden muss hier niemand: Das Abbild entstand eben
erst, bei gestopptem Proxy, und seither hat niemand etwas widerrufen. Wer andere
Werte für `POSTGRES_USER` oder `POSTGRES_DB` gesetzt hat, setzt sie hier ein.

Startet die neue Version versehentlich auf dem alten Volume, hält `db` an
und startet in einer Schleife neu. Die Daten bleiben unberührt, und mit der
alten `compose.yaml` läuft alles wie vorher. Das Log rät zu `pg_upgrade`,
der Weg hier bleibt aber das Abbild. Von 18 nach 19 lädt es so, wie es
geschrieben wurde: 19 nimmt `standard_conforming_strings = off` nicht mehr
an, und im Abbild steht `on`, wie in jeder Datenbank aus der `compose.yaml`.

## Betreiber und Datenschutz

Im Fuß jeder Seite steht, wer die Anwendung betreibt, und daneben ein Verweis
auf den Datenschutz, `/datenschutz/`, auf einem englischen Server `/privacy/`.
Beides trägt ein Administrator in der Verwaltung ein,
unter **Organisation → Erscheinungsbild und Betreiber**: Name, Kontakt und die
Anlaufstelle für den Datenschutz, denn das veröffentlichte Image kennt keinen
Betrieb. Ohne Eintrag bleibt im Fuß nur der Verweis stehen. Die Angaben gehören
zu einer **Fassung** des Erscheinungsbilds, zusammen mit dem Logo und dem Namen
der Anwendung (Vorgabe „CrossTally“). Die Fassung „Standard“ ist
unveränderlich und immer da. Geändert wird eine Kopie, und in der Liste wird
gewählt, welche Fassung gilt (README,
[„Ein eigenes Logo“](README.md#ein-eigenes-logo)).

Die Seite ist **ohne Anmeldung** zu lesen. Sie geht gerade die Zählenden an, und
die bekommen von der Weboberfläche sonst nur die Sperrseite zu sehen. In der
Zähl-App führt der Weg dorthin über das Kontomenü.

Ein **Impressum** gibt es nur, wenn eines eingetragen ist, in derselben Fassung
unter „Impressum“, je Sprache ein Text. Dann steht im Fuß ein Verweis
auf `/impressum/` (auf einem englischen Server `/legal-notice/`), ebenso im
Kontomenü der Zähl-App, und die Seite ist wie der Datenschutz ohne Anmeldung zu
lesen. Sie zeigt den Text ohne Rahmen, so wie er eingetragen ist, im Format des
Datenschutztexts (siehe „Eigener Text“). Was hineingehört, entscheidet der
Betrieb.

**Rückmeldungen** gehen an die „Adresse für Rückmeldungen“ derselben Fassung.
Ausgeliefert ist sie leer, und dann gibt es den Eintrag nicht. Mit Adresse
steht im Kontomenü der Weboberfläche und der Zähl-App „Problem oder Wunsch
melden“. Der Link öffnet eine Mail im eigenen Mailprogramm, vorbelegt mit
Version, Seite, Konto, Zeit und Browser. Die Anwendung speichert davon nichts.
Der Betreff beginnt immer mit `[CrossTally]`, darauf lässt sich ein Mailfilter
setzen.

### Warum kein Impressum von Haus aus und keine Cookie-Abfrage

Das ist keine Rechtsberatung, sondern die Überlegung hinter dem Entwurf, nach
deutschem Recht.

Ein **Impressum** nach § 5 DDG trifft Anbieter geschäftsmäßiger digitaler
Dienste, also ein Angebot an einen unbestimmten Personenkreis. Eine Anwendung,
die nur die eigenen Beschäftigten erreichen, ist ein internes Werkzeug und kein
Angebot am Markt. Deshalb bringt die Anwendung keins mit. Wer trotzdem eines
braucht, etwa weil die Anwendung auch Fremden offensteht, trägt es in der
Verwaltung ein (siehe oben). Der Seitenfuß ist so oder so da, weil ein Name
billiger ist als die Frage, und weil der Datenschutz ohnehin einen
Verantwortlichen braucht.

Eine **Cookie-Abfrage** verlangt § 25 TDDDG nur für das, was nicht unbedingt
erforderlich ist. Gespeichert wird hier ausschließlich Erforderliches:

- das Sitzungs-Cookie und das CSRF-Cookie von Django, dazu ohne Anmeldung ein
  Cookie für die gewählte Sprache, das mit dem Browserfenster geht,
- die Wahl der Darstellung, der Ansichtszustand und kurze Merker zwischen zwei
  Seiten im `localStorage` beziehungsweise `sessionStorage`
  (`static/js/theme.js`, `view_state.js`, `expand.js`),
- in der Zähl-App die Anmeldung, die Angaben des Kontos, die Zählliste, die
  Prüfliste und die noch nicht gesendeten Eingaben in IndexedDB. Ohne sie gäbe
  es kein Zählen im Funkloch. Beim Abmelden wird die Ablage geleert.

Es gibt keine Analyse, kein Tracking und keine Einbindung fremder Server. Die
Content-Security-Policy in `django_project/settings.py` lässt für Skripte,
Stile, Schriften, Bilder und Verbindungen nur `self` zu, und `test_csp.py` macht
jeden Verstoß vorher rot. Bei Beschäftigten wäre eine Einwilligung ohnehin das
falsche Mittel, denn sie muss freiwillig sein.

### Was daneben trotzdem anfällt

Das erledigt keine Software, sondern der Betrieb:

- **Information nach Art. 13 DSGVO.** Die Seite `/datenschutz/`
  (oder `/privacy/`) erfüllt das in dem Umfang, den die Anwendung selbst betrifft. Wie
  der Betrieb mit Beschäftigtendaten sonst umgeht, steht nicht darin.
- **Verzeichnis von Verarbeitungstätigkeiten** nach Art. 30 DSGVO. Die
  Anwendung braucht dort einen Eintrag.
- **Mitbestimmung** nach § 87 Abs. 1 Nr. 6 BetrVG, falls es einen Betriebsrat
  gibt. Eine technische Einrichtung ist schon mitbestimmungspflichtig, wenn sie
  zur Überwachung von Leistung und Verhalten **geeignet** ist. An einer
  Zählposition steht zwar kein Name, aber `gezaehlt_am` und die Zuordnung an der
  Inventur lassen bei einer einzelnen zählenden Person Rückschlüsse zu.

### Konten löschen statt deaktivieren

Am Konto hängt fast nichts. Mit ihm gehen nur die Zuordnung zu Häusern und
Inventuren, seine Passkeys und die Einträge dieser Person im Protokoll der
Verwaltung. Ebenso gehen seine Fehlversuche und seine Versuche am Köder
unter `/admin/`, die den Namen als Text tragen. Die Zähldaten bleiben unberührt,
denn sie verweisen auf kein Konto. Deshalb ist **Löschen** die richtige Antwort,
wenn jemand den Betrieb verlässt, und nicht das Häkchen „Aktiv“ wegzunehmen. Ein
Konto ist in einer Minute wieder angelegt, falls doch jemand zurückkommt.

Wer an einer Inventur gezählt hat, hält die Anwendung nur bis zu ihrem
Abschluss fest. Im PDF steht es nicht. Wer es für eine Prüfung braucht, notiert
es vorher außerhalb der Anwendung.

### Eigener Text

Der Text der Seite steht in der Verwaltung, in derselben Fassung wie das Logo,
je Sprache einer. Vorbelegt ist er mit dem mitgelieferten Text
(`server/templates/privacy/de.txt` und `en.txt`). Der beschreibt, was die
Anwendung tut, und das stimmt. Ob das für den eigenen Betrieb reicht,
entscheidet, wer dort für den Datenschutz zuständig ist. Der Text lässt sich
ergänzen, ändern oder ganz ersetzen. Ein eigenes Image braucht es dafür nicht.
Die Datei ist nur die Vorbelegung einer neuen Fassung. Ändert sie sich mit
einer neuen Version der Anwendung, zieht eine Migration die Fassungen nach,
deren Text noch ein mitgelieferter war. Ein geänderter Text bleibt, wie er
ist, und bekommt die Neuerung nicht. Wer beide Felder leert, nimmt die Seite
heraus: Sie antwortet dann mit 404, und weder der Seitenfuß noch das Kontomenü
der Zähl-App verweisen auf sie.

Der Text ist kein HTML, sondern Text mit fünf Regeln: eine Leerzeile trennt
Absätze, ein einfacher Zeilenumbruch bleibt stehen, `## ` beginnt eine
Zwischenüberschrift, `- ` einen Aufzählungspunkt, `**so**` schreibt fett. Wer
verantwortlich ist und wer Auskunft gibt, kommt aus den Feldern daneben und
steht über und unter dem Text, wie im Seitenfuß. Für das Impressum gelten
dieselben Regeln, nur ohne Rahmen: Es zeigt allein seinen Text.

### Logo und Name im Einzelnen

Wie eine eigene Fassung mit eigenem Logo entsteht, steht in der README
([„Ein eigenes Logo“](README.md#ein-eigenes-logo)). Dazu:

- **Nur PNG.** Ein SVG kann Skript enthalten, und aus derselben Herkunft
  ausgeliefert liefe es im Browser der Angemeldeten. Geprüft wird der Inhalt der
  Datei und nicht ihr Name: quadratisch, mindestens 512 Pixel, höchstens 4096,
  höchstens 2 MB, ein einzelnes Bild.
- **Was daraus entsteht:** das Logo der Navigationsleiste, das Bild im
  Browsertab, das Icon für iOS und die vier Größen, die die Zähl-App für den
  Startbildschirm nennt. Es braucht dafür kein Chromium und keinen Build.
- **Hintergrund für den Startbildschirm:** Android legt das Icon in seine eigene
  Form (Kreis, Squircle, Tropfen) und schneidet dabei den Rand weg. Verlässlich
  sichtbar ist nur der innere Kreis mit 80 % Durchmesser. Für diesen einen Fall
  sitzt das Motiv verkleinert auf einem deckenden Grund, und dessen Farbe steht
  im Feld daneben (voreingestellt weiß). Überall sonst bleibt das Logo
  durchsichtig.
- **Fassungen:** Löschen lässt sich jede Fassung außer dem Standard und der aktiven.
  Der Haken **Logo entfernen** unter „Name und Logo“ setzt das Logo einer
  Fassung auf den Platzhalter zurück.
- **Die Farbe der installierten App** (`theme_color`) steht im Manifest
  in `app/vite.config.js`. Ihr Name kommt aus der aktiven Fassung.

## Sprache und Zeit

Zwei Einstellungen der `.env` entscheiden, womit der Server anfängt:

```bash
LANGUAGE_CODE=de        # oder en
TIME_ZONE=Europe/Berlin
```

`LANGUAGE_CODE` gilt an zwei Stellen. Erstens dort, wo niemand angemeldet ist:
Anmeldemaske, Fehlerseiten, Datenschutz. Zweitens als **Vorgabe für jedes neue
Konto**. Wer sich anmeldet, findet die Wahl in seinem Kontomenü und behält sie
danach. Ein Wechsel am Server ändert nichts an den Konten, die es schon gibt.

**Auch die Adressen der Weboberfläche folgen `LANGUAGE_CODE`.** Auf einem
deutschen Server heißt die Liste der Häuser `/haeuser/`, auf einem
englischen `/sites/`, die Datenschutzseite `/datenschutz/` oder `/privacy/`. Die
Sprache des Kontos ändert daran nichts: Eine Adresse gilt für alle, die
denselben Server benutzen, und ein Link lässt sich weitergeben.
Wer `LANGUAGE_CODE` umstellt, stellt damit alle Adressen um, und alte
Lesezeichen laufen ins Leere. Gleich bleiben `/api/`, `/app/`, `/admin/`, die
Anmeldung unter `/accounts/` und der Pfad aus `ADMIN_URL`. Ein offener Link aus
einer Einladung oder zur Bestätigung einer neuen Adresse gilt nach dem Wechsel
nicht mehr.

Steht dort etwas anderes als `de` oder `en`, weist Django es beim Start ab
(`translation.E004`). Eine unbekannte Zeitzone ebenso, dann startet der
Container gar nicht erst.

Die Zeitzone betrifft nur die Anzeige. Gespeichert wird immer in UTC.
Umgestellt wirkt sie sofort auf Stichtage, Abschlusszeitpunkte und die
Zeitstempel der Backups.

## Was leicht schiefgeht

- **`DEBUG=False` und kein `EXTRA_ALLOWED_HOSTS`** → „Bad Request (400)“, im
  Log `DisallowedHost`. Der Name,
  unter dem die App aufgerufen wird, muss dort stehen.
- **Kein `BEHIND_TLS_PROXY=true`** hinter Caddy → Django hält die Verbindung für
  unverschlüsselt und leitet endlos um.
- **`SITE_ADDRESS=:80` ohne Proxy davor** → Caddy bedient nur Port 80, Django
  leitet auf https um, und auf Port 443 antwortet niemand. Mit eigener Domain
  gehört die Domain hinein (siehe „Das Zertifikat“).
- **Passkeys hängen an der Domain.** Der Browser bindet jeden Passkey an die
  Adresse, unter der er entstand. Zieht die Anwendung auf eine andere Domain
  um, gelten alle nicht mehr, und jedes Gerät legt einen neuen an. Unter einer
  IP-Adresse entstehen gar keine, und ohne HTTPS auch nicht, außer
  auf `localhost`. Hinter dem Proxy braucht die Prüfung der Herkunft die
  Einstellung `BEHIND_TLS_PROXY=true`, sonst erwartet Django `http://` und
  lehnt jede Antwort des Browsers ab.
- **`.env` nach einer Änderung** wirkt erst nach `docker compose up -d` neu,
  denn sie wird beim Start gelesen.
- **Ein Knopf tut nichts, eine Schrift oder ein Bild fehlt** → in die Konsole
  des Browsers sehen. Die Seite schickt eine **Content-Security-Policy** mit
  (`SECURE_CSP` in `settings.py`). Ausgeführt und geladen wird nur, was von der
  eigenen Adresse kommt. Was ein vorgeschalteter Dienst in die Seite schreibt
  (Skripte eines CDN, „E-Mail-Schutz“, Analyse), sperrt der Browser. Das ist
  gewollt. Dort abschalten, nicht die Richtlinie lockern.
- **Der Datenordner von Postgres** wird als Ganzes eingehängt
  (`/var/lib/postgresql`), nicht das `data` darunter. So verlangt es das Image
  seit Postgres 18, sonst verweigert es den Start.
- **Steht noch etwas vor Caddy, das TLS macht** (`tailscale serve`), braucht
  Caddy `TRUSTED_PROXIES` in der `.env`. Die Vorgabe ist leer: Caddy glaubt
  niemandem, überschreibt den Vermerk über das ursprüngliche Protokoll, und
  Django leitet endlos auf https um. Eingetragen wird der Proxy davor und sonst
  nichts, denn wem Caddy glaubt, der bestimmt seine Adresse selbst und kommt so
  an der Anmeldesperre vorbei. `private_ranges` glaubt jedem im Hausnetz. Auf
  demselben Rechner kommt `tailscale serve` aus dem Docker-Netz an, dafür
  genügt `172.16.0.0/12`. Enger ist das Gateway allein, zu finden mit
  dem Befehl `docker network inspect <ordner>_default`.
- **Mit einem Proxy davor hört Caddy nur dort, wo der Proxy es erreicht**
  (`LISTEN_ADDRESS`, auf demselben Rechner `127.0.0.1`). Sonst erreicht jeder im
  Hausnetz Caddy am Proxy vorbei und gilt als der Proxy, denn Docker Desktop
  reicht die Adresse des Aufrufers nicht durch.
- **`PROXY_COUNT` muss zur Kette der Proxys passen.** Die Sperre zählt je
  Adresse, und hinter Caddy steht die des Aufrufers in `X-Forwarded-For`. Die
  Zahl sagt, wie viele Stationen **vor Caddy** darin stehen. Caddy schreibt
  die Kopfzeile selbst und steht nicht darin. Aus dem Internet direkt auf
  Caddy: 0 (Vorgabe). Steht `tailscale serve` davor: 1. Zählt die Anwendung
  falsch, findet sie keine Adresse oder die eines Proxys und sperrt dann alle
  auf einmal.
- **Die Suche braucht die Erweiterung `pg_trgm`.** Eine Migration legt sie an
  (`CREATE EXTENSION`). Mit dem Postgres aus `compose.yaml` geht das von selbst,
  denn dort gehört die Datenbank dem Konto der Anwendung. Auf einem fremden
  Postgres, dessen Konto das nicht darf, legt sie vorher jemand mit den Rechten
  an: `CREATE EXTENSION IF NOT EXISTS pg_trgm;` in der Datenbank der Anwendung.
  Sonst bricht `migrate` beim Start ab.
- **Wer sich fünfmal vertippt, ist eine halbe Stunde gesperrt** (`django-axes`,
  Benutzername und Adresse zusammen). Die Sperre löst sich von selbst. Wer
  nicht warten will, löscht die Zeile in der Verwaltung unter „Fehlversuche bei
  der Anmeldung“.

## Für Forks und Mitwirkende

Was hier steht, braucht nur, wer selbst Images baut oder veröffentlicht oder am
Projekt mitarbeitet.

### Einen Fork auf GitHub vorbereiten

Nur für den, der selbst Images veröffentlicht, etwa wegen eigener Änderungen
am Code. Für ein eigenes Logo, den Betreiber und einen eigenen Datenschutztext
braucht es das nicht. Die stehen in der Verwaltung (siehe README). Einmalig,
damit `image.yml` durchläuft und das Image in der Registry landet:

1. **Actions müssen erlaubt sein** (Settings → Actions → General). Ein Secret
   braucht es **nicht**. Der Workflow meldet sich mit dem `GITHUB_TOKEN` an,
   das jeder Lauf bekommt, und fordert sich `packages: write` selbst an.
2. **Nach dem ersten Push auf `main`** entsteht das
   Package `ghcr.io/<besitzer>/<repo>`. Es übernimmt die Sichtbarkeit des Repos:
   Aus einem öffentlichen Repo ist es öffentlich, und der Server zieht ohne
   Anmeldung.
3. **Nur bei einem privaten Repo oder einem alten Package** (Package → Package
   settings):
   - Ist das Repo privat, ist es auch das Package. Entweder *Change visibility*
     → **Public**, oder auf dem Server einmal anmelden, mit einem klassischen
     PAT mit `read:packages`:
     `echo "<PAT>" | docker login ghcr.io -u <benutzer> --password-stdin`.
   - Gab es das Package schon, etwa aus einem gelöschten Repo mit demselben
     Namen, gehört es nicht dem neuen Repo, und der Upload scheitert
     mit `permission_denied`. Dann unter *Manage Actions access* das Repo mit
     der Rolle „Write“ eintragen oder das alte Package löschen.
4. **Das eigene Image eintragen:** in der `compose.yaml` den Namen
   unter `web.image` auf `ghcr.io/<besitzer>/<repo>` ändern. Welche Tags
   entstehen, steht unter „Versionen und Releases“.
5. **release-please darf Pull Requests anlegen**: Settings → Actions → General →
   *Workflow permissions* → „Allow GitHub Actions to create and approve pull
   requests“ anhaken. Ohne den Haken scheitert der Job „Release“ in `image.yml`.
6. **Zusammenführen als ein Commit**: Settings → General → *Pull Requests* →
   „Allow squash merging“, als Vorgabe für die Nachricht „Pull request title“.
   Der Titel wird damit zur Commit-Nachricht, und `pr_title.yml` prüft ihn.
7. **Dependabot** braucht nichts weiter, `.github/dependabot.yml` genügt. Die
   Sicherheitsmeldungen dazu stehen unter Settings → Advanced Security.

### Selbst bauen

Auf einem Rechner, der selbst baut (Entwicklung, Testserver):

```bash
git pull
docker compose up -d --build     # migrate läuft beim Start (bin/start.sh)
```

Ein selbst gebautes Image nennt im Kontomenü den Stand des Checkouts, aus dem
es gebaut wurde, etwa `lokal-1a2b3c4`. Der Build liest ihn aus den paar Dateien
von `.git`, die der Build-Kontext mitnimmt (`bin/version.sh`), ohne dass jemand
etwas einträgt. Aus einem Archiv ohne `.git` heißt es „unknown“, ebenso aus
einem Worktree oder Submodul, wo `.git` eine Datei ist. Wer einen eigenen Namen
will, setzt `APP_VERSION` in der `.env`. Ein veröffentlichtes Image trägt seine
Nummer aus `image.yml`.

Für einen ARM-Rechner gehört in `image.yml` `linux/arm64` zu den Plattformen
und `docker/setup-qemu-action` davor. Der Lauf dauert dann ein Vielfaches.
Hochgeladen wird nach `ghcr.io/<besitzer>/<repo>` mit dem Zugangstoken des Laufs. Wer das
nicht will, setzt im Schritt „Build and push“ `push: false`.

### Versionen und Releases

Die Versionen führt **release-please** (Job „Release“ in `image.yml`), aus den
Commit-Nachrichten auf main. Die folgen den Conventional Commits:

| Nachricht beginnt mit | Wirkung | im Changelog unter |
| --- | --- | --- |
| `feat:` | hebt die mittlere Stelle (0.3.0 → 0.4.0) | Neu |
| `fix:` | hebt die letzte Stelle (0.3.0 → 0.3.1) | Behoben |
| `perf:`, `refactor:`, `revert:` | hebt die letzte Stelle, wie `fix:` | Schneller, Umbauten, Zurückgenommen |
| `docs:`, `style:`, `test:`, `build:`, `ci:`, `chore:` | keine | - |
| `feat!:` oder `fix!:` | ein Bruch: im Betrieb ist Handarbeit nötig | oben, hervorgehoben |

Vor 1.0.0 hebt auch ein Bruch nur die mittlere Stelle. Eine Korrektur gehört
unter `fix:`, nicht unter `refactor:`, damit sie unter „Behoben“ steht.

Dependabot schreibt seine Titel danach (`.github/dependabot.yml`). Was im
Betrieb läuft, also die Python-Pakete, die Images und die Pakete
unter den `dependencies` der App, kommt als `fix(deps): …` und wird zur
nächsten Patch-Version, ein Sicherheitsupdate eingeschlossen. Deshalb steht
unter den `dependencies` alles, dessen Code in der App landet: Dexie, Svelte
und das PWA-Plugin, das Workbox in den Service Worker bringt. Werkzeuge der
Entwicklung und die Actions kommen als `chore(…)` und lösen keine aus.

Der Ablauf:

1. Änderungen kommen über einen Zweig und einen Pull Request nach main,
   als **ein** Commit (squash). Dessen Nachricht ist der Titel des Pull
   Requests. Einen Zweig `dev` gibt es nicht. main ist der Arbeitsstand, und die
   Schwelle zur Veröffentlichung ist der Release-PR.
2. Nach jedem Push auf main hält release-please einen Pull Request **„chore:
   release x.y.z“** aktuell. Darin stehen `CHANGELOG.md`, `version.txt`,
   `.release-please-manifest.json` und die Zeile `VERSION` in `.env.example`,
   sonst nichts. Er darf liegen bleiben,
   solange noch gesammelt wird.
3. **Veröffentlicht wird, indem man ihn zusammenführt.** Derselbe Lauf setzt den
   Tag `vx.y.z`, legt das Release auf GitHub an und baut das Image.

Die Tags des Images:

| Auslöser | Tags |
| --- | --- |
| Push auf main | `:main` und `:main-<kurzer Hash>` |
| Release (zusammengeführter Release-PR) | dazu `:x.y.z`, `:x.y` und `:latest` |

`:latest` ist damit die letzte **veröffentlichte** Version, nicht der letzte
Push auf main, und gibt es erst mit dem ersten Release.

Eine bestimmte Nummer erzwingen (etwa 1.0.0): ein Commit auf main, dessen
Nachricht im Rumpf die Zeile `Release-As: 1.0.0` trägt. Das erste Release war
0.1.0, so steht es als `initial-version` in `release-please-config.json`. Ohne
die Angabe hätte release-please bei 1.0.0 begonnen.

Zwei Eigenheiten: Den Release-PR legt ein Workflow mit dem `GITHUB_TOKEN` an.
Seine Prüfungen laufen deshalb erst, wenn jemand die wartenden Workflows im PR
freigibt, und nach jedem neuen Stand von release-please wieder. Danach wird er
zusammengeführt wie jeder andere. Auch die Prüfung des Titels hört deshalb auf
das Ereignis `pull_request`: Auf `pull_request_target` startet GitHub für einen
solchen PR gar keinen Lauf, und er bliebe blockiert. Ein eigenes Token für
release-please erspart die Freigabe. Und die Version
in `pyproject.toml` und `package.json` bleibt, wie sie ist. Beides sind keine
veröffentlichten Pakete, und ein Sprung dort machte `uv.lock` ungültig.

### Was die CI tut

| Datei | Läuft bei | Was |
| --- | --- | --- |
| `server_ci.yml` | Pull Request auf main | ruff, pyright, Systemprüfung, `check --deploy`, fehlende Migrationen, dann die Tests gegen Postgres mit Abdeckung |
| `app_ci.yml` | Pull Request auf main | Prettier, Vitest mit Abdeckung, Build, dann Playwright gegen ein Django mit dem Demo-Bestand |
| `audit.yml` | montags und bei Pull Requests an den Lock-Dateien und den Audit-Skripten, beides nur für main | bekannte Lücken in allem Fremden, was ausgeliefert wird: Python-Pakete (`pip-audit`), die Pakete im Build der Zähl-App (`npm audit`), die Bibliotheken unter `static/vendor/` (OSV). Dazu die Lizenzen aller Abhängigkeiten gegen eine Liste erlaubter. Montags ein Scan jedes Images aus `compose.yaml`, vom eigenen `:main` und `:latest` |
| `pr_title.yml` | Pull Request auf main | der Titel folgt den Conventional Commits |
| `secrets.yml` | Pull Request auf main | gitleaks über die Commits des Pull Requests |
| `image.yml` | **Push** auf main, außer wenn er nur `*.md` oder `*.mermaid` ändert | ruft die drei oberen auf, führt die Versionen fort, baut das Image und durchsucht es nach bekannten Lücken |
| `image.yml` | Pull Request auf main | baut zur Probe, ohne hochzuladen, und prüft `compose.yaml` und `Caddyfile` |

Beim Push laufen Stil und Tests also auch. `image.yml` ruft die anderen als
Bausteine auf (`workflow_call`), statt sie zu wiederholen. Ein Image aus rotem
Code hilft niemandem. Auf das **Audit** wartet das Image dagegen nicht. Eine
fremde Lücke ohne verfügbare Korrektur hielte sonst auch jede eigene Korrektur
vom Server fern. Der Lauf wird trotzdem rot. Das Audit gilt nur für main: Einen
Zeitplan startet GitHub immer auf dem Standardzweig, und in einer Kopie kann das
ein anderer sein. Dort wird jeder Job übersprungen.

Installiert wird in der CI mit `uv sync --locked`. Passt `uv.lock` nicht mehr
zur `pyproject.toml`, bricht der Lauf. (`--frozen` täte das nicht, es nimmt den
Lock, wie er ist.) Nach einer Änderung an den Abhängigkeiten also `uv lock`.

Die Tests laufen mit dem, was das Image installiert: ohne die
Entwicklungswerkzeuge, mit der Gruppe `postgres` und damit mit der C-Fassung
von psycopg. Ein Schritt davor prüft, dass sie wirklich geladen ist. Wer lokal
gegen PostgreSQL testen will, holt sie mit `uv sync --group postgres`. Das
braucht einen Compiler und die Kopfdateien von libpq, unter Debian
sind das `gcc` und `libpq-dev`. Dazu kommt die Gruppe `test` mit `tblib`
und `coverage`. Ohne `tblib` endet ein Fehlschlag im parallelen Lauf mit
„cannot pickle 'traceback' object“ statt mit dem Namen des Tests. Lokal kommt
die Gruppe mit `uv sync --group test` oder `uv run --group test`. Ein
bloßes `uv sync` lässt sie weg.

Die Tests laufen unter `coverage`, Zeilen und Zweige zusammen. Erreichen sie
weniger als 95 % (`fail_under` in `server/pyproject.toml`), wird der Job rot.
Für die Zähl-App tut das `npm run test:coverage` mit den Schwellen
in `app/vite.config.js`. Vor den Tests prüft `manage.py check --deploy` die
Einstellungen des Betriebs, mit Platzhaltern für einen Betrieb mit Mailserver
und einem Jahr HSTS. Jede Warnung macht den Job rot.

Fremde Actions sind auf einen **Commit** festgelegt, die Version steht als
Kommentar dahinter. **Dependabot** hebt beides gemeinsam an und kümmert sich
ebenso um `uv.lock`, `package-lock.json`, die Basisbilder im `Dockerfile` und
die Images in `compose.yaml`, eine neue Hauptversion von PostgreSQL ausgenommen.
Das geschieht montags, je Ökosystem ein Pull Request, bei den Images einer je
Image. Ein großer Sprung kommt allein.

**Nicht** im Blick hat Dependabot die Bibliotheken der Weboberfläche
unter `server/static/vendor/` (Bootstrap, Tom Select, SortableJS). Sie liegen
fertig im Repo. Eine neue Version holt `server/bin/vendor.sh`: Version und
SRI-Hash dort ändern, ausführen, die Oberfläche einmal durchklicken. Das Skript
holt die Lizenztexte mit, und `THIRD_PARTY_NOTICES.md` zieht in beiden Sprachen
mit.

Meldet der Montagsscan Lücken im veröffentlichten Image, ist meist nur das
Basisbild gealtert. Ein neues Release baut ohne Cache und spielt die
Sicherheitsupdates von Debian ein (`apt-get upgrade` im `Dockerfile`). Meldet er
Lücken in Datenbank oder Proxy, die der Hersteller noch nicht behoben hat, steht
der Befund bis dahin in `.trivyignore.yaml`, mit Grund und Ablaufdatum. Danach
wird der Lauf wieder rot. Das eigene Image prüft der Scan nur in dem Repo, das
es veröffentlicht, und im Image der Datenbank übergeht er `gosu`, das beim
Start nur die Rechte senkt. Vom eigenen Image prüft er `:main` und `:latest`,
denn welches davon läuft, entscheidet die `.env` des Servers (`VERSION`). Fehlt
eines der beiden in der Registry, übergeht er es mit einem Hinweis. Vor dem
ersten Release fehlt `:latest`.

### Übersetzungen

Die Ausgangssprache ist **Deutsch**. Jede msgid ist der deutsche Satz, wie er im
Code oder in der Vorlage steht. Daraus entsteht die englische Oberfläche über
gettext. Was aus WinTICKET kommt oder von einer Führungskraft eingetragen wurde
(Artikel-, Haus-, Familiennamen), bleibt in jeder Sprache, wie es dasteht.

Die Kataloge liegen unter `server/locale/`:

| Ordner | Inhalt |
| --- | --- |
| `en/LC_MESSAGES/django.po` | die ganze Oberfläche auf Englisch |
| `de/LC_MESSAGES/django.po` | was Djangos eigener deutscher Katalog offen lässt, dazu der Spiegel (siehe unten) |
| `de/LC_MESSAGES/djangojs.po` | dasselbe für das Auswahlfeld mit den zwei Listen, dessen Texte im JavaScript stehen |
| `*.mo` | Das Übersetzte. Django liest nur diese Dateien. |

Djangos eigener deutscher Katalog hinkt der Version hinterher. Was dort fehlt,
steht hier, und weil `LOCALE_PATHS` vor den Katalogen der Pakete gelesen wird,
gilt es.

**Der Spiegel der Ausgangssprache.** Darunter steht im deutschen Katalog noch
einmal jeder eigene Eintrag, als seine eigene Übersetzung. Das klingt sinnlos,
ist aber nötig: Django setzt den Katalog von `LANGUAGE_CODE` als **Rückfall**
für jede andere Sprache. Steht der Server auf Englisch, käme ein deutscher
Satz, der im deutschen Katalog fehlt, für ein deutsches Konto auf Englisch
heraus. Geschrieben wird der Block von `bin/mirror_source_catalog.py`, von Hand
wird dort nichts ergänzt.

Nach einem neuen oder geänderten Text im Code:

```bash
# aus server/, braucht gettext (apt install gettext)
uv run manage.py makemessages -l en --ignore=.venv --ignore=pwa --ignore=data
# die neuen Einträge in 'locale/en/LC_MESSAGES/django.po' ausfüllen, dann:
uv run python bin/mirror_source_catalog.py
uv run manage.py compilemessages --ignore=.venv
```

Das `--ignore` ist wichtig: Ohne es läuft der Befehl auch über die Kataloge
aller Sprachen, die Django mitbringt. Die `.mo` gehören ins Repo (`.gitignore`
nimmt genau diesen Ordner aus), damit das Image nichts übersetzen muss und ohne
gettext auskommt. Dass Datei und Übersetzung zueinander passen und kein Eintrag
leer bleibt, prüft `django_project/test_translations.py` bei jedem Testlauf.

Drei Dinge laufen **nicht** über gettext:

- **Die Hilfetexte** (`server/templates/help/<sprache>/`). Es sind lange
  Fließtexte, und die stehen besser als ganze Seite nebeneinander als in hundert
  Bruchstücken im Katalog. Fehlt ein Text auf Englisch, erscheint der deutsche.
- **Die Zähl-App.** Sie trägt ihre Texte in `app/src/texts.js`, deutsch und
  englisch nebeneinander, und holt die Sprache des Kontos über die Schnittstelle.
- **Die Skripte der Website** (`server/static/js/`). In einer `.js`-Datei
  greift gettext nicht. Was ein Skript anzeigt, reicht die Vorlage ihm deshalb
  als `data`-Attribut an seinem eigenen `<script>`-Tag herein, und dort
  wirkt `{% translate %}`. Der Text steht also im Katalog wie jeder andere. Wer ein
  Skript ergänzt, schreibt kein Wort hinein, sondern ein Attribut daneben.

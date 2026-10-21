# Die Demo

*[English version](DEMO.en.md)*

Ein Befehl legt einen vollständigen, erfundenen Bestand an: vier Häuser, vier
Konten, ein Warenkatalog, Zähllisten und 14 Inventuren an sieben Stichtagen.
Vier Läufe sind abgeschlossen, zwei laufen gerade, und einer wird noch
vorbereitet (ein Lauf ist eine Inventur je Haus am selben Stichtag). Damit
lässt sich die ganze Anwendung durchspielen, ohne dass jemand erst Stammdaten
eintippt oder echte Zahlen einspielt.

> **Nichts davon ist echt.** Häuser, Waren, Artikelnummern, Mengen und Namen
> sind erfunden, und das Passwort der Konten steht weiter unten in diesem Text.
> Der Bestand gehört in keine Datenbank, in der gearbeitet wird. Der Befehl
> weigert sich deshalb, sobald dort etwas steht, das nicht aus der Demo stammt,
> und sei es ein einziges Konto. Auf einem Server ohne `DEBUG` baut er nur mit
> einem eigenen Schalter (siehe unten).

Solange die Demo läuft, sagt es die Oberfläche auf jeder Seite: ein Band über
der Navigationsleiste, ein Abzeichen daneben, das beim Scrollen stehen bleibt,
dasselbe in der Verwaltung und eine Zeile unter dem Kopf der Zähl-App. Das
Kennzeichen hängt an einem eigenen Satz in der Datenbank, nicht an den Daten:
es bleibt, auch wenn man beim Ausprobieren ein Haus löscht, und es verschwindet
mit `--reset`.

## Aufbauen

Die Demo braucht kein Docker. Der kürzeste Weg ist der Entwicklerweg aus der
[README](README.md) mit SQLite:

```bash
cd server
cp .env.example .env         # SECRET_KEY und ADMIN_URL eintragen
uv sync
uv run manage.py migrate
uv run manage.py demo        # baut den Bestand auf und druckt eine Übersicht
uv run manage.py runserver   # http://localhost:8000
```

> **Die Demo legt keine eigene Datenbank an.** Sie schreibt in die, auf die
> `DATABASE_URL` zeigt, beim Entwickeln also `server/db.sqlite3`. Steht dort
> schon etwas, bricht der Befehl ab und nennt, was er gefunden hat, auch
> mit `--reset`.

Wer schon mit echten Daten arbeitet, gibt der Demo eine eigene Datei. Dann
bleibt die eigene Datenbank unberührt:

```bash
cd server
export DATABASE_URL="sqlite:///demo.sqlite3"
uv run manage.py migrate
uv run manage.py demo
uv run manage.py runserver
```

Die Zeile in der `.env` stört dabei nicht: was in der Umgebung steht, hat
Vorrang, denn die `.env` wird gelesen, ohne Gesetztes zu überschreiben. Der Pfad
ist relativ zu dem Verzeichnis, aus dem der Befehl läuft, hier
also `server/demo.sqlite3`. Die Datei steht in der `.gitignore` und lässt sich
danach einfach löschen.

Zurück zur eigenen Datenbank geht es mit `unset DATABASE_URL` oder in einem
neuen Terminal. Wer das vergisst, arbeitet weiter in der Demo.

Für die Zähl-App im Browser dazu einmal `npm ci && npm run build`
aus `app/`. Django liefert sie danach unter `/app/` aus.

Mit Docker läuft die Anwendung ohne `DEBUG`, wie im Betrieb. Dort baut der
Befehl nur mit `--demo-server`, dem Schalter für einen Server, der nur die Demo
zeigen soll. Auch dann baut er nur in einer frischen Datenbank: gleich nach dem
ersten Start und statt `createsuperuser`. Steht dort schon ein Konto, weigert er
sich.

```bash
docker compose exec web python manage.py demo --demo-server
```

Wer einen solchen Server erreicht und diesen Text kennt, meldet sich
als `admin` an. Deshalb baut der Befehl ohne `DEBUG` nur, wenn der Schalter es
ausdrücklich sagt.

Fünf Schalter gibt es:

| Schalter | Wirkung |
| --- | --- |
| `--reset` | Löscht Häuser, Waren, Artikel, Zähllisten, Inventuren und die vier Demo-Konten, bevor neu aufgebaut wird. Fragt vorher nach. Nur in einer Datenbank, die schon die Demo trägt. |
| `--date JJJJ-MM-TT` | Rechnet von diesem Tag aus statt von heute. Nützlich für Screenshots, die immer gleich aussehen sollen. |
| `--language de\|en` | In welcher Sprache die erfundenen Namen und die Ausgabe des Befehls entstehen. |
| `--noinput` | Lässt `--reset` ohne Rückfrage laufen, etwa aus einem Skript. |
| `--demo-server` | Baut auch ohne `DEBUG`, auf einem Server, der nur die Demo zeigen soll. |

Die Schalter heißen englisch, wie bei jedem Django-Befehl, und haben Kurzformen
(`-r`, `-d`, `-l`), nur `--noinput` und `--demo-server` nicht. Die deutsche
Demo ist die Vorgabe.

### Die Demo auf Englisch

```bash
uv run manage.py demo --language en
```

Dann heißen die Häuser „Cinema North“, „Cinema East“, „Cinema West“ und „Ice cream workshop South“, die
Waren „Lemon soda 0.5 l“ und „Popcorn salt“, die Orte „Counter left“ und
„Freezer“. Auch die Importdateien liegen englisch bereit, und die vier Konten
stehen auf Englisch, sodass die Oberfläche gleich nach dem Anmelden in ihr
dasteht. Auch die Übersicht, die der Befehl am Ende druckt, ist dann englisch.
Ohne den Schalter gilt `LANGUAGE_CODE` aus der `.env`: auf einem
englisch eingestellten Server entsteht die englische Demo von selbst, und auch
die Adressen heißen dort englisch (`/sites/` statt `/haeuser/`).

Namen sind **Daten**, keine Oberflächentexte. Im Betrieb stehen dort die Waren
des Hauses, und die übersetzt niemand. Die Demo ist erfunden, deshalb gibt es
für sie eine Namenstabelle (`server/demo/names_en.py`).

Ein Ding bleibt dabei deutsch, und das mit Absicht: **das Gerüst der
Importdateien.** „gekauft in Kiste(n) zu 24 Stück(e)“ ist das Format der
Warenwirtschaft, und die ist ein deutsches Programm. Englisch werden dort nur
die Bezeichnungen.

„Nicht zählbar“, „Ohne Behälter“ und „Ohne Warengruppe“ gehören dagegen der
Anwendung und nicht einem Bestand. Sie stehen im Katalog und folgen damit der
Sprache **des Kontos**: auf einem englischen Server liest ein deutsches Konto
sie weiter auf Deutsch. Gespeichert bleibt ihr Name, wie er ist, umbenannt wird
nichts. Das gilt in jeder Datenbank, auch in einer, in der gearbeitet wird.

## Die Konten

Passwort für die ersten drei: `demo1234`.

| Konto | Rolle | Was damit geht |
| --- | --- | --- |
| `admin` | Administrator | Alles, dazu die Verwaltung und die Backups. Darf überall zählen, ohne eingetragen zu sein. |
| `leitung` | Führungskraft | Die ganze Weboberfläche, aber keine Verwaltung. Ist jeder offenen Inventur als zählend zugeordnet, wie `zaehler`. |
| `zaehler` | zählt nur | Kommt nicht auf die Website, sondern nur in die Zähl-App unter `/app/`. Zählt in jeder offenen Inventur. |
| `eingeladen` | wartet noch | Ohne Passwort angelegt, so wie das Benutzerformular es mit Mailserver tut, solange „Passwort selbst vergeben“ leer bleibt. Steht in der Benutzerliste als „eingeladen“. Auf seiner Seite schickt „Einladung erneut senden“ die Mail mit dem Link, über den die Person ihr Passwort setzt. Die Mail landet im Log oder in Mailpit (siehe DEPLOYMENT.md). |

Dass `zaehler` an der Website abprallt, ist kein Fehler, sondern der Zweck der
Rolle. Die Sperrseite erklärt es.

Jedes Konto ändert sein Passwort selbst: in der Weboberfläche über das
Kontomenü, in der Zähl-App ebenso. Für `zaehler` ist die App der einzige Weg.
Danach gilt für dieses Konto das neue Passwort, und jedes andere Gerät meldet
sich damit neu an. Mit `--reset` entstehen die vier Konten wieder, die ersten
drei mit dem Passwort von oben.

Passkeys bringt die Demo keine mit: ein Passkey gehört zu einem Gerät und
entsteht nur dort. Jedes Konto legt sich über das Kontomenü („Passkeys“)
welche an, auf `localhost` auch ohne HTTPS. Vorher fragt die Seite nach dem
Passwort von oben, die Zähl-App ebenso, und vor dem Entfernen noch einmal. Wer
einen Passkey entfernt, meldet das Konto auf jedem anderen Gerät ab, auf der
Website sofort und in der Zähl-App spätestens nach einer Stunde. Das Gerät, auf
dem er entfernt wird, bleibt angemeldet.

Jedes Konto hat eine Adresse unter `example.org`. Wer eines ändert, deaktiviert
oder löscht, schickt der Person eine Mail, die sagt, was sich geändert hat und
von wem. Ebenso kommt eine bei einem neuen Passwort und bei jedem neuen oder
entfernten Passkey. Die Mails landen im Log oder in Mailpit (siehe
DEPLOYMENT.md). Die Adresse ändert jedes Konto selbst über das Kontomenü, auch
in der Zähl-App, oder `admin` ändert sie: an die neue Adresse geht erst eine
Mail mit einem Link, und die Adresse gilt, sobald jemand ihn bestätigt hat. Ein
Versuch: die Adresse von `zaehler` in der App ändern und den Link aus Mailpit
öffnen.

Was in den Mails steht, ändert `admin` in der Verwaltung
unter **Organisation → Mailtexte**. Die Demo liefert die mitgelieferten Texte
aus. Ein Versuch: den Text der Einladung ändern, die Vorschau unten auf der Seite
ansehen und dann auf der Seite von `eingeladen` „Einladung erneut senden“
drücken. Je Mail lässt sich dort auch abschalten, dass sie nennt, wer etwas
ausgelöst hat. Ohne Mailserver (`DEBUG` aus und kein `EMAIL_HOST`)
verschwinden die Einladung und „Passwort vergessen?“ von den Seiten, und ein
neues Konto braucht gleich ein Passwort.

## Was bereitliegt

### Häuser

| Haus | Zustand |
| --- | --- |
| Kino Nord | Voll eingepflegt: vier Bereiche, neun Orte, rund 70 Artikel, Zähllisten mit Gebindevorgaben. An einem Ort wird nichts erwartet. |
| Kino Ost | Voll eingepflegt, aber anders geschnitten: zwei Bereiche, fünf Orte, eigene Warengruppen und eigene Bezeichnungen für dieselben Waren. |
| Kino West | Leer. Dieses Haus wird in der Demo selbst aufgesetzt, siehe „Ein Haus aufsetzen“. |
| Eismanufaktur Süd | Ein Haus ohne Warenwirtschaft: fünf eigene Artikel ohne Nummer, zwei Orte. Das Eis ist dieselbe Ware wie in den Kinos, hier in Kilo statt in Stück. |

Dass die beiden gepflegten Häuser dieselbe Ware verschieden benennen und in
verschiedene Warengruppen legen, ist Absicht. Genau dafür gibt es die
Ware, die sie über die Häuser hinweg zusammenhält.

### Der Katalog

Rund 80 Waren in fünfzehn Warenfamilien, dazu sieben Rezepturartikel. Vertreten ist
jeder Fall, den die Anwendung kennt:

- Ware in Stück, in Kilogramm und in Litern, mit ein bis zwei Gebinden je Ware.
  Drei hat nur der Schokoriegel: einzeln, im Display zu 20 und im Karton
  zu 40. An ihm lässt sich die Reihenfolge der Gebinde ziehen. Das Display
  ist in keiner Zählliste angehakt und zählt deshalb nirgends mit.
- Abwiegbare Ware mit Behältern und Tara, etwa lose Süßwaren und Nachochips.
- Rezepturartikel mit Rezeptur: eine Portion Nachos löst sich beim Abschluss in
  Chips, Sauce und Schale auf. „Popcorn süß lose“ kommt als Ganzes auf die
  Waage und verteilt sich auf drei Waren. „Süßwarenmischung“ und
  „Sirupmischung Hauslimo“ nennen ihre Rezeptur als Anteile in Prozent, die
  zweite aus Sirupen in Litern, die ihr Gewicht je Liter tragen.
- Drei Inventurarten: Monats-, Jahres- und Becherinventur. In der
  Jahresinventur steht, was nur einmal im Jahr gezählt wird, etwa Wein und
  Reinigungsmittel. Mehrwegbecher führt nur Kino Nord, sie zählen in der
  Becherinventur.
- Den Weißwein zählt Kino Ost jeden Monat, Kino Nord nur einmal im Jahr. Auf
  der Seite der Ware steht er deshalb als „gemischt“, mit den Häusern je Art.
- Derselbe Becher zweimal in Kino Nord: „Becher 0,4 l“ unter 101607 und
  „Trinkbecher 0,4 l“ unter 101613, als zwei Waren und an denselben Orten
  erwartet.
- Artikel in allen vier Zuständen: aktiv, inaktiv, eingestellt und nicht
  zählbar. Letztere liegen in der Familie „Nicht zählbar“, wo Pfand und
  Gutscheine hingehören.

### Inventuren

Die Stichtage hängen am Tag des Aufbaus, die Demo altert also nicht. Gebaut im
September 2026 sieht es so aus:

| Stichtag | Arten | Status |
| --- | --- | --- |
| 31.12.2025 | Monats-, Jahres- und Becherinventur | abgeschlossen, beide Häuser |
| 31.05.2026 | Monatsinventur | abgeschlossen, beide Häuser |
| 30.06.2026 | Monatsinventur | abgeschlossen, beide Häuser |
| 31.07.2026 | Monatsinventur | abgeschlossen, beide Häuser |
| 24.08.2026 | Becherinventur | nur Kino Nord, denn nur dort gibt es Mehrwegbecher. Gezählt, ohne Sollbestand, mit Differenz von Hand, gesperrt, und wartet aufs Abschließen |
| 31.08.2026 | Monats- und Becherinventur | **läuft**: Kino Nord ist durchgezählt, Kino Ost hat angefangen |
| 31.12.2026 | Monats-, Jahres- und Becherinventur | in Vorbereitung, beide Kinos und die Manufaktur, Sollbestand ist eingelesen beziehungsweise von Hand eingetragen |

Welche Tage es bei dir sind, sagt der Befehl am Ende seiner Ausgabe.

## Ein Rundgang

### Die laufende Inventur

Die Startseite ist die Inventurliste. Der zweite Lauf von oben ist der offene,
darüber steht der in Vorbereitung. Über die Reiter im Kopf springt man zwischen den Häusern und dem Reiter „Gesamt“, der
die Differenz je Ware über beide Häuser zeigt.

- **Kino Ost** steht am Anfang. Auf der Detailseite sieht man je Ort „x von y
  gezählt“, und es ist genau ein Ort fertig.
- **Kino Nord** ist durchgezählt. Der Knopf „Prüfen“ führt in die Prüfansicht
  mit Suche, Warengruppen zum Einklappen, Differenz je Artikel und einem
  Notizfeld je Zeile. „Werte aktualisieren“ holt den Stand nach, ohne die Seite
  neu zu bauen. Solange etwas nachgekommen ist, bleibt der Abschluss gesperrt.
  Beim Colasirup ist „Soll zählt“ angehakt, mit der Notiz, warum: Er steckt in
  der Anlage. Beim Abschluss gilt dort das Soll als Ergebnis.

Der Reiter „Gesamt“ zeigt je Ware die Differenz über beide Häuser und daneben
je Haus. In den **abgeschlossenen** Läufen laufen bei genau zwei Waren die
Differenzen gegeneinander: was in einem Haus fehlt, liegt im anderen zu viel.
Das ist der typische Fall einer Warenbewegung zwischen zwei Häusern. Im
laufenden Lauf ist das Soll die Datei aus der Warenwirtschaft, und die Zählung
weicht davon vielerorts ab. Dort sagt die Zahl „gegenläufig“ noch wenig.

### Abschließen und die Klärung

Der Abschluss von Kino Nord ist der interessanteste Teil der Demo. Nach der
Rückfrage „wirklich abschließen?“ kommt die Klärung, und **jeder** ihrer sieben
Blöcke hat hier etwas zu fragen:

1. **Nicht mehr aktiv, aber gezählt.** Vier Artikel, darunter zwei eingestellte.
   Drei davon waren beim Zählen noch aktiv und schieden erst danach aus, so wie
   solche Zeilen in der App entstehen. Bei einem ist der Haken gesperrt: Die
   Warenwirtschaft hat den Eishörnchen eine neue Nummer gegeben, und der neue
   Satz führt die Ware. Die Zeile sagt, welcher.
2. **Unerwartet gezählt.** Was in der App dazugelegt wurde. Die Frage ist, ob es
   künftig in der Zählliste dieses Orts stehen soll.
3. **Erwartet, aber nichts eingetragen.** Einzelne Gebinde, die offen blieben.
4. **Gar nicht gezählt.** Artikel ohne jede Eingabe. Hier lässt sich ein Artikel
   inaktiv setzen, einstellen oder in eine andere Inventurart wechseln, etwa in
   die Jahresinventur.
5. **Gezählt, aber ohne Soll.** Drei Artikel, die die Datei aus der
   Warenwirtschaft nicht kennt: einer aus dem Sortiment und zwei aus Block 1,
   die nicht mehr aktiv sind. Ohne Antwort frören sie ohne Differenz ein.
6. **Gezählt, aber in keiner Art dieser Inventur.** Der Weißwein, den jemand im
   Monat mitgezählt hat, und die beiden Jahreswaren aus Block 1. Beim Weißwein
   fragt die Seite, ob die Antwort nur für diesen Artikel gilt oder auch für den
   von Kino Ost.
7. **In einem fremden Behälter gewogen.** Eine Schale, die zum anderen Haus
   gehört und trotzdem auf der Waage stand.

Ohne einen einzigen Haken passiert nichts weiter, abgeschlossen wird trotzdem.
Danach gibt es das PDF, und die Prüfansicht ist vorbei. Wer sie noch einmal
sehen will, baut die Demo mit `--reset` neu auf.

Auch die **Becherinventur** von Kino Nord wartet aufs Abschließen. Dort fragt
die Klärung nach der Art: der große Mehrwegbecher blieb ungezählt und lässt sich
in die Monats- oder die Jahresinventur wechseln. Der Einwegbecher „Becher 0,4 l“
wurde in der App dazugezählt, gehört aber zur Monatsinventur. Ihn bietet der
Block „Gezählt, aber in keiner Art dieser Inventur“ zum Aufnehmen in die
Becherinventur an. Weil Kino Ost denselben Becher führt, kommt die Frage, ob das
auch dort gilt.

Einen Sollbestand hat die Becherinventur nicht, die Differenz kommt **von
Hand**. In ihrer Prüfliste steht deshalb statt Soll und Differenz ein Feld für
die Differenz aus der Warenwirtschaft. Bei den beiden Mehrwegbechern ist schon
eine eingetragen, beim kleinen mit Notiz. Der in der App dazugezählte Becher
wartet noch auf seine. Auf der Seite der Inventur und über ihrer Prüfliste lässt sich die
Differenz von Hand wieder ausschalten. Was eingetragen ist, bleibt dabei stehen.

**Gesperrt** ist die Becherinventur auch: gezählt wird dort nicht mehr, geprüft
schon. Der Knopf mit dem Schloss auf ihrer Seite heißt deshalb „Öffnen“. Offen
heißt er „Sperren“. Abschließen geht in beiden Stellungen.

### Vorbereitung und Sollbestand

Die Jahresinventur am Jahresende steht noch in der Vorbereitung. Nur dort ist
zu sehen, was später verschwindet:

- den **Upload des Sollbestands**, und zwar so oft man will. Die zuletzt
  hochgeladene Datei gilt, ein Rest der vorigen bleibt nicht stehen.
- die **Klärung** dessen, was die Datei offen lässt.
- die **Pflege von Hand**: das Soll je Artikel zum Eintragen, für Häuser ohne
  Datei oder für den Artikel, den sie nicht kennt. Ein Soll, das schon steht,
  ist gesperrt, bis „Bearbeiten“ die Zeile freigibt.
- **Arten ändern**: eine vergessene Inventurart kommt samt ihren Zeilen dazu,
  eine abgewählte nimmt ihre leeren Zeilen mit.

Danach schaltet „Zählen starten“ die Inventur für die App frei. Ab da halten
die Zähl-Apps ihre Zeilen fest, und die Stammdaten sollen sich nicht mehr unter
ihnen bewegen. Deshalb ist die Klärung dann vorbei. Zurück geht es über „Zurück
in die Vorbereitung“, die gezählten Mengen bleiben dabei stehen.

Die Klärung zeigt genau das, was die Datei offen ließ:

- eine Artikelnummer, die es im Haus nicht gibt,
- eine Nummer, die zu einem inaktiven Artikel gehört,
- eine, die zu einem eingestellten gehört,
- in Kino Nord die alte Nummer der Eishörnchen,
- ein Artikel, den die Datei anders nennt als das Haus.

Nicht darunter ist der Tomatensaft von Kino Nord: eingestellt, und die Datei
führt ihn mit Bestand 0. Da sind sich Warenwirtschaft und Haus einig, das Soll
wird still gespeichert, und niemand wird gefragt. In Kino Ost führt die Datei
ihn mit Bestand 9, deshalb ist er dort der eingestellte Artikel in der Klärung.

Die alte Nummer der Eishörnchen führt die Datei noch. Der Satz ist eingestellt,
und dieselbe Ware führt das Haus jetzt unter der neuen Nummer. Die Zeile nennt
sie, und „Bei 101404 dazurechnen“ legt den Bestand dort zum Soll. Danach zählt
die alte Nummer als die neue: Wer die Datei noch einmal hochlädt, findet den
Bestand gleich dort, ohne Frage, und die Seite des alten Artikels sagt, als
welcher er zählt.

Jede Zeile hat ihren eigenen Knopf. „Erneut zuordnen“ nimmt die Klärung auf,
ohne dass man die Datei noch einmal hochlädt.

Den Artikel, den die Datei nicht kennt, hat Kino Ost von Hand eingetragen: auf
„Von Hand pflegen“ steht er unter „Mit Soll“ mit dem Vermerk „von Hand“. In Kino
Nord ist er offen geblieben und steht in der Klärung unter „Aktiv, aber ohne
Soll“.

Dieselbe Datei steckt auch in der laufenden Inventur von Kino Nord. Dort ist
die Klärung nicht mehr erreichbar, ihre Folgen aber schon: beim Abschluss fragt
der Block „Gezählt, aber ohne Soll“ nach dem Artikel, den die Datei nicht
kennt.

### Ein Haus ohne Warenwirtschaft

Die Eismanufaktur Süd beliefert die Kinos mit Eis und führt keine
Warenwirtschaft. Ihre fünf Artikel haben die Quelle „eigen“ und keine Nummer.
Zwei davon sind dieselbe Ware wie im Kino: „Eis Vanille“ steht in Kino Nord in
Stück unter einer WinTICKET-Nummer, in der Manufaktur in Kilo, mit der
Umrechnung dazwischen. So hängen die Häuser über die Ware zusammen, ohne dass
ein System beide kennt. Die Manufaktur steht mit in der Jahresinventur, die
noch vorbereitet wird: ohne Datei, ihr Soll ist für die vier zählbaren Artikel
von Hand eingetragen. Auf „Von Hand pflegen“ stehen dort lauter Zeilen „von
Hand“, und die Klärung hat nichts zu fragen. Der fünfte, der Waffelbruch, steht
versehentlich unter „Nicht zählbar“ (siehe „Ein Versehen zurücknehmen“).

### Die Zähl-App

Unter `/app/` anmelden, am besten als `zaehler`. Die App zeigt die laufende
Inventur, darin die Orte und je Ort die Zählliste in der Reihenfolge des Regals.

Zu sehen sind dort:

- Gebinde nebeneinander. An den Theken steht der übliche Auffüllbestand als
  Vorschlag dabei („Erwartet: 24“), im Lager nicht. Eine Auffüllliste führt
  eben nur, wer auffüllt. Rund ein Viertel der Zeilen hat eine solche Vorgabe.
- Die Waage: je Gefäß eine Wiegung mit eigener Tara. Die Auswahl des Behälters
  ist Pflicht, „Ohne Behälter“ steht für die genullte Waage.
- Rezepturartikel, die als Portion gezählt werden.
- Ein Knopf je Zeile, um sie zu entfernen, und die Suche, um Unerwartetes
  dazuzunehmen.
- Ein Ort, an dem nichts erwartet wird: der „Aktionstisch“ im Foyer von Kino
  Nord. Was dort steht, wechselt mit dem Film, eine Zählliste führt dafür
  niemand. Bereich und Ort stehen trotzdem in der App, mit „nichts erwartet“
  statt eines Fortschritts. Was doch dort steht, kommt über die Suche dazu.
- „+ dazu“ und „− ab“ neben einer Zahl: ein zweiter Fund wird hinzugezählt,
  ein doppelt gezählter Kasten wieder abgezogen, ohne im Kopf zu rechnen.
  Unter null geht es nicht.
- Der Knopf zum Auffrischen oben rechts holt auch die Stammdaten neu. Wer in
  der Weboberfläche einen Ort oder einen Artikel anlegt, während die App die
  Inventur schon geladen hat, sieht ihn nach einem Druck darauf. Der
  Abgleich im Hintergrund bringt nur die Zahlen.
- Ein Reiter je Inventurart, soweit der Ort Zeilen dafür hat: im
  Getränkelager von Kino Nord „Monatsinventur“ und „Becherinventur“. Kino Ost
  führt keine Becher und sieht dort keinen zweiten Reiter. Was zu einer Art
  gehört, die die Inventur nicht zählt, bekommt keine Zeile. Wer es trotzdem
  zählen will, holt es über die Suche, die seine Art dazuschreibt. Es steht dann
  im ersten Reiter wie jeder andere unerwartete Fund. In der Demo ist das der
  Weißwein in Kino Nord.
- Die **Prüfliste** über den Bereichen einer Inventur: je Artikel die Differenz
  groß in ihrer Farbe, darunter Ist und Soll, mit Suche und den Filtern
  „Abweichung“ und „Ungezählt“. Der Stift öffnet die Notiz, das × daneben steht
  nur, wo es eine gibt. In der Becherinventur von Kino Nord ist die Differenz
  dort ein Feld, auch für `zaehler`. Ein- und ausschalten können es `leitung`
  und `admin`. Sie setzen dort auch „Soll zählt“, je Zeile mit Soll ein Knopf.
  Beim Colasirup ist er schon gedrückt, `zaehler` sieht dort nur den Hinweis.
- Die gesperrte Becherinventur: In der Liste trägt sie ein Schloss, und geöffnet
  zeigt sie gleich die Prüfliste, ohne Bereiche und Suche. `leitung` und `admin`
  sehen dort den Knopf, der das Zählen wieder freigibt.

Die App arbeitet offline und gleicht ab, sobald sie wieder Netz hat. Wer das
sehen will, schaltet im Browser auf „offline“, zählt weiter und schaltet zurück.

### Auswertung, PDF und Verlauf

Die vier abgeschlossenen Läufe liegen als eingefrorene Ergebnisse vor. Damit
lässt sich anschauen:

- die Ergebnisliste je Inventur, mit Ist, Soll und Differenz,
- das PDF je Haus und über den ganzen Lauf. Das über den Lauf beginnt mit den
  Warenfamilien allein, gegliedert wie die Blöcke dahinter, „Knabbereien“ darin
  in Portionen (auf Papier „Port.“), und nennt die Seite mit ihren Waren,
- die Auswertung über mehrere Zeiträume und Häuser hinweg,
- wie sich dieselbe Ware über die Monate entwickelt, denn die Differenzen
  wandern von Lauf zu Lauf.

Die Jahresinventur vom Dezember zählt alle drei Arten, also auch die Waren, die
monatlich nicht gezählt werden. Der Vergleich mit einer Monatsinventur zeigt den
Unterschied.

### Den Katalog durchstöbern

Unter „Stammdaten“ liegen die Listen, die die Stammdaten tragen:

- **Waren** mit Einheit, Familie, Gebinden und den Häusern, die sie führen.
  Zwei Waren lassen sich zusammenführen, wenn sich herausstellt, dass es
  dieselbe ist. Und eine Ware lässt sich aufteilen, wenn sich herausstellt,
  dass zwei darin stecken. Die Hausartikel der betroffenen Häuser wandern
  dann zu einer neuen Ware, samt ihren Gebinden.
- **Rezepturartikel** mit ihrer Rezeptur.
- **Behälter** mit Tara, zugeordnet an ein Haus oder an eine einzelne Ware.
- **Inventurarten** mit ihrer Reihenfolge, die auch die der Reiter ist, und
  den aktiven Artikeln je Haus. Die Becherinventur hat nur in Kino Nord welche.
  Die Zahl der Artikel führt zur Warenliste, gefiltert auf die Art. Der
  Weißwein steht dort unter Monats- und Jahresinventur, und der Filter
  „gemischt“ findet ihn allein.
- **Warenfamilien** über mehrere Dimensionen hinweg. „Limonaden“ etwa führt
  Flaschen und Sirup zusammen und summiert in Litern. Was eine Flasche fasst
  und wiegt, steht als Umrechnung an der Ware: die „Cola 0,5 l“ mit 500 ml
  und 520 g je Stück. Die Waage und die Summe der Familie rechnen damit.
  Zwei Familien stehen über anderen: „Getränke“ fasst Limonaden, Säfte und
  Wasser sowie Bier und Wein zusammen und summiert in Litern, „Snacks“ die
  fünf Familien vom Popcorn bis zum Herzhaften in Kilogramm. In Prüfliste
  und Auswertung samt ihrem PDF stehen sie als Baum: zugeklappt nur die
  oberste Ebene, jede Familie mit der Summe über alles darunter. Das Blatt
  einer einzelnen Inventur gliedert dagegen nach Warengruppe und
  Artikelnummer, so wie die Warenwirtschaft ihre Artikel führt. „Knabbereien“ darunter
  summiert in **Portionen**, der Einheit, die nur eine Familie führt: der
  Schokoriegel ist eine Portion, die losen Salzbrezeln sind 50 g je Portion.
- **Artikel je Haus**, mit Suche über Nummer, Bezeichnung, Ware und Warengruppe.
  Die Treffer stehen danach, wie gut sie passen. Wer in Kino Nord „popcorn“
  tippt, sieht zuerst „Popcornsalz“ und „Popcornzucker“ und erst dahinter das
  „Kokosfett“, das nur in derselben Warengruppe steht. Ein Klick auf einen
  Spaltenkopf sortiert stattdessen nach der Spalte.
  Zwei Artikel in Kino Ost zählen in einer anderen Einheit als ihre Ware: die
  Nachochips in Gramm statt Kilogramm, die Kaffeebohnen in Beuteln zu je einem
  Kilo. Das Haus bekommt Sollbestand und Ergebnis in seiner Einheit, die
  Auswertung rechnet in die der Ware um. Ein abgeschlossenes Ergebnis behält die
  Einheit, in der gezählt wurde. Die Kaffeebohnen zählt Kino Ost erst seit der
  laufenden Inventur in Beuteln, in den abgeschlossenen stehen sie in kg.
- Am Ort die **Zählliste** mit zwei Reitern: die Reihenfolge zum Ziehen und die
  Artikelauswahl. Die Vorgabemengen stehen dort je Gebinde und sind an den
  Theken gepflegt, im Lager leer. Wer die Reihenfolge ändert, sieht sie sofort gespeichert.
  Ein Knopf über der Spalte wählt alle Gebinde der Liste an oder wieder ab.
  Ein neu aufgenommener Artikel kommt mit allen aktiven Gebinden angehakt,
  aber ohne den Haken „Wiegen“, außer es gibt für ihn nichts als die Waage.
  Eine Zeile ganz ohne Gebinde nimmt „Speichern“ nicht an, denn der Artikel
  bekäme dort keine Zeile in der Inventur. Die Liste ist nach Inventurarten
  unterteilt, im Getränkelager von Kino Nord in Monats-, Jahres- und
  Becherinventur. Gezogen wird nur innerhalb eines Abschnitts. Wer dort den
  Weißwein (nur diesen Artikel) in die Monatsinventur setzt, findet ihn danach
  am Ende ihres Abschnitts.

Zehn Wege durch die Pflege, die sich mit dem Demo-Bestand ausprobieren lassen:

- **Zwei Waren zusammenführen.** Kino West führt nach dem ersten Import (siehe
  unten) „Sirup Cola 10 l“ als eigene Ware, obwohl es der „Colasirup“ der
  anderen Häuser ist. Auf der Seite der Ware: „Zusammenführen“, die andere
  wählen, entscheiden, wer bleibt. „Vorschau“ zeigt, was mit den Gebinden
  geschieht, „Endgültig zusammenführen“ führt es aus. Die Hausartikel von Kino
  West hängen danach an der bleibenden Ware.
- **Doppelt angelegte Artikel zusammenlegen.** In Kino Nord den Artikel 101607
  „Becher 0,4 l“ öffnen, dann „Zusammenführen“ und unter den Artikeln des Hauses
  101613 „Trinkbecher 0,4 l“ wählen. Dasselbe geht auf der Seite der Ware. Weil
  Kino Nord beide führt, fragt die Vorschau je Erwartung des Trinkbechers, ob
  sie übernommen wird. Danach ist 101613 eingestellt und zählt
  als 101607. Am Becher selbst ändert sich nichts. Beide standen an denselben
  Orten, also sind die Vorgabemengen dort zusammengezählt. Wer danach in der
  vorbereiteten Jahresinventur von Kino Nord den Sollbestand erneut einliest,
  sieht den Bestand von 101613 beim Becher dazugerechnet. Wer vorher auf der
  Seite der Ware „Becher 0,4 l“ den Karton auf inaktiv schaltet, sieht ihn in
  der Vorschau hervorgehoben, denn der Karton des Trinkbechers ginge darin auf.
  Geschrieben wird erst nach „Wieder aktivieren“ oder „Inaktiv lassen“.
- **Eine Inventurart anlegen.** Unter Stammdaten → Inventurarten eine neue Art
  anlegen, etwa „Quartalszählung“. Auf der Seite einer Warengruppe setzt die
  Auswahl oben alle ihre Hausartikel in die Art, auf der Seite einer Ware alle
  Artikel der Ware. Am einzelnen Hausartikel fragt die Auswahl, ob sie nur für
  ihn gilt oder für die ganze Ware. „Neue Inventur“ bietet die Art danach an,
  und wählbar sind nur die Häuser mit aktiven Artikeln darin.
- **Eine Ware aufteilen.** „Eishörnchen“ führen beide Häuser. Auf der Seite der
  Ware: „Aufteilen“, die Hausartikel von Kino Ost auf die andere Seite
  schieben, der neuen Ware einen Namen geben. Beide Hälften behalten Gebinde
  und Zählpositionen.
- **Die Einheit einer Ware wechseln.** „Sprühsahne“ wird in Gramm geführt. Auf
  der Seite der Ware „Bearbeiten“, Einheit auf kg stellen: Gebinde und
  Rezepturen werden umgerechnet. Die Häuser zählen weiter in ihrer Einheit,
  ihre Ergebnisse bleiben. Wer stattdessen auf Stück wechselt, muss den Faktor
  angeben. Bei der „Cola 0,5 l“ nicht: Sie kennt ihr Stück (500 ml), und der
  Wechsel auf Stück rechnet damit.
- **Die Ware vom Hausartikel aus ändern.** In Kino Nord den Artikel
  „Cola 0,5 l“ öffnen, dann „Bearbeiten“. Unter „Einstellungen der Ware“ stehen
  ihr Name, ihre Familie, das Wiegen und die Umrechnungen.
  Der Hinweis darüber nennt Kino Ost, denn die Angaben gehören der Ware und
  gelten dort genauso. Die Einheit bleibt auf der Seite der Ware.
  Auf der Seite des Artikels selbst lässt sich die Reihenfolge der Gebinde
  ziehen, wie auf der Seite der Ware. Auch sie gilt in jedem Haus.
- **Ein Gebinde auf inaktiv setzen.** Auf der Seite der Ware „Cola 0,5 l“ den
  Kasten auf inaktiv schalten. In der Zählliste des Getränkelagers von Kino Nord
  steht seine Vorgabe danach grau mit „inaktiv“, und eine neue Inventur gibt ihm
  keine Zeile. „Speichern“ verlangt dort jetzt ein anderes Gebinde, sonst bekäme
  die Cola im Lager gar keine Zeile mehr. Wer danach den Artikel „Cola 0,5 l“
  von Kino Nord bearbeitet und unter „Gebinde“ einen Kasten mit 24 einträgt,
  bekommt die Zeile markiert zurück. Gespeichert wird erst nach „Wieder
  aktivieren“ oder „Inaktiv lassen“. Genauso fragt ein neuer Hausartikel zur
  Cola, etwa in Kino West vor dem Import. Der Import von Kino West dagegen holt
  den Kasten zurück, denn seine Datei führt ihn. Der Plan sagt es vorher bei der
  Cola, und wer das nicht will, nimmt dort die Zeile des Kastens aus dem Feld.
- **Einen Ort aufteilen oder zusammenführen.** In Kino Nord steht „Theke links“
  neben „Theke rechts“. Auf der Seite des Orts lassen sich beide
  zusammenführen. Die Zählliste hängt sich hinten an. „Aufteilen“ geht den
  umgekehrten Weg. Dasselbe gibt es für Warenfamilien, etwa um „Süßwaren“ in
  lose Ware und Riegel zu trennen.
- **Einen Ort kopieren.** Auf der Seite von „Theke links“ in Kino Nord:
  „Kopieren“. Die Kopie bekommt dieselbe Zählliste, in einem beliebigen
  Bereich und unter eigenem Namen. Wer einen Bereich von Kino Ost wählt, sieht
  in der Vorschau, welche Artikel dort nicht geführt werden. Sie fehlen in der
  Kopie.
- **Ein Versehen zurücknehmen.** Der Waffelbruch der Eismanufaktur steht unter
  „Nicht zählbar“, weil sich jemand verklickt hat. Auf der Seite der Ware holt
  „Wieder aufnehmen“ ihn zurück: in die Familie „Zutaten“, und sein Artikel ist
  wieder aktiv und steht wieder im Rohstofflager. Familie, Status und Platz hat
  er sich beim Hineinwechseln gemerkt.

### Ein Haus aufsetzen

Kino West ist leer. So wird daraus ein zählbares Haus, und das ist zugleich der
Weg, den ein echtes neues Haus nimmt:

1. **Artikel importieren.** Stammdaten → Häuser und Orte → Kino West → „Importieren“,
   dann `server/demo/exports/artikel_kino_west.csv` hochladen. Der Plan zeigt
   alle Zeilen als neu. Zu den meisten schlägt er die passende Ware aus den
   anderen Häusern vor, denn dort gibt es sie schon. Der Import schreibt erst
   nach dem Übernehmen.
2. **Bereiche und Orte anlegen**, etwa „Theke“ mit „Theke links“ und „Lager“
   mit „Getränkelager“.
3. **Zähllisten füllen.** Am Ort im Reiter „Artikel“ auswählen, was dort steht,
   und je Zeile die Gebinde prüfen. Die aktiven sind schon angehakt.
4. **Inventur anlegen** für Kino West, Art und Stichtag wählen. Mit dem Haken
   „Zählende gleich hier festlegen“ kommt `zaehler` gleich dazu, ohne ihn wird
   danach an der Inventur zugeordnet.
5. **Sollbestand hochladen** in der Vorbereitung:
   `server/demo/exports/sollbestand_kino_west.xlsx`, das Zählblatt, wie
   WinTICKET es ausgibt, oder dieselben Zahlen
   als `sollbestand_kino_west.csv`. Eine Nummer darin kennt
   das Haus nicht, sie wird zur Frage. Die Pfandartikel zählt der Bericht und
   übergeht sie.
6. **Zählen starten.** Erst damit erscheint die Inventur in der App.

### Ein zweiter Import

`server/demo/exports/artikel_kino_ost.csv` ist der Fall, der im Betrieb
häufiger vorkommt: ein Haus ist gepflegt, und die Warenwirtschaft liefert eine
neue Datei. In den Plan kommt nur, was eine Entscheidung braucht. Enthalten
sind alle Arten:

| Art | Was in der Datei steht |
| --- | --- |
| neu | Zwei Nummern, die das Haus nicht kennt |
| eingestellt | Ein Artikel, der sich zurückholen ließe |
| umbenannt | Dieselbe Nummer unter anderer Bezeichnung |
| Konflikt | Eine andere Warengruppe, und zweimal eine Anzahl je Gebinde, die die Ware nicht führt |
| fehlt | Zwei Nummern, die das Haus führt und die Datei nicht mehr kennt |

Solange ein Konflikt offen ist, bleibt der Knopf gesperrt. Mit der Wahl wandert
die Zeile in den Block „Behobene Konflikte“. Die Zeilen, die das Haus genau so
führt, stehen nicht im Plan, sondern nur in der Meldung darüber.

Der Export lässt eine Warengruppe ganz aus („290 EK - Sonstiges“). Genau so
arbeitet die Warenwirtschaft: man wählt die Warengruppen, und die kommen
vollständig. Die drei Artikel darin bleiben deshalb unberührt, und die Meldung
über dem Plan sagt es. Der Block „Fehlt in der Datei“ zeigt nur die beiden
Nummern aus Warengruppen, die wirklich exportiert wurden.

Jeder Block ist nach Warengruppen gegliedert. Beide Ebenen klappen auf und zu,
und im Kopf einer Warengruppe setzt ein Knopf alle ihre Zeilen auf einmal.

### Das eigene Logo

Oben links steht das ausgelieferte Logo, das blaue. Es gehört zur
Fassung **Standard** unter **Organisation → Erscheinungsbild und Betreiber**, zusammen
mit dem Namen „CrossTally“, dem leeren Betreiber, dem leeren Impressum und dem
mitgelieferten Datenschutztext. Die Fassung
lässt sich nicht ändern, nur kopieren, und die Demo liefert sie so aus, wie
ein frischer Betrieb sie vorfindet.

Zum Ausprobieren liegt ein Logo bereit: `server/demo/images/logo_demo.png`,
dieselbe Strichliste in Türkis. Der Weg: die Zeile „Standard“ anhaken, **Kopie
anlegen**, die Kopie öffnen, ihr unter „Name der Anwendung“ einen eigenen Namen
geben (etwa „Kino-Inventur“), das Bild hochladen (als Hintergrund
passt `#0f766e`) und speichern, dann in der Liste die Kopie anhaken und **Als aktive
Fassung wählen**. Danach stehen Name und Logo oben links, im Browsertab und am
Icon der Zähl-App. Zurück zum blauen Logo heißt: den Standard wählen. In
derselben Kopie lässt sich ein Impressum eintragen, etwa eine erfundene
Anschrift mit einer Zeile je Angabe. Dann steht im Fuß jeder Seite und im
Kontomenü der Zähl-App ein Verweis darauf. Ebenso lässt sich dort eine Adresse
für Rückmeldungen eintragen, etwa `rueckmeldung@example.org`. Die Domain ist
für Beispiele reserviert, eine Mail dorthin erreicht niemanden. Dann bietet das
Kontomenü der Website und der Zähl-App „Problem oder Wunsch melden“ an, und das
Mailprogramm öffnet sich mit Version, Seite, Konto, Zeit und Browser im Text.
`manage.py demo --reset` nimmt eigene Fassungen ebenfalls mit.

Es reicht eine Datei. Favicon, das Icon für iOS und die vier Größen für den
Startbildschirm entstehen daraus. Angenommen wird nur ein quadratisches PNG von
mindestens 512 x 512 Pixeln, und geprüft wird der Inhalt der Datei, nicht ihr
Name. Wer eine umbenannte Textdatei hochlädt, bekommt es gesagt.

### Verwaltung und Backups

Als `admin` gibt es die Verwaltung unter der Adresse aus `ADMIN_URL` und darin
die Knöpfe für Backups. Ein Backup der Demo ist ein guter Weg, um den Stand
festzuhalten, bevor man etwas ausprobiert, das sich nicht zurücknehmen lässt.
Eingespielt wird aber nur, solange nirgends gezählt wird. Die Demo hat drei
laufende Zählungen: vorher jede „Zurück in die Vorbereitung“ schicken oder
abschließen. Das Band kommt mit dem Stand zurück, ein Stand aus einem echten
Betrieb nimmt es weg. Das Logo liegt in der Datenbank und ist darin enthalten.
Nach dem Einspielen ist jeder abgemeldet, auch `admin`: auf der Website
sofort, in der Zähl-App spätestens nach einer Stunde.
Steht ein `BACKUP_KEY` in der `server/.env`, wird das Backup verschlüsselt
geschrieben. Beim Entwickeln ist der Schlüssel freiwillig, im Betrieb Pflicht.
Ist einer gesetzt, spielt die Verwaltung nur verschlüsselte Stände ein. Einen
älteren ohne Verschlüsselung verschlüsselt vorher `manage.py backup store`
(siehe DEPLOYMENT.md, „Zurückspielen“).

Unter `/admin/` liegt kein Admin, sondern ein Köder mit derselben Maske.
Anmeldeversuche landen in der Verwaltung unter „Anmeldeversuche am falschen Admin“.

## Die Dateien

Sie liegen in `server/demo/exports/` und sind erfundene Exporte im Format der
Warenwirtschaft. Dieselben mit englischen Bezeichnungen stehen daneben
in `server/demo/exports/en/`.

| Datei | Wofür |
| --- | --- |
| `artikel_kino_west.csv` | Erster Import, setzt das leere Haus auf |
| `artikel_kino_ost.csv` | Zweiter Import in ein gepflegtes Haus, mit allen Arten |
| `sollbestand_kino_nord.csv` | Sollbestand der laufenden Inventur, schon eingelesen |
| `sollbestand_kino_ost.csv` | Dasselbe für das zweite Haus |
| `sollbestand_kino_west.csv` | Für die Inventur, die nach dem Import entsteht |
| `sollbestand_kino_west.xlsx` | Dasselbe als Zählblatt, wie WinTICKET es ausgibt |

Dazu liegt in `server/demo/images/` ein Logo zum Hochladen (`logo_demo.png`),
daneben die SVG-Datei, aus der es gerendert wurde. Es ist die Strichliste des
Platzhalters in anderen Farben.

Die Artikeldateien tragen den Aufbau, den die Warenwirtschaft ausgibt: eine
Zwischenzeile je Warengruppe, darunter je Artikel eine Zeile und darunter eine
Infozeile „gekauft in … zu …“. Daraus liest der Import die Gebindegröße.

Sie tragen auch die Schreibweisen, wie die Warenwirtschaft sie ausgibt:
„Kiste(n)“, „Sack(Säcke)“, „Karnister“, „ltr“, „gramm“. Der Import macht daraus
die Begriffe des Hauses („Kasten“, „Sack“, „Kanister“) und die fünf erlaubten
Einheiten. Was sich nicht als Einheit erkennen lässt, etwa „Becher“, wird
gezählt, also in Stück geführt. Bei Ware in Stück steht im Gebindefeld immer
auch „Einzeln = 1“, denn im Regal steht eine angebrochene Packung.

Die Sollbestandsdateien sind schlicht: `Artikelnummer;Bestand;Bezeichnung`. Die
Bezeichnung ist freiwillig. Weicht sie von der im Haus ab, sagt es die Klärung.
Das Zählblatt (`sollbestand_kino_west.xlsx`) trägt dieselben Zahlen so, wie
WinTICKET sie druckt: eine Kopfzeile je Seite, eine Lagerzeile, hinter dem
Istbestand die leeren Spalten zum Eintragen. Es entsteht aus der CSV
(`uv run python -m demo.count_sheet`), ein Test hält beide zusammen.

## Neu anfangen

```bash
uv run manage.py demo --reset
```

Das löscht alles, was die Demo anlegt, und baut sie neu auf. Vorher nennt der
Befehl, was in dieser Datenbank steht, und fragt nach. **Gelöscht wird dabei
jedes Haus, jede Ware und jede Inventur, auch eine, die erst beim Ausprobieren
dazukam.** Das geht nur in einer Datenbank, die die Demo trägt, erkennbar an
ihrem Kennzeichen. In jeder anderen weigert sich der Befehl auch mit `--reset`,
sobald dort etwas steht. Ohne `DEBUG` gehört `--demo-server` dazu. Die
Stammdaten der Anwendung bleiben stehen: die Basiseinheiten, der Behälter „Ohne
Behälter“ und die Warenfamilie „Nicht zählbar“ kommen aus einer Migration und
gehören nicht zur Demo.

## Für Entwickler

Die Demo liegt als App im Repo und nicht als fertige Datenbank. Der Grund ist
die Pflege: ein Abzug veraltet stumm, sobald sich ein Modell ändert.

| Datei | Inhalt |
| --- | --- |
| `server/demo/catalog.py` | Warenfamilien, Behälter, Waren, Gebinde, Rezepturartikel |
| `server/demo/houses.py` | Häuser, Bereiche, Orte, Artikel je Haus, Zähllisten |
| `server/demo/inventories.py` | Die Läufe, das Zählen und die gezielten Fälle für die Klärung |
| `server/demo/users.py` | Die vier Konten |
| `server/demo/branding.py` | Die Fassung zum Ausprobieren: Logo, Impressum, Rückmeldeadresse |
| `server/demo/count_sheet.py` | Baut das Zählblatt aus der CSV |
| `server/demo/files.py` | Die Dateien und ihr Weg |
| `server/demo/saving.py` | Jeder Satz geht durch die Prüfung des Modells |
| `server/demo/management/commands/demo.py` | Der Befehl `manage.py demo` mit seinen Schaltern |
| `server/demo/names_en.py` | Dieselben Namen auf Englisch, samt der Tabelle für die Importdateien |
| `server/demo/builder.py` | Setzt es zusammen, räumt es wieder weg und erkennt einen Bestand, der nicht aus der Demo stammt |
| `server/demo/models.py`, `state.py` | Das Kennzeichen, an dem die Oberfläche die Demo erkennt |
| `server/demo/tests.py` | Der Vertrag, siehe unten |

Der Aufbau geht durch die Dienste der Anwendung, nicht an ihnen vorbei. Die
Inventuren entstehen mit `inventuren_anlegen`, der Sollbestand kommt
über `sollbestand_einlesen` aus einer Datei, und abgeschlossen wird
mit `Inventur.abschliessen`. Ändert sich dort etwas, bricht der Aufbau.

`server/demo/tests.py` hält die Demo an der Anwendung fest. Die Tests prüfen
nicht, ob bestimmte Zahlen herauskommen, sondern ob die Demo zeigt, was sie
verspricht: dass jeder Block der Klärung eine Frage hat, dass die Sollklärung
ihre Fälle kennt, dass die abgeschlossenen Läufe Ergebnisse tragen und dass
sich die mitgelieferten Dateien einlesen lassen. Sie laufen in der CI mit.

Auch der Durchgang durch die Zähl-App im echten Browser (`app/e2e/`) baut auf
der Demo auf: Er meldet sich als `zaehler` an und zählt in der laufenden
Inventur, übernimmt die Anmeldung von `leitung` von der Website und wiegt im
Süßwarenregal an der Theke von Kino Nord. Fällt eines davon aus der Demo,
bricht er.

**Wer ein Feature ergänzt, ergänzt die Demo.** Ein neues Feld gehört in einen
der Baupläne, ein neuer Fall in die Tabellen von `inventories.py`, und der Test
dazu sagt, dass er in der Demo auch wirklich vorkommt.

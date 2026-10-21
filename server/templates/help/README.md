# Hilfetexte

Hier liegen die Erklärungen, die in der Weboberfläche hinter dem Knopf **Hilfe**
stehen. Auf den Seiten selbst steht nur das Nötigste. Wer mehr wissen will, öffnet
die Seitenleiste.

## Wo die Texte liegen

Je Sprache ein Ordner: `de/` und `en/`. Deutsch ist die Ausgangssprache und hat
jeden Text. Fehlt ein Text auf Englisch, erscheint der deutsche. Lieber eine
Erklärung in der falschen Sprache als gar keine.

Die Hilfe läuft **nicht** über gettext. Es sind lange Fließtexte, und die stehen
besser als ganze Seite nebeneinander als in hundert Bruchstücken im Katalog.

## Einen Text ändern

Jede Datei gehört zu genau einer Seite und heißt wie sie: `de/ware_list.html`
ist die Hilfe zur Liste der Waren, `de/inventur_detail.html` die zur Seite einer
Inventur. Anlegen und Bearbeiten teilen sich eine Datei (`behaelter_form.html`).
Wer einen Text ändert, ändert ihn in beiden Sprachen.

Geschrieben wird einfaches HTML:

```html
<h3>Zwischenüberschrift</h3>
<p>Ein Absatz. <strong>Wichtiges</strong> fett, <em>Begriffe</em> kursiv.</p>
<ul>
    <li>Ein Punkt einer Aufzählung.</li>
</ul>
```

Angaben der Seite lassen sich einsetzen, etwa `{{ haus.name }}`. Zur Verfügung
steht, was auch die Seite selbst kennt.

## Eine neue Hilfe anlegen

Eine Datei mit dem Namen der Seite in `de/` genügt, dazu dieselbe in `en/`. Der Knopf
erscheint dann von selbst. Hat eine Seite keinen Text, gibt es dort auch keinen Knopf.
Welche Namen es gibt, steht in den `urls.py` der Apps (`name="…"`). Wo der Name deutsch
ist, steht der Dateiname in `django_project/help.py` unter `DATEINAMEN`.

## Was mehrere Seiten erklären

Unter `<sprache>/shared/` liegen Abschnitte, die an mehreren Stellen gebraucht werden:
die vier Artikelstatus, die Inventurart, der Sammelstatus der Waren, die Reihenfolge der
Suchtreffer. Eingebunden werden
sie mit `{% include "help/de/shared/artikelstatus.html" %}`. Jede Sprache bindet ihre
eigenen ein. Wer dort etwas ändert, ändert es in dieser Sprache überall.

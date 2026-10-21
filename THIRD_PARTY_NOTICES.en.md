# Third-party components

*[Deutsche Fassung](THIRD_PARTY_NOTICES.md)*

The project itself is under the MIT licence ([LICENSE](LICENSE)). But it also
ships third-party code, in the repo and in the Docker image. That code stays
under the licences of its authors. This file says what is where and where the
respective licence text lives. Whoever passes the project or the image on passes
these texts on with it.

## In the web interface

These libraries live ready-made in the repo and are served by the application
itself. They are fetched and checked against an SRI hash
by `server/bin/vendor.sh`, and so are their licence texts.

| Library | Version | Licence | Licence text |
| --- | --- | --- | --- |
| Bootstrap | 5.3.8 | MIT | `server/static/vendor/bootstrap/LICENSE.txt` |
| Popper (inside `bootstrap.bundle.min.js`) | 2.11.8 | MIT | `server/static/vendor/bootstrap/LICENSE-popper.txt` |
| Tom Select | 2.4.3 | Apache-2.0 | `server/static/vendor/tom-select/LICENSE.txt` |
| SortableJS | 1.15.6 | MIT | `server/static/vendor/sortablejs/LICENSE.txt` |

Tom Select also bundles `@orchidjs/sifter` and `@orchidjs/unicode-variants`.
Both are under Apache-2.0 as well, and the licence text is the same. None of
the packages ships a NOTICE file.

**Changed** in these files is one thing only: the reference to the source map at
the end of the file is replaced by a note, because the maps are not shipped. The
note is in every file concerned.

## In the server's code

The decoy under `/admin/` (`server/accounts/honeypot.py`) and the model of
the sign-in attempts (`server/accounts/models.py`) go back to the package
django-admin-honeypot 1.2.0 (MIT, Copyright (c) Derek Payton). The code is
rewritten and shortened, the idea and the table remain. The licence text is
in `server/accounts/LICENSE-admin-honeypot.txt`.

## In the counting app

The counting app is built from npm packages. Which of them end up in the shipped
JavaScript is determined by the build itself (`app/bin/licenses.mjs`). It
writes their names, versions and full licence texts, including NOTICE files,
to `third-party-licenses.txt` next to the app. In operation the file is
at `/app/third-party-licenses.txt`. If a package lacks its licence text, the build
fails.

That covers above all Dexie (Apache-2.0, with NOTICE), Svelte and Workbox (both
MIT). The authoritative list is the file, not this paragraph.

The packages needed only for building and testing (Vite, Vitest, Prettier,
Playwright …) are not shipped. The exception are the small helpers of Vite
and `vite-plugin-pwa` that end up in the bundle. They are therefore in the file as
well.

## In the Docker image

- **Python packages:** every package ships its licence text in its
  `*.dist-info` folder (`/app/.venv/lib/python3.*/site-packages/`). The licences
  are almost entirely MIT, BSD or Apache-2.0. Two exceptions:
  - `psycopg` and its C implementation `psycopg-c` are under LGPL-3.0. Both are
    installed unchanged as packages of their own and can be swapped for another
    version, as the LGPL requires. The C implementation is compiled from its
    source when the image is built and bundles nothing. The libpq library
    through which both talk to PostgreSQL comes from Debian (see below).
  - `pyphen` (the hyphenation of WeasyPrint) is available under GPL-2.0+,
    LGPL-2.1+ or MPL-1.1, at your choice. Here the **MPL-1.1** applies. pyphen,
    too, is installed unchanged as a package of its own. Its dictionaries carry
    their own licence notes in `pyphen/dictionaries/`.
- **System packages** from the base image and from `apt` (Pango, HarfBuzz,
  libpq, the DejaVu font): their licence texts are at
  `/usr/share/doc/<package>/copyright`.
- **Static files of third-party packages:** `collectstatic` also gathers, at
  build time, what Django and the Django REST Framework bring along, among them,
  for the latter's browsable interface, Bootstrap 3 (MIT), the Glyphicons
  Halflings (usable only with Bootstrap 3) and Font Awesome (SIL OFL 1.1 for
  the font, MIT for the CSS). In operation that interface is switched off, but
  the files are shipped under `/app/staticfiles/`. Their licence texts are in
  the packages under `site-packages/`.
- This file, its German version and `LICENSE` are in the image under `/app/`.

## Trademarks

“Compeso” and “WinTICKET” are trademarks of their respective owners. This
project is not affiliated with them.

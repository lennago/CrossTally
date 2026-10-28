# Deployment

*[Deutsche Fassung](DEPLOYMENT.md)*

The short path to a running version is in the README
([“Running it with Docker”](README.en.md#running-it-with-docker)). This text is the reference behind it. The front part covers
operation in the order you need it: what runs, what the machine needs, the
first setup, backups, updating, operator and privacy. The back part holds what
only matters for forks and contributors. For development none of this is
needed. There it stays with uv, SQLite and `runserver`.

## What runs

```text
compose.yaml
├── db      PostgreSQL 18, own volume, health check
├── web     this project as one image: Django + the built counting app
└── proxy   Caddy, handles TLS and nothing else
```

**One image, four stages** (`Dockerfile`): Node builds the PWA, a second stage
the Python environment, a third gathers the application's files, and the last
takes only the results along. Node and the compiler stay behind. The app is not
a service of its own but a set of files. A second image would be a second truth
about the same build. The application runs as the user with uid 1000. It can
read its code and its packages but not change them.

**psycopg talks to PostgreSQL in its C implementation**, built against the
system's libpq. With the pure Python implementation, pages that read a lot took
up to twice as long. If the C implementation is missing, the build fails.

**Django serves the files itself**, through WhiteNoise (`wsgi.py`): the static
files under `/static/` and the counting app under `/app/`. That way the proxy
does not have to share a directory with the application. The path `/app/` is
mandatory, because a service worker only controls the path it is served from
(its scope).

**The proxy caps the size of a request body** at 12 MB (`request_body` in
the `Caddyfile`). Otherwise an oversized upload fills the disk, because
Django's own limits leave the file parts unbounded. The 12 MB are above the
largest allowed upload (10 MB, `MAX_UPLOAD_MB`
in `server/django_project/validation.py`). Whoever raises that in a fork raises
the limit in the `Caddyfile` with it.

**The licences travel along.** Under `/app/` the image contains its
own `LICENSE` and `THIRD_PARTY_NOTICES.md` in both languages, plus the licence
texts of all
third-party parts: those of the libraries under `static/vendor/`, those of the
counting app in `pwa/third-party-licenses.txt` (written by the build, in the
browser at the address `/app/third-party-licenses.txt`) and those of the Python
packages in their `*.dist-info` folders.

Docker collects the logs of the three services as files
under `/var/lib/docker/containers/`. The `compose.yaml` limits them to five
files of 20 MB per service, otherwise they would grow to gigabytes over months,
with addresses and paths in them. `docker compose logs web` thus shows the last
days.

## What the machine needs

The load is small. Few devices count at the same time, and their requests are
short. What costs computing time are the reports, the PDFs and large imports.

| | At least | Comfortable |
| --- | --- | --- |
| Processor | 2 cores, x86-64 | 4 cores, x86-64 |
| Memory | 2 GB | 4 GB |
| Disk | SSD, 20 GB | SSD, 32 GB |

A used office PC or a mini PC is enough. The stack has only been tested on
x86-64.

**Memory:** when idle, the three services together take around 500 MB, almost
all of it the application server with its four workers. A worker takes around
70 MB when fresh. PDFs and large import plans make it grow considerably, an
import plan of 600 lines by around 170 MB in one measurement. It does not give
that memory back. That is why gunicorn restarts every worker after about 500
requests, between two requests, without anyone noticing. It gets tight on the
smallest machine when several workers handle large imports or PDFs at once.
Then `GUNICORN_WORKERS=2` lowers the need, and the counting app waits while two
such requests run.

**Processor:** the time of a page is spent in Python and not in the database.
What counts is therefore how fast a single core is. More cores only help once
several managers run reports at the same time.

**Disk:** the three images take around 0.9 GB, and after an update the old one
still sits next to the new one. The database and the backups hardly matter
compared to that. More important than the size is the kind: PostgreSQL writes
often and in small pieces, hence an SSD.

**The image is only built for amd64.** An ARM machine such as a Raspberry Pi
needs a self-built image (see “Building it yourself”), a 64-bit system, at
least 4 GB of memory and an SSD. On the SD card, Postgres gives out under the
many small writes.

## Setting it up the first time

### Prerequisites

- **A machine** as above, with Linux, Docker and the Compose plugin
  (version 2, that is `docker compose`).
- **A name and a certificate.** With a domain of your own, Caddy fetches the
  certificate itself, through the ACME procedure, from Let's Encrypt for
  instance. For that a DNS record (A or AAAA) points to the public address of
  the machine, and ports 80 and 443 are reachable from the internet: open in
  the firewall and forwarded in the router. If the machine is only in the
  internal network, see “The certificate”.
- **A mail server** is optional. Without it a new account gets a password right
  away, and “Forgot your password?” is missing (see “Mails”).

### Steps

```bash
git clone https://github.com/lennago/crosstally.git && cd crosstally
cp .env.example .env                      # fill in, see “All values of the .env”
mkdir -p backups && sudo chown 1000:1000 backups
docker compose pull
docker compose up -d --no-build
docker compose exec web python manage.py createsuperuser
```

What is needed from the repository are only `compose.yaml`, `Caddyfile` and
the backup script. The image comes finished from the registry. `--no-build`
keeps compose from building it itself from the build instructions in
the `compose.yaml` (see “Building it yourself”). In `VERSION` the template
names the latest version. Which other values exist is described under
“Updating”.

The folder `backups` belongs to the container's user (uid 1000). If compose
creates it on the first start, it belongs to `root`, and the button “Create
backup” fails on the write permission. That is why you create it before the
first start.

The database is migrated on every start (`bin/start.sh`). The first account is
an administrator, all others are created in the interface. The administration
lives under the path from `ADMIN_URL`. If you are not signed in, it answers
404. Sign in on the website first, then “Administration” in the account menu
leads there. Only administrators see it.

### The certificate

**Without HTTPS there is no counting app.** Service workers only run in a
secure context, so there is neither installation nor offline operation either.
`SITE_ADDRESS` decides who handles TLS:

| Situation | `SITE_ADDRESS` | What handles TLS |
| --- | --- | --- |
| Machine with a public name | `crosstally.example.de` | Caddy fetches the certificate itself |
| A proxy in front that already has a certificate | `:80` | the proxy, `tailscale serve` for instance |

If the machine is only in the internal network, no public certificate authority
reaches it, and Caddy gets no certificate for its name. Three ways still lead
to HTTPS:

- **A proxy in front that already has a certificate.** That can be an in-house
  reverse proxy or [Tailscale](https://tailscale.com/kb/1312/serve), which
  issues a machine inside your own tailnet a real certificate (HTTPS
  certificates have to be enabled in the admin console for that). Then the
  line `SITE_ADDRESS=:80` applies, plus the three values for a proxy in front
  (see below and “What easily goes wrong”).
- **A public domain with the DNS check.** The certificate authority then checks
  a record in the DNS instead of a connection to the machine. That needs a Caddy
  with the module of the DNS provider, so an image of your own for the
  service `proxy`.
- **Caddy's own certificate authority**, with `tls internal` in the Caddyfile.
  Then every device, every phone included, has to trust Caddy's root
  certificate. It lies in the container
  under `/data/caddy/pki/authorities/local/root.crt`. On phones that is
  tedious, and the change to the Caddyfile has to come along with
  every `git pull`.

With Tailscale on the same machine it looks like this:

```bash
tailscale serve --bg --https=443 http://localhost:80
tailscale serve status     # shows the address
```

Then in the `.env`: `SITE_ADDRESS=:80`, `LISTEN_ADDRESS=127.0.0.1`,
`TRUSTED_PROXIES=172.16.0.0/12` and `PROXY_COUNT=1`, plus the tailnet name
in `EXTRA_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. The file is read on start,
so it only takes effect with `docker compose up -d`.

### Your own proxy instead of Caddy

Whoever already has a proxy (Traefik, nginx …) takes the service `proxy` out of
the `compose.yaml` and puts port 8000 of the service `web` behind it. `web` does
not publish the port. If the proxy runs on the machine itself, the service gets
the line `ports: ["127.0.0.1:8000:8000"]` for it. A proxy in Docker joins the
network of compose instead. The proxy sets `X-Forwarded-Proto` and puts the
caller's address into `X-Forwarded-For`. `BEHIND_TLS_PROXY=true` stays,
and `PROXY_COUNT` counts the hops in front of your own proxy, 0 if it is
reached straight from the network.

### All values of the `.env`

This means the `.env` next to the `compose.yaml`. The template `.env.example`
names every value with a short explanation, and whatever is commented out has a
usable default. For development there is a separate one, `server/.env`
from `server/.env.example`. The two have nothing to do with each other.

| Variable | Required | Default | What for |
| --- | --- | --- | --- |
| `SECRET_KEY` | yes | | Signs sessions and tokens. Long and random. |
| `BACKUP_KEY` | yes | | The key every backup is encrypted with. At least 32 characters, see “Backups”. |
| `ADMIN_URL` | yes | | Path of the administration, one of your own ending in `/`. Empty, with `/` at the start or without `/` at the end, the placeholder from the template and a path under or above one that is open without signing in (`admin/`, `accounts/`, `api/` …) prevent the start. If you are not signed in, the path answers 404. Under `/admin/` there is only the decoy. |
| `POSTGRES_PASSWORD` | yes | | Password of the database inside the compose network, long and random like the two above. The database takes it once, at its first start. Whoever changes it later changes it there too with `ALTER USER`. |
| `POSTGRES_DB`, `POSTGRES_USER` | | `crosstally` | Name of the database and of its user. |
| `SITE_ADDRESS` | yes | `:80` | What Caddy serves: your own domain. The default `:80` only applies with a proxy in front, see “The certificate”. |
| `EXTRA_ALLOWED_HOSTS` | yes | | The name the application is reached under. Several separated by commas. |
| `CSRF_TRUSTED_ORIGINS` | recommended | | The same address with `https://` in front. Behind Caddy, Django compares the origin of a form with its own address, so forms work without it. The entry is needed when a proxy on the way changes scheme or name. Several separated by commas. |
| `BEHIND_TLS_PROXY` | | `false` | `true` as soon as a proxy handles TLS, so with Caddy always. The template sets it. |
| `DEBUG` | | `False` | In operation always `False`. |
| `VERSION` | | `latest` | Which image runs. The template names the latest version, see “Updating”. |
| `APP_VERSION` | | | Only when building on the machine itself, and only for a name of your own for the version in the account menu. Without the entry the build takes the state of the checkout (`lokal-1a2b3c4`). At run time the number the image carries itself applies. An entry here does not override it. |
| `GUNICORN_WORKERS` | | `4` | Workers of the application server. Each serves one request at a time, see “What the machine needs”. |
| `LISTEN_ADDRESS` | | | On which address of the machine Caddy listens. Empty means on all of them. With a proxy **in front of** Caddy on the same machine, `127.0.0.1`. |
| `PROXY_COUNT` | | `0` | Hops **in front of** Caddy, see “What easily goes wrong”. |
| `TRUSTED_PROXIES` | | | Whose `X-Forwarded-*` headers Caddy trusts. Empty means nobody's. Several separated by spaces. Only with a proxy **in front of** Caddy, see “What easily goes wrong”. |
| `HSTS_SECONDS` | | `300` | How long the browser allows HTTPS only. Short to begin with, later a year (`31536000`). |
| `CONN_MAX_AGE` | | `60` | Seconds a connection to the database stays open. |
| `LANGUAGE_CODE` | | `de` | Language of the server and its addresses, `de` or `en`. See “Language and time”. |
| `TIME_ZONE` | | `Europe/Berlin` | Time zone of the display, a name from the IANA list. |
| `EMAIL_HOST` | | | SMTP server for invitations, “Forgot your password?”, confirming a new e-mail address and the notifications about changes to an account. Empty: no mail goes out, see “Mails”. |
| `EMAIL_PORT` | | `587` | With `EMAIL_USE_SSL=true` the default is `465`. |
| `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | | | Credentials for the SMTP server. |
| `EMAIL_USE_TLS` | | `true` | STARTTLS, the usual way on port 587. |
| `EMAIL_USE_SSL` | | `false` | Implicit TLS on port 465. Switches STARTTLS off by itself. |
| `DEFAULT_FROM_EMAIL` | with `EMAIL_HOST` | `webmaster@localhost` | Sender of the mails. If a server is set, the application does not start without it. |

The connection to the database and `BACKUP_DIR` are set by the `compose.yaml`
itself. They do not belong in the `.env`.

### Mails

**E-mail** only leaves via SMTP when `DEBUG` is off and `EMAIL_HOST` names a
server. Otherwise the application writes the mail to standard output, that is
into the container's log (`docker compose logs web`). To see the mail as it
arrives, use Mailpit (see “Trying it out without a domain”). Whether a
configured server accepts mail is shown by a test mail:

```bash
docker compose exec web python manage.py sendtestemail you@example.com
```

**Without a server** the pages drop what promises a mail: “Forgot your
password?” (the address answers with 404), the invitation and “Send invitation
again”. A new account gets its first password right when it is created. A
forgotten one is set anew by an administrator in the administration (open the
account, “Reset password”).

**With a server** the invitation is the normal way: the person gets a link and
sets their password themselves. Every person learns by mail when their account
is changed, deactivated or deleted, their password is set anew or a passkey is
added or removed. A new e-mail address applies only once the person has
confirmed it through the link in the mail. Five “Forgot your password?”
requests per hour are possible per e-mail address and per address the request
comes from. If a configured server does not answer, the change applies anyway,
and the invitation can be sent again from the account's page.

### The texts of the mails

What the mails say is set by an administrator in the administration,
under **Organisation → Mail texts**: per mail a subject and a text, in German
and in English. An empty field means the supplied text, with its improvements
on the next update. Which placeholders a mail knows (such
as `{{ link }}`) is listed on its page, with a preview below. Per mail
the tick **names who triggered it** decides whether the mail names the person
who created, changed or deleted the account. The backup takes the texts along.

## Trying it out without a domain

On your own machine Docker is enough (on Windows, Docker Desktop):

```bash
cp .env.example .env     # fill in as under “All values of the .env”, plus
                         # SITE_ADDRESS=localhost
docker compose pull && docker compose up -d --no-build
```

The web interface then runs at `https://localhost`. Caddy issues the certificate
for that itself, so the browser warns on the first visit. The counting app on
the phone needs a certificate the phone trusts, through Tailscale for instance
(see “The certificate”).

**Looking at mails without anything going out:** [Mailpit](https://mailpit.axllent.org/)
accepts every mail as an SMTP server, sends none and shows them in the browser
the way a mail client displays them. It goes into the network that compose
creates for the services. That network is named after the folder holding
the `compose.yaml`, followed by `_default` (`docker network ls` shows it):

```bash
docker run -d --rm --name mailpit --network <folder>_default \
  -p 127.0.0.1:8025:8025 axllent/mailpit
```

Then in the `.env`:

```bash
EMAIL_HOST=mailpit
EMAIL_PORT=1025
EMAIL_USE_TLS=false
DEFAULT_FROM_EMAIL=sender@example.com
```

After `docker compose up -d` the mails are at `http://localhost:8025`,
including the one from “Forgot your password?” with the link in it. Whether a real
server accepts them and whether they end up in spam is not something Mailpit
shows. That takes a real delivery, and often your own mailbox with SMTP access
is enough for it.

## Backups

There are **two of them**, and that is on purpose:

| | `bin/backup.sh` | Button in the administration |
| --- | --- | --- |
| What | dump of the database (`pg_dump`, `.sql.gz.enc`) | data snapshot as JSON (`dumpdata`, `.json.gz.enc`) |
| Who | cron or by hand, on the machine itself | an administrator in the browser |
| What for | the way back when the database is broken | the way back when the **data** is wrong |

Both write to `./backups`, both keep the seven most recent backups. After a
restore through the administration, the backup from before lies there too, as
the way back. An uploaded logo lives in the database and is part of both kinds.

### Every backup is encrypted

A backup holds the accounts with their password hashes and everything the
sites have counted. It is copied and kept on other machines, and nobody watches
over it there any more. So every one is encrypted, with the `BACKUP_KEY` from
the `.env`:

```bash
openssl rand -base64 48      # the result goes into the .env as BACKUP_KEY
```

- **Without the key the application does not start**, nor with one shorter
  than 32 characters. Refusing only the backup would be quieter: a nightly run
  that fails is noticed on the day the backup is needed.
- **The key also belongs in a place away from this machine.** If it is lost,
  no backup can be read any more, and nobody can change that. Whoever copies
  the backups to another machine does not put the key next to them.
- **A new key does not read the old backups.** Whoever changes it keeps the old
  one until the backups from before have aged out.
- **Encrypted is not signed.** A wrong key and a damaged file are noticed,
  because inside sits gzip with its checksum. But whoever may write into the
  folder could swap a snapshot for an older one. A file of their own without the
  key, on the other hand, is not taken by the restore (see “Restoring”).

The format is OpenSSL's: first gzip, then AES-256 with a key that PBKDF2
derives from the `BACKUP_KEY`. That way a backup can be read even when the
application no longer exists, on any machine with OpenSSL:

```bash
export BACKUP_KEY='…'        # not as an argument: everyone on the machine could read that
openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 -pass env:BACKUP_KEY \
    -in backups/stand_<time>.json.gz.enc | gunzip > stand.json
```

### The database dump

```bash
./server/bin/backup.sh       # the script changes into the folder with the compose.yaml itself
```

The simplest way to run it daily is from the crontab of `root`
(`sudo crontab -e`), with the path the repository lies in:

```cron
15 3 * * * /opt/crosstally/server/bin/backup.sh
```

`BEHALTEN=14 ./server/bin/backup.sh` keeps fourteen dumps instead of seven,
and `ZIEL=/path ./server/bin/backup.sh` writes into another folder.
Whoever changes `BEHALTEN` adjusts the sentence about the seven most recent
backups in the privacy text (administration, “Appearance and operator”).

Before the dump the script tidies up: `manage.py cleanup` deletes expired
sessions and sign-in tokens of the counting app, failed sign-ins once the half
hour of the lockout is over, the “Forgot your password?” requests and the
attempts at the decoy under `/admin/` after 30 days, and the log of the
administration after a year. All of it would otherwise pile up without end.
Whoever does not run the script daily calls the command themselves
(`docker compose exec web python manage.py cleanup`). The periods are also in
the privacy text: the text depends on this run.

`pg_dump` runs in the `db` container, because its version has to match the
database. Encrypting happens in a container of its own from the image
of `web`, and the key does not leave it on the way. `web` itself need not be
running for this: the dump is made even when the application is stopped or does
not start after a failed migration, and that is when it is needed most.
If `pg_dump` breaks off or the key is missing, no file is left behind. Whoever
calls the script has to be allowed to write into the folder: `root`, or the user
the folder belongs to. The dump is readable by them only.

### Off site

Backups on the same disk as the database do not help when the disk fails or the
machine is lost. A copy therefore belongs on another machine regularly, right
after the nightly dump, say:

```bash
rsync -a /opt/crosstally/backups/ backup@nas.example.com:crosstally/
```

The files are encrypted and may sit on outside storage. The key does not belong
next to them but in a third place, a password manager for instance. A copy of
the `.env` belongs there too, because it holds `BACKUP_KEY`
and `POSTGRES_PASSWORD`, and without it the installation cannot be set up again
on a new machine.

### Restoring

**A JSON snapshot through the administration**, when the data is wrong and the
machine is fine: choose the snapshot among the backups in the administration and
restore it with a typed confirmation. The administration saves the current
state beforehand. What applies:

- **Not while counting is going on.** The counting apps hold lines of the
  current state. After a restore they could no longer match what was counted.
  Close first, or go back to preparation. Through the console (`loaddata`) the
  block does not apply. That is the way for development.
- **Afterwards everyone is signed out.** The sessions of the web interface live
  in the database and are gone afterwards. Every refresh token of the counting
  app that was valid before is on the block list afterwards, even one the
  snapshot does not know. The access token runs out within an hour, then the app asks for
  the sign-in. Whatever it has not sent yet stays and goes out after the sign-in.
  The scripts under `server/bin/` sign everyone out as well, as soon as the
  snapshot carries accounts.
- **Accounts, passwords and passkeys are those of the snapshot.** Whoever changed a
  password, removed a passkey or deactivated an account since then does it
  again. Otherwise a lost device, say, signs in again with its old passkey.
- **An older snapshot is brought up to date.** Fields that no longer exist are
  skipped (`loaddata --ignorenonexistent`). What a migration has changed since,
  such as the inventory types on the articles or the unit on the results, the
  restore rewrites by the same rules. That applies in the administration as
  well as in `manage.py backup read` and the scripts under `server/bin/`. The
  file itself stays as it is. If a site has two inventories on the same day in
  it, the snapshot cannot be restored, because there is only one per site and
  day.
- **A snapshot that names a model that no longer exists cannot be restored.**
  The administration reports it and leaves the database untouched. Such a snapshot
  is an archive: it can only be read with the code of its time.
- **An unencrypted snapshot is not restored** as long as a key is set, and in
  operation one always is. Anyone who may write into the folder could have
  placed such a file, accounts of their own included. An older snapshot of your
  own comes in through the key, see below.
- **A snapshot is held in memory as a whole while restoring**, at about four times
  its size. Only from a few hundred MB would the container need more than the
  2 GB from “What the machine needs”.

**An unencrypted snapshot of your own**, say from development without
a `BACKUP_KEY`, is encrypted with the key first. Afterwards the administration offers it for restoring like
any other:

```bash
docker compose exec web sh -c \
    'python manage.py backup store /backups/stand_<time> < /backups/stand_<time>.json'
docker compose exec web rm /backups/stand_<time>.json
```

`backup store` writes the snapshot next to it, encrypted with the `BACKUP_KEY`,
as `stand_<time>.json.gz.enc`. The second line takes the unencrypted file
away. On the console `manage.py backup read` prints an unencrypted file only
with `--plaintext`, and the scripts under `server/bin/` take the same word in
front of the path. That way whoever operates the machine says the file is
their own.

**The dump with `psql`**, when the database is broken or as the way back after
an update:

```bash
docker compose stop web
docker compose exec db dropdb -U crosstally --force crosstally
docker compose exec db createdb -U crosstally crosstally
docker compose run -d --no-deps --name crosstally-einspielen --entrypoint sleep web infinity
docker exec -i crosstally-einspielen python manage.py backup decrypt \
    < backups/crosstally-<stamp>.sql.gz.enc |
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U crosstally crosstally
docker exec crosstally-einspielen python manage.py sign_out_everyone
docker rm -f crosstally-einspielen
docker compose up -d --no-build
```

Decrypting happens in a container of its own from the image of `web`,
because `web` is stopped at that moment. The container itself does nothing (`sleep`),
the commands run in it with `docker exec`. What a container started
with `docker compose run` prints, Docker writes into its log, and the whole dump
would end up there in plain text. What a command started with `docker exec`
prints only goes into the pipe to `psql`. `backup decrypt` passes on only once
the dump is whole and the key is the right one. Half a dump would otherwise
already arrive in the database. `ON_ERROR_STOP` stops at the first error.
Without it `psql` would carry on, and the error would hide somewhere in hundreds
of lines of output. Whatever was counted or maintained since the backup is gone
afterwards. Anyone who set other values for `POSTGRES_USER` or `POSTGRES_DB`
uses them here.

**Afterwards everyone is signed out** (`sign_out_everyone`). The dump carries
the sessions of the website and the sign-ins of the counting app from the time
it was taken, and they would be valid again, including whatever was shut out
since. The command ends every
session and blocks every refresh token from the dump. A device that signed in or
renewed its sign-in only after the dump is not in it, and the server does not
know its token. It has to sign in anew at its next renewal, within the hour at
the latest. Accounts, passwords and passkeys are those of the dump: whoever
changed a password, removed a passkey or deactivated an account since then does
it again.

**On a new machine**, when the old one is gone:

1. Set up the machine as under “Setting it up the first time”, but with the
   old `.env`. It carries `BACKUP_KEY`, `POSTGRES_PASSWORD` and the `VERSION`
   that ran last.
2. Copy the backups into the folder `backups` and give it to the container's
   user (`sudo chown -R 1000:1000 backups`).
3. Instead of `createsuperuser`, restore the most recent dump as above. The
   accounts come along.
4. Point the name in the DNS to the new machine. Caddy fetches the certificate
   anew, and the passkeys stay valid as long as the name stays the same.

## Updating

On the server where the image comes from the registry:

```bash
./server/bin/backup.sh           # a backup first
git pull                         # brings compose.yaml and Caddyfile along
# Raise VERSION in the .env, then:
docker compose pull
docker compose up -d --no-build
```

The data lives in the database volume, not in the image. A new image leaves it
alone. Four things still matter:

- **A backup first, and not while a count is running.** `migrate` runs on start
  and changes the schema where the new version asks for it. Migrations that
  bring data up to date need no manual work. What does need manual work is
  announced in the changelog as a break (`feat!:` or `fix!:`).
- **If `git pull` changed the `Caddyfile`, the proxy needs a new container:**
  `docker compose up -d --force-recreate --no-build proxy`. Caddy reads the file
  only at start, and `docker compose up -d` leaves a container standing whose
  settings stayed the same. A plain `restart` is not enough: the container holds
  the old file, and under Docker Desktop it does not start at all afterwards.
- **Never `docker compose down -v`.** The `-v` deletes the volumes and with
  them the database.
- **After a release that merges migrations** (the changelog says so), once
  per app named there:
  `docker compose exec web python manage.py migrate <app> --prune`. That clears
  the records of the old files from the database. If they are left in place, a
  later migration with the same name counts as applied and never runs.

The values `VERSION` can take:

| What is meant | `VERSION` |
| --- | --- |
| the working state, that is the last push to main | `main` |
| a particular state of it | `main-1a2b3c4` |
| a particular version | `0.3.0`, or `0.3` for the latest 0.3.x |
| the last published version (the default of the `compose.yaml`) | `latest` |

In operation the `.env` is best given a fixed version. Then the entry decides
what runs, and not the moment of the last `pull`. Rolling out means changing the
number. Which tags exist is shown under “Packages” in the repository, the latest
version also in the template `.env.example`. Which version is currently running is
shown at the very bottom of the account menu of the web interface. The counting
app on the phones checks hourly and on coming back from the background whether
there is a new version, and announces itself with a note in the bar. The new
version is only loaded on a click, so that no draft is lost.

### The way back

**The way back is the old number, as long as the new version brought no
migration.** If it changed the schema, the old version no longer knows it. Then
the way back leads through the dump taken before (see “Restoring”) and then the
old `VERSION` in the `.env`.

### If the start fails

`bin/start.sh` migrates on every start. If a migration fails, the application
server does not start, and Docker tries again after a minute
(`restart: unless-stopped`). It looks like this: `docker compose ps` shows `web`
freshly started again and again, the website does not answer, and the counting
app shows “Offline”. The cause is at the top of `docker compose logs web`.

Two causes occur. A value in the `.env` is missing or wrong: then the message
says so, and after the correction `docker compose up -d` is enough. Or a
migration asks for manual work on the data first. Then the message names what
to clean up, and the changelog announced it as a break. Cleaning up happens
with the **old version**: put in the old `VERSION`, start, fix the data, then
enter the new number and start again. To be safe, restore the dump taken before
ahead of the second attempt.

### A new major version of PostgreSQL

A new major version does not read the data directory of the old one. That is
why Dependabot does not propose it (`.github/dependabot.yml`), and the switch
comes deliberately, with a release that announces it as a break. New
minor versions come with `docker compose pull` by themselves. The switch itself:

```bash
docker compose stop proxy                    # from here on nobody reaches the application
./server/bin/backup.sh                       # a fresh dump
docker compose run --rm --no-deps -T --entrypoint sh web -c \
    'python manage.py backup decrypt > /dev/null && echo readable' < backups/crosstally-<stamp>.sql.gz.enc
docker compose down                          # without -v
docker volume rm <folder>_db_data            # only now: the data directory of the old version
git pull                                     # the compose.yaml with the new major version
# Raise VERSION in the .env, then:
docker compose pull
docker compose up -d --wait db               # only the database, still empty
docker compose run -d --no-deps --name crosstally-einspielen --entrypoint sleep web infinity
docker exec -i crosstally-einspielen python manage.py backup decrypt \
    < backups/crosstally-<stamp>.sql.gz.enc |
    docker compose exec -T db psql -v ON_ERROR_STOP=1 -U crosstally crosstally
docker rm -f crosstally-einspielen
docker compose up -d --no-build              # only now web and proxy
```

From the dump to the restore nobody reaches the application. Whatever arrived in
that time would be gone afterwards. And `web` only starts once the data is back.
Otherwise it would first set up an empty database, and that would already be
visible from outside. The name of the volume is shown by `docker volume ls`. It
is only deleted once the dump has been shown to be readable. The check throws
the dump away inside the container and prints only “readable”.
With `> /dev/null` outside, the decrypted dump would go through the container's
output into the log Docker writes for every container. The restore therefore
goes through `docker exec` as above. Nobody has to be signed out here: the dump
was only just taken, with the proxy stopped, and nothing was revoked since.
Anyone who set other values for `POSTGRES_USER` or `POSTGRES_DB` uses them here.

If the new version starts on the old volume by mistake, `db` stops and
restarts in a loop. The data stays untouched, and with the old `compose.yaml`
everything runs as before. The log suggests `pg_upgrade`, but the way here
remains the dump. From 18 to 19 it loads as it was written: 19 no longer
accepts `standard_conforming_strings = off`, and the dump says `on`, as in
every database from the `compose.yaml`.

## Operator and privacy

The footer of every page states who operates the application, and next to it a
link to the privacy page, `/datenschutz/`, on an English server `/privacy/`.
Both are entered by an administrator in the administration,
under **Organisation → Appearance and operator**: name, contact and the contact
for data protection, because the published image does not know who runs it. Without an
entry, only the link remains in the footer. The details belong to
a **version** of the appearance, together with the logo and the name of the
application (default “CrossTally”). The version “Default” is immutable and
always present. A copy is what gets changed, and the list is where you choose
which version applies (README,
[“Your own logo”](README.en.md#your-own-logo)).

The page can be read **without signing in**. It concerns the counters above all,
and they otherwise only get to see the lockout page of the web interface. In the
counting app the way there leads through the account menu.

A **legal notice** (imprint) exists only if one is entered, in the same version
under “Legal notice”, one text per language. Then the footer links
to `/impressum/` (on an English server `/legal-notice/`), and so does the
account menu of the counting app, and the page can be read without signing in
like the privacy page. It shows the text without a frame, as it was entered, in
the format of the privacy text (see “Your own text”). What belongs in it is up
to the business.

**Feedback** goes to the “Address for feedback” of the same version. It ships
empty, and then there is no such entry. With an address, the account menu of
the web interface and of the counting app offers “Report a problem or
suggestion”. The link opens an e-mail in the person's own mail program,
prefilled with version, page, account, time and browser. The application stores
none of it. The subject always starts with `[CrossTally]`, which a mail filter
can match.

### Why no legal notice out of the box and no cookie banner

This is not legal advice but the reasoning behind the design, under German law.

A **legal notice** under § 5 DDG applies to providers of commercial digital
services, that is, an offering to an indefinite group of people. An application
that only your own employees can reach is an internal tool and not an offering
on the market. That is why the application brings none. Whoever needs one
anyway, say because the application is also open to outsiders, enters it in
the administration (see above). The page footer is there either way, because a
name is cheaper than the question, and because data protection needs a
responsible party regardless.

A **cookie banner** is required by § 25 TDDDG only for what is not strictly
necessary. Only what is necessary is stored here:

- the session cookie and the CSRF cookie from Django, plus, without signing in,
  a cookie for the chosen language that goes with the browser window,
- the choice of appearance, the view state and short notes between two pages
  in `localStorage` or `sessionStorage` (`static/js/theme.js`, `view_state.js`,
  `expand.js`),
- in the counting app the sign-in, the details of the account, the count list,
  the review list and the entries not yet sent, in IndexedDB. Without them there
  would be no counting where there is no reception. Signing out empties the
  storage.

There is no analytics, no tracking and no embedding of outside servers. The
content security policy in `django_project/settings.py` allows only `self` for
scripts, styles, fonts, images and connections, and `test_csp.py` turns any
violation red beforehand. With employees, consent would be the wrong instrument
anyway, because it has to be given freely.

### What still comes up alongside

That is not handled by software but by the business:

- **Information under Art. 13 GDPR.** The page `/datenschutz/`
  (or `/privacy/`) fulfils that as far as the application itself is concerned.
  How the business otherwise handles employee data is not described there.
- **Record of processing activities** under Art. 30 GDPR. The application needs
  an entry there.
- **Co-determination** under § 87 (1) no. 6 BetrVG, if there is a works council.
  A technical installation is subject to co-determination as soon as it
  is **suitable** for monitoring performance and conduct. A count line carries
  no name, but `gezaehlt_am` and the assignment on the inventory allow
  conclusions where a single person is counting.

### Deleting accounts instead of deactivating them

Almost nothing hangs on the account. Deleting it removes only the assignment
to sites and inventories, its passkeys and that person's entries in the log of
the administration. Its failed sign-ins and its attempts at the decoy
under `/admin/`, which carry the name as text, go with it as well. The count
data stays untouched, because they point to no account. **Deleting** is
therefore the right answer when somebody leaves the business, rather than
taking away the “Active” tick. An account is created again in a minute, should
somebody come back after all.

Who counted in an inventory is only kept until it is closed. It is not in the
PDF. Whoever needs it for an audit notes it beforehand, outside the
application.

### Your own text

The text of the page sits in the administration, in the same version as the
logo, one per language. It is prefilled with the supplied text
(`server/templates/privacy/de.txt` and `en.txt`). That describes what the
application does, and that is accurate. Whether it is enough for your own
business is decided by whoever is responsible for data protection there. The
text can be extended, changed or replaced entirely. No image of your own is needed for
that. The file is only the prefill of a new version. If it changes with a new
release of the application, a migration carries it into the versions whose
text was still a bundled one. An edited text stays as it is and does not
receive the update. Whoever empties both fields removes the page: it then
answers with 404, and neither the footer nor the account menu of the counting
app links to it.

The text is not HTML but text with five rules: a blank line separates
paragraphs, a single line break stays, `## ` starts a subheading, `- ` a bullet
point, `**like this**` is bold. Who is responsible and who gives information
come from the fields next to it and appear above and below the text, as in the
footer. The legal notice follows the same rules, only without a frame: it shows
its text alone.

### Logo and name in detail

How a version of your own with your own logo comes about is in the README
([“Your own logo”](README.en.md#your-own-logo)). In addition:

- **PNG only.** An SVG can contain script, and served from the same origin it
  would run in the browser of whoever is signed in. What is checked is the
  content of the file, not its name: square, at least 512 pixels, at most 4096,
  at most 2 MB, a single image.
- **What is derived from it:** the logo of the navigation bar, the image in the
  browser tab, the icon for iOS and the four sizes the counting app names for
  the home screen. It needs no Chromium and no build for that.
- **Background for the home screen:** Android puts the icon into its own shape
  (circle, squircle, teardrop) and cuts off the edge in the process. Only the
  inner circle with 80 % of the diameter is reliably visible. For this one case
  the motif sits shrunk on an opaque background, and its colour is in the field next
  to the upload (white by default). Everywhere else the logo stays transparent.
- **Versions:** every version except Default and the active one can be deleted.
  The tick **Remove logo** under “Name and logo” resets the logo of a version to
  the placeholder.
- **The colour of the installed app** (`theme_color`) is in the manifest
  in `app/vite.config.js`. Its name comes from the active version.

## Language and time

Two settings in the `.env` decide what the server starts with:

```bash
LANGUAGE_CODE=de        # or en
TIME_ZONE=Europe/Berlin
```

`LANGUAGE_CODE` applies in two places. First, wherever nobody is signed in: the
sign-in form, error pages, privacy. Second, as the **default for every new
account**. Whoever signs in finds the choice in their account menu and keeps it
afterwards. A change on the server changes nothing about the accounts that
already exist.

**The addresses of the web interface follow `LANGUAGE_CODE` as well.** On a
German server the list of sites is `/haeuser/`, on an English one `/sites/`,
the privacy page `/datenschutz/` or `/privacy/`. The language of the account
changes nothing about that: an address applies to everyone using the same
server, and a link can be passed on. Whoever switches `LANGUAGE_CODE` switches
all addresses with it, and old bookmarks lead nowhere. What stays the same:
`/api/`, `/app/`, `/admin/`, the sign-in under `/accounts/` and the path
from `ADMIN_URL`. An open link from an invitation or for confirming a new
address no longer works after the switch.

If something other than `de` or `en` is set there, Django rejects it on start
(`translation.E004`). An unknown time zone likewise, and then the container does
not even start.

The time zone only concerns the display. Storage is always in UTC. A change
takes effect immediately on cut-off dates, closing times and the timestamps of
the backups.

## What easily goes wrong

- **`DEBUG=False` and no `EXTRA_ALLOWED_HOSTS`** → “Bad Request (400)”,
  `DisallowedHost` in the log. The name the app is reached under has to be
  listed there.
- **No `BEHIND_TLS_PROXY=true`** behind Caddy → Django considers the connection
  unencrypted and redirects endlessly.
- **`SITE_ADDRESS=:80` without a proxy in front** → Caddy only serves port 80,
  Django redirects to https, and nobody answers on port 443. With a domain of
  your own, the domain goes in (see “The certificate”).
- **Passkeys are bound to the domain.** The browser ties every passkey to the
  address it was created under. If the application moves to another domain,
  none of them applies any more, and every device creates a new one. Under an
  IP address none can be created at all, nor without HTTPS, except
  on `localhost`. Behind the proxy the origin check needs the
  setting `BEHIND_TLS_PROXY=true`, otherwise Django expects `http://` and
  rejects every answer of the browser.
- **The `.env` after a change** only takes effect after `docker compose up -d`,
  because it is read on start.
- **A button does nothing, a font or an image is missing** → look into the
  browser console. The page sends a **content security policy** along
  (`SECURE_CSP` in `settings.py`). Only what comes from your own address is
  executed and loaded. Whatever an upstream service writes into the page (scripts
  from a CDN, “e-mail protection”, analytics) is blocked by the browser. That is
  intended. Turn it off there instead of loosening the policy.
- **The data folder of Postgres** is mounted as a whole
  (`/var/lib/postgresql`), not the `data` below it. The image has required that
  since Postgres 18, otherwise it refuses to start.
- **If something in front of Caddy already handles TLS** (`tailscale serve`),
  Caddy needs `TRUSTED_PROXIES` in the `.env`. The default is empty: Caddy
  trusts nobody, overwrites the note about the original protocol, and Django
  redirects endlessly to https. Enter the proxy in front and nothing else,
  because whoever Caddy trusts picks their own address and gets past the
  sign-in lockout that way. `private_ranges` trusts everybody in the local
  network. On the same machine `tailscale serve` arrives from the Docker
  network, and `172.16.0.0/12` is enough for that. Narrower still is the gateway
  alone, found with the command `docker network inspect <folder>_default`.
- **With a proxy in front, Caddy listens only where the proxy reaches it**
  (`LISTEN_ADDRESS`, on the same machine `127.0.0.1`). Otherwise everybody in
  the local network reaches Caddy past the proxy and counts as the proxy,
  because Docker Desktop does not pass the caller's address on.
- **`PROXY_COUNT` has to match the chain of proxies.** The lockout counts per
  address, and behind Caddy the caller's address sits in `X-Forwarded-For`. The
  number says how many hops **in front of Caddy** appear in it. Caddy writes
  the header itself and does not appear in it. Straight from the internet onto
  Caddy: 0 (the default). With `tailscale serve` in front: 1. If the application
  counts wrongly, it finds no address or that of a proxy, and then locks out
  everybody at once.
- **The search needs the extension `pg_trgm`.** A migration creates it
  (`CREATE EXTENSION`). With the Postgres from `compose.yaml` this happens by
  itself, because there the database belongs to the application's account. On an
  external Postgres whose account may not do that, someone with the rights
  creates it beforehand: `CREATE EXTENSION IF NOT EXISTS pg_trgm;` in the
  application's database. Otherwise `migrate` fails at start.
- **Whoever mistypes five times is locked out for half an hour** (`django-axes`,
  user name and address together). The lockout resolves itself. Whoever does not
  want to wait deletes the row in the administration under “Failed sign-ins”.

## For forks and contributors

What stands here is only needed by whoever builds or publishes images
themselves or works on the project.

### Preparing a fork on GitHub

Only for whoever publishes images themselves, for instance because of changes
of their own to the code. A logo of your own, the operator and your own privacy
text do not need this. They sit in the administration (see the README). Once,
so that `image.yml` runs through and the image lands in the registry:

1. **Actions have to be allowed** (Settings → Actions → General). A secret
   is **not** needed. The workflow signs in with the `GITHUB_TOKEN` that every
   run receives, and requests `packages: write` for itself.
2. **After the first push to `main`** the package `ghcr.io/<owner>/<repo>` comes
   into being. It takes on the visibility of the repository: from a public
   repository it is public, and the server pulls without signing in.
3. **Only with a private repository or an old package** (Package → Package
   settings):
   - If the repository is private, so is the package. Either *Change
     visibility* → **Public**, or sign in on the server once, with a classic
     PAT with `read:packages`:
     `echo "<PAT>" | docker login ghcr.io -u <user> --password-stdin`.
   - If the package existed before, from a deleted repository of the same name
     for instance, it does not belong to the new repository, and the upload
     fails with `permission_denied`. Then add the repository under *Manage
     Actions access* with the role “Write”, or delete the old package.
4. **Enter your own image:** change the name under `web.image` in
   the `compose.yaml` to `ghcr.io/<owner>/<repo>`. Which tags come into being is
   described under “Versions and releases”.
5. **release-please may create pull requests**: Settings → Actions → General →
   *Workflow permissions* → tick “Allow GitHub Actions to create and approve
   pull requests”. Without that tick the “Release” job in `image.yml` fails.
6. **Merging as one commit**: Settings → General → *Pull Requests* → “Allow
   squash merging”, with “Pull request title” as the default for the message.
   The title thereby becomes the commit message, and `pr_title.yml` checks it.
7. **Dependabot** needs nothing further, `.github/dependabot.yml` is enough. The
   security alerts for it are under Settings → Advanced Security.

### Building it yourself

On a machine that builds the image itself (development, test server):

```bash
git pull
docker compose up -d --build     # migrate runs on start (bin/start.sh)
```

A self-built image shows the state of the checkout it was built from in the
account menu, say `lokal-1a2b3c4`. The build reads it from the few files
of `.git` the build context carries (`bin/version.sh`), without anyone
entering anything. From an archive without `.git` it says “unknown”. The same
goes for a worktree or a submodule, where `.git` is a file. For a name of your
own, set `APP_VERSION` in the `.env`. A published image carries its number
from `image.yml`.

For an ARM machine, `linux/arm64` belongs among the platforms in `image.yml`,
with `docker/setup-qemu-action` before it. The run then takes many times as
long. Uploading goes to `ghcr.io/<owner>/<repo>` with the access token of the
run.
Whoever does not want that sets `push: false` in the “Build and push” step.

### Versions and releases

Versions are managed by **release-please** (job “Release” in `image.yml`), from
the commit messages on main. Those follow the Conventional Commits. The
changelog itself is German, so its sections are named here as they appear:

| Message starts with | Effect | in the changelog under |
| --- | --- | --- |
| `feat:` | raises the middle digit (0.3.0 → 0.4.0) | Neu (new) |
| `fix:` | raises the last digit (0.3.0 → 0.3.1) | Behoben (fixed) |
| `perf:`, `refactor:`, `revert:` | raises the last digit, like `fix:` | Schneller, Umbauten, Zurückgenommen (faster, rebuilt, reverted) |
| `docs:`, `style:`, `test:`, `build:`, `ci:`, `chore:` | none | - |
| `feat!:` or `fix!:` | a break: manual work is needed in operation | at the top, highlighted |

Before 1.0.0, even a break raises only the middle digit. A correction belongs
under `fix:`, not under `refactor:`, so that it is listed under “Behoben”.

Dependabot writes its titles accordingly (`.github/dependabot.yml`). What runs
in operation, that is the Python packages, the images and the packages under
the app's `dependencies`, comes as `fix(deps): …` and becomes the next patch
version, a security update included. That is why everything whose code ends
up in the app stands under `dependencies`: Dexie, Svelte and the PWA plugin
that brings Workbox into the service worker. Tools for development and the
actions come as `chore(…)` and trigger none.

The procedure:

1. Changes reach main through a branch and a pull request, as **one** commit
   (squash). Its message is the title of the pull request. There is no `dev`
   branch. main is the working state, and the threshold to publication is the
   release PR.
2. After every push to main, release-please keeps a pull request **“chore:
   release x.y.z”** up to date. It contains `CHANGELOG.md`, `version.txt`,
   `.release-please-manifest.json` and the line `VERSION` in `.env.example`,
   and nothing else. It may sit there as
   long as things are still being collected.
3. **Publishing happens by merging it.** The same run sets the tag `vx.y.z`,
   creates the release on GitHub and builds the image.

The tags of the image:

| Trigger | Tags |
| --- | --- |
| push to main | `:main` and `:main-<short hash>` |
| release (merged release PR) | plus `:x.y.z`, `:x.y` and `:latest` |

`:latest` is therefore the last **published** version, not the last push to
main, and it only exists from the first release on.

Forcing a particular number (1.0.0, say): a commit on main whose message carries
the line `Release-As: 1.0.0` in its body. The first release was 0.1.0, as
the `initial-version` in `release-please-config.json` says. Without it,
release-please would have started at 1.0.0.

Two quirks: a workflow opens the release PR with the `GITHUB_TOKEN`. Its checks
therefore only run once someone approves the waiting workflows in the PR, and
again after every new state release-please pushes. After that it is merged like
any other. That is also why the title check listens to the `pull_request`
event: on `pull_request_target` GitHub starts no run at all for such a PR, and
it would stay blocked. A token of its own for release-please saves the approval.
And the version
in `pyproject.toml` and `package.json` stays as it is. Neither is a published
package, and a jump there would invalidate `uv.lock`.

### What the CI does

| File | Runs on | What |
| --- | --- | --- |
| `server_ci.yml` | pull request to main | ruff, pyright, system checks, `check --deploy`, missing migrations, then the tests against Postgres with coverage |
| `app_ci.yml` | pull request to main | Prettier, Vitest with coverage, build, then Playwright against a Django with the demo data |
| `audit.yml` | Mondays, and on pull requests touching the lock files and the audit scripts, both only for main | known vulnerabilities in everything third-party that is shipped: Python packages (`pip-audit`), the packages in the build of the counting app (`npm audit`), the libraries under `static/vendor/` (OSV). Plus the licences of all dependencies against a list of allowed ones. On Mondays, a scan of every image from `compose.yaml`, for the project's own image both `:main` and `:latest` |
| `pr_title.yml` | pull request to main | the title follows the Conventional Commits |
| `secrets.yml` | pull request to main | gitleaks over the commits of the pull request |
| `image.yml` | **push** to main, unless it only changes `*.md` or `*.mermaid` | calls the three above, carries the versions forward, builds the image and scans it for known vulnerabilities |
| `image.yml` | pull request to main | builds as a trial, without uploading, and checks `compose.yaml` and `Caddyfile` |

On a push, style and tests therefore run as well. `image.yml` calls the others as
building blocks (`workflow_call`) instead of repeating them. An image built from
red code helps nobody. The image does **not** wait for the audit, though. A
third-party vulnerability without an available fix would otherwise keep every fix
of your own off the server too. The run still turns red. The audit applies only
to main: GitHub always starts a schedule on the default branch, and in a copy
that may be another one. There every job is skipped.

Installation in the CI happens with `uv sync --locked`. If `uv.lock` no longer
matches the `pyproject.toml`, the run fails. (`--frozen` would not do that. It
takes the lock as it is.) So after a change to the dependencies, run `uv lock`.

The tests run with what the image installs: without the development tools,
with the group `postgres` and thus with the C implementation of psycopg. A step
before them checks that it really is loaded. Whoever wants to test against
PostgreSQL locally gets it with `uv sync --group postgres`. That needs a
compiler and the headers of libpq (on Debian `gcc` and `libpq-dev`). Added to
that is the group `test` with `tblib` and `coverage`. Without `tblib` a failure
in the parallel run ends with “cannot pickle 'traceback' object” instead of the
test's name. Locally `uv sync --group test` or `uv run --group test` gets the
group. A plain `uv sync` leaves it out.

The tests run under `coverage`, lines and branches together. If they reach
less than 95 % (`fail_under` in `server/pyproject.toml`), the job turns red.
For the counting app `npm run test:coverage` does the same, with the thresholds
in `app/vite.config.js`. Before the tests `manage.py check --deploy` checks the
settings of operation, with placeholders for an operation with a mail server
and a year of HSTS. Any warning turns the job red.

Third-party actions are pinned to a **commit**, with the version as a comment
behind it. **Dependabot** raises both together and also takes care of `uv.lock`,
of `package-lock.json`, the base images in the `Dockerfile` and the images
in `compose.yaml`, a new major version of PostgreSQL excepted. That happens on
Mondays, one pull request per ecosystem, for the images one per image. A big
jump comes on its own.

What Dependabot does **not** watch are the libraries of the web interface
under `server/static/vendor/` (Bootstrap, Tom Select, SortableJS). They are
bundled in the repository. A new version is fetched by `server/bin/vendor.sh`:
change the version and SRI hash there, run it, click through the interface once.
The script brings the licence texts along, and `THIRD_PARTY_NOTICES.md` follows
in both languages.

If the Monday scan reports vulnerabilities in the published image, usually only
the base image has aged. A new release builds without a cache and brings in the
security updates from Debian (`apt-get upgrade` in the `Dockerfile`). If it
reports vulnerabilities in the database or the proxy that the vendor has not
fixed yet, the finding stands in `.trivyignore.yaml` until then, with a reason
and an expiry date. After that the run turns red again. The scan checks the
project's own image only in the repository that publishes it, and in the image
of the database it passes over `gosu`, which only drops the rights at start. Of
the project's own image it checks `:main` and `:latest`, because which of the
two runs is decided by the server's `.env` (`VERSION`). If the registry lacks
one of them, the scan passes over it with a notice. Before the first release
that is `:latest`.

### Translations

The source language is **German**. Every msgid is the German sentence as it
stands in the code or in the template. The English interface is produced from it
through gettext. Whatever comes from WinTICKET or was entered by a manager
(article, site and family names) stays as it is, in every language.

The catalogues live under `server/locale/`:

| Folder | Contents |
| --- | --- |
| `en/LC_MESSAGES/django.po` | the whole interface in English |
| `de/LC_MESSAGES/django.po` | what Django's own German catalogue leaves open, plus the mirror (see below) |
| `de/LC_MESSAGES/djangojs.po` | the same for the selection field with the two lists, whose texts sit in JavaScript |
| `*.mo` | the compiled translation. Django reads only these files. |

Django's own German catalogue lags behind the version. What is missing there
stands here, and since `LOCALE_PATHS` is read before the catalogues of the
packages, it applies.

**The mirror of the source language.** Below that, the German catalogue holds
every entry of its own once more, as its own translation. That sounds pointless
but is necessary: Django sets the catalogue of `LANGUAGE_CODE` as
the **fallback** for every other language. If the server is set to English, a
German sentence that is missing from the German catalogue would come out in
English for a German account. The block is written
by `bin/mirror_source_catalog.py`. Nothing is added there by hand.

After a new or changed text in the code:

```bash
# from server/, needs gettext (apt install gettext)
uv run manage.py makemessages -l en --ignore=.venv --ignore=pwa --ignore=data
# fill in the new entries in 'locale/en/LC_MESSAGES/django.po', then:
uv run python bin/mirror_source_catalog.py
uv run manage.py compilemessages --ignore=.venv
```

The `--ignore` matters: without it the command also runs over the catalogues of
every language Django brings along. The `.mo` files belong in the repository
(`.gitignore` exempts exactly this folder), so that the image has nothing to
compile and gets by without gettext. That file and translation match, and that no
entry stays empty, is checked by `django_project/test_translations.py` on every
test run.

Three things do **not** run through gettext:

- **The help texts** (`server/templates/help/<language>/`). They are long
  flowing texts, and those sit better as a whole page side by side than as a
  hundred fragments in the catalogue. If a text is missing in English, the German
  one appears.
- **The counting app.** It carries its texts in `app/src/texts.js`, German
  and English side by side, and fetches the language of the account through the
  interface.
- **The scripts of the website** (`server/static/js/`). gettext does not reach
  into a `.js` file. Whatever a script displays is therefore handed to it by the
  template as a `data` attribute on its own `<script>` tag, and
  there `{% translate %}` applies. The text ends up in the catalogue like any other.
  Whoever extends a script writes no word into it, but an attribute next to it.

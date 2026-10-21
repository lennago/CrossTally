# One image, four stages: build the counting app, build the Python
# environment, gather the application's files, then Django with all of it.
#
# The app is not a service of its own but a set of files that Django serves.
# A second image would be a second truth about the same build. Node and the
# compiler stay in their stages and do not come along.

# --------------------------------------------------------------------------
# Stage 1: the PWA
# --------------------------------------------------------------------------
FROM node:26-alpine AS pwa

# The same folder layout as in the repo: 'vite.config.js' builds into
# '../server/pwa', and that has to hold here as well.
WORKDIR /build/app

COPY app/package.json app/package-lock.json ./
RUN npm ci

COPY app/ ./
# The app shows the version it was built from ('vite.config.js'): the build
# argument from 'image.yml', or, built on the machine itself, the state of
# the checkout ('bin/version.sh', from the few files of '.git' the context
# carries). Declared only here, after 'npm ci', so a new version keeps that
# layer cached. The result goes along into the last stage ('VERSION').
#
# '.git' as a pattern next to a file that always exists: an archive has no
# '.git', a worktree and a submodule have a file of that name instead. The
# build goes on without, and the version is "unknown". Until 10/2026 a plain
# 'COPY .git' stopped every such build.
ARG APP_VERSION=
COPY server/bin/version.sh .gi[t] /build/git/
RUN sh /build/git/version.sh "$APP_VERSION" /build/git > /build/VERSION \
    && APP_VERSION="$(cat /build/VERSION)" npm run build

# --------------------------------------------------------------------------
# Stage 2: the Python environment
# --------------------------------------------------------------------------
# The same base image as the last stage: the environment points to its Python.
FROM python:3.14-slim AS python-env

# Python comes from the base image, uv must not download one here.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never

# The same path as in the last stage. An environment carries it in its scripts
# and cannot be moved.
WORKDIR /app

# The C implementation of psycopg is built here, against the system's libpq.
# That needs a compiler and the headers, and neither belongs in operation.
# Hence a stage of its own (reason for the C implementation in 'pyproject.toml').
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        gcc \
        libc6-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# uv is a single file and comes from its own image. Installed is exactly what
# 'uv.lock' records ('--frozen'), without the development tools.
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
COPY server/pyproject.toml server/uv.lock server/.python-version ./
RUN uv sync --frozen --no-dev --group postgres

# --------------------------------------------------------------------------
# Stage 3: the application's files
# --------------------------------------------------------------------------
# Code, app, version and licenses, readable for everyone and writable for
# root alone. The account of the last stage only reads them. A checkout keeps
# the modes of the machine it was made on. With a strict umask (027, 077) its
# files are closed to others, and the account could not read its own code.
# Here the change costs nothing. In the last stage a 'chmod' or 'chown' puts
# every file it touches into the image a second time.
FROM python:3.14-slim AS files

WORKDIR /app

COPY server/ ./
COPY --from=pwa /build/server/pwa ./pwa
# Which version this is. The application shows it in the account menu
# ('settings.py' reads the file).
COPY --from=pwa /build/VERSION ./VERSION
# Whoever passes the image on passes the licenses on too (see the notices).
COPY LICENSE THIRD_PARTY_NOTICES.md THIRD_PARTY_NOTICES.en.md ./
RUN chmod -R u=rwX,go=rX /app

# --------------------------------------------------------------------------
# Stage 4: Django
# --------------------------------------------------------------------------
FROM python:3.14-slim

# With the environment's path in front, 'python' and 'gunicorn' find it
# without 'start.sh' having to know where it lies.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# WeasyPrint requires the system's font and graphics libraries, and the slim
# Python image does not ship them. Without them the first PDF request fails,
# not the start. Plus 'libpq5' for psycopg, which is installed without
# '[binary]' (reason in 'pyproject.toml'). The C implementation from the
# stage before loads the same library. And 'openssl', the program the
# backups are encrypted with ('django_project/encryption.py'). The base image
# happens to bring it along. Named here, it stays when that changes.
#
# 'upgrade' first: the base image is rebuilt only every few weeks and lags
# behind Debian's security updates. The scan in 'image.yml' found a dozen fixed
# vulnerabilities in it. The layer sits in the cache, which is why a release
# builds without it ('no-cache' there).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install --no-install-recommends -y \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        fonts-dejavu-core \
        libpq5 \
        openssl \
    && rm -rf /var/lib/apt/lists/*

# pip is not needed here, the environment comes finished from the stage
# before. It ships its own copies of third-party packages ('pip/_vendor') that
# nobody keeps current. What is not there does not have to be reported by a
# scan.
RUN python -m pip uninstall --yes --quiet pip

# Copied before the source code so the layer is rebuilt only when the
# dependencies change. uv itself stays behind: nothing is installed here any
# more.
COPY --from=python-env /app/.venv ./.venv

# Without the C implementation everything would work, only slower, and nobody
# would notice. psycopg falls back silently. So the build says it.
RUN python -c "import psycopg, sys; sys.exit(psycopg.pq.__impl__ != 'c' and 'psycopg runs as: ' + psycopg.pq.__impl__)"

COPY --from=files /app ./

# The static files are collected at build time, not at every start, because
# they change only with the image. The values here are placeholders. Collecting
# neither touches the database nor needs a secret.
RUN SECRET_KEY=build DEBUG=False ADMIN_URL=build/ DATABASE_URL=sqlite:///build.sqlite3 \
    BACKUP_KEY=only-a-placeholder-for-collecting-static-files \
    python manage.py collectstatic --noinput

# Do not run as root. The account reads '/app' and writes nothing there. Code,
# environment and static files belong to root, so whoever gets to run code in
# the container cannot change the application for good. It writes to
# '/backups' (a mount), to the temporary directory and to its home, where
# gunicorn opens its control socket. Until 10/2026 a 'chown -R' handed it all
# of '/app' and put the whole environment into the image a second time.
RUN useradd --system --create-home --uid 1000 crosstally
USER crosstally

EXPOSE 8000

# Checks its own state without going through the proxy. The request poses as a
# forwarded https request. Otherwise Django without DEBUG answers with a
# redirect to https, which leads nowhere here. If 'BEHIND_TLS_PROXY' is missing
# in operation, Django ignores the header and the container counts as
# unhealthy: exactly the misconfiguration that would also send every caller in
# circles.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/app/', headers={'X-Forwarded-Proto': 'https'}))"

ENTRYPOINT ["./bin/start.sh"]

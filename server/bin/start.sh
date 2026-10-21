#!/bin/sh
# What is to be done at startup. The static files are already in the image.
set -eu

echo "Migrationen …"
# A failed migration must not start the application server. Docker restarts
# the container ('restart: unless-stopped'), so the message would scroll by
# at once. The pause keeps it at the top of the log for a minute and spares
# the database a hot loop. What to do stands in DEPLOYMENT.md.
if ! python manage.py migrate --noinput; then
    echo "Die Migration ist gescheitert. Die Anwendung startet nicht." >&2
    echo "Was zu tun ist, steht in DEPLOYMENT.md unter „Wenn der Start scheitert“." >&2
    sleep 60
    exit 1
fi

echo "Anwendungsserver …"
# Four workers, because a worker serves one request at a time. The counters
# send short requests, but a PDF takes seconds. With two workers, two managers
# rendering one each made a call of the counting app wait for 2.4 seconds.
# With four it was 10 milliseconds (measured on 2026-09-28). A worker takes
# around 70 MB when fresh and up to 200 MB once it has rendered PDFs.
# '--access-logfile -' writes to standard output, which Docker keeps
# ('x-logging' in 'compose.yaml').
# A worker that has rendered PDFs does not give the memory back. After about
# 500 requests gunicorn starts it afresh, between two requests, so nobody
# notices. The jitter keeps the four from restarting at the same moment.
exec gunicorn django_project.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-4}" \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -

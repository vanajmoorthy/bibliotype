#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

# Wait for the database to be ready (optional but good practice)
# We can add this back in for extra safety if needed.

echo "Applying database migrations..."
poetry run python manage.py migrate --noinput

echo "Collecting static files..."
poetry run python manage.py collectstatic --noinput

# Then exec the container's main process (what's set as CMD in the Dockerfile).
# This replaces the script process with the Gunicorn process, which is standard practice.
exec "$@"

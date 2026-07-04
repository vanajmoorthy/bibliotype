#!/bin/sh
# This is the single, unified entrypoint script for all environments.
set -e

# Wait for the database to be ready.
./wait-for-postgres.sh db

# Celery workers skip web bootstrap (migrations + staticfiles). When web and
# worker boot together (docker compose up -d --force-recreate on deploy), both
# used to race `manage.py migrate`; the loser crashed on a duplicate-index
# error and, with no restart policy, stayed dead until manually restarted.
case "$*" in
    *celery*)
        echo "Worker container detected — skipping migrations (web applies them)."
        # Don't start consuming tasks against a half-migrated schema: wait
        # until the web container has applied everything.
        until poetry run python manage.py migrate --check >/dev/null 2>&1; do
            echo "Waiting for web to finish applying migrations..."
            sleep 3
        done
        echo "Migrations are up to date — starting worker."
        ;;
    *)
        echo "Applying database migrations..."
        poetry run python manage.py migrate

        # In production, the web container also collects static files for Nginx.
        if [ "$DJANGO_ENV" = "production" ]; then
            echo "Running in PRODUCTION mode"

            echo "Collecting static files..."
            poetry run python manage.py collectstatic --noinput

            echo "Applying ownership and permissions to staticfiles for Nginx..."
            # The 'www-data' user on the host has a standard UID and GID of 33.
            # We change the ownership of the files inside the container to match.
            chown -R 33:33 /app/staticfiles
            # Set permissions so the owner/group can read/write and others can read.
            chmod -R 775 /app/staticfiles
        else
            echo "Running in DEVELOPMENT mode"
            # No extra steps needed for development before the main command.
        fi
        ;;
esac

# This special command executes the 'command' from the docker-compose file.
exec "$@"

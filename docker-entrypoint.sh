#!/bin/sh
# This is the single, unified entrypoint script for all environments.
set -e

# Wait for the database to be ready.
./wait-for-postgres.sh db

echo "Applying database migrations..."
poetry run python manage.py migrate

# This is the key difference: check the environment variable.
# In production, we need to collect static files.
if [ "$DJANGO_ENV" = "production" ]; then
    echo "Running in PRODUCTION mode"
    echo "Collecting static files..."
    poetry run python manage.py collectstatic --noinput
else
    echo "Running in DEVELOPMENT mode"
    # No extra steps needed for development before the main command.
fi

# This special command executes the 'command' from the docker-compose file.
exec "$@"

#!/bin/sh
set -e

# This script prepares the container for local development

# Wait for the database to be ready.
./wait-for-postgres.sh db

echo "Applying database migrations (local)..."
poetry run python manage.py migrate

# This special command tells the script to run whatever was passed
# as the 'command' in the docker-compose file.
exec "$@"

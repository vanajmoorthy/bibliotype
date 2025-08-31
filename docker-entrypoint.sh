# Corrected docker-entrypoint.sh

#!/bin/sh
set -e

# This script's only job is to wait for Postgres to be ready.
# After it finishes, Docker will run the 'command' from docker-compose.
./wait-for-postgres.sh db

# exec "$@" allows the command from docker-compose to be the container's main process.
exec "$@"

# Corrected Dockerfile

FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y postgresql-client dos2unix curl

# Install Node.js
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Install pnpm
RUN npm install -g pnpm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
ENV POETRY_VIRTUALENVS_CREATE=true

WORKDIR /app

# Copy and install Python dependencies
COPY pyproject.toml poetry.lock ./
RUN pip install poetry
RUN poetry install --no-root --no-interaction

# Copy Node.js files and static directory for building
COPY package.json pnpm-lock.yaml ./
COPY static/ ./static/
COPY tailwind.config.js ./

# Install Node.js dependencies and build Tailwind CSS
RUN pnpm install --frozen-lockfile
RUN pnpm run build

# Copy the rest of the application
COPY . .

RUN dos2unix /app/wait-for-postgres.sh
RUN dos2unix /app/docker-entrypoint.sh

# Make scripts executable
RUN chmod +x /app/wait-for-postgres.sh
RUN chmod +x /app/docker-entrypoint.sh

# The entrypoint will run first, then the command from docker-compose.
ENTRYPOINT ["/app/docker-entrypoint.sh"]


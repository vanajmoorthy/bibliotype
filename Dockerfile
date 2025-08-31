# Start with the correct Python version for your project
FROM python:3.13-slim-bookworm

# Install essential packages and Node.js/npm
RUN apt-get update && apt-get install -y postgresql-client curl gnupg
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
RUN apt-get install -y nodejs

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
ENV POETRY_VIRTUALENVS_CREATE=true

WORKDIR /app

# --- Dependency Installation (Cache this layer) ---
# Install Python dependencies
COPY pyproject.toml poetry.lock ./
RUN pip install poetry
RUN poetry install --no-root --no-interaction

# Install Node.js dependencies
COPY package.json package-lock.json* ./
RUN npm install --legacy-peer-deps

# --- Application Code & Build ---
# NOW, copy the rest of the application source code
COPY . .

# NOW, build the CSS since the config and templates are present
RUN npm run build

# Make entrypoint scripts executable
RUN chmod +x /app/wait-for-postgres.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]

EXPOSE 8000

CMD ["poetry", "run", "gunicorn", "bibliotype.wsgi:application", "--bind", "0.0.0.0:8000"]

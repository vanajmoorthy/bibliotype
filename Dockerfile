FROM python:3.13-slim-bookworm

RUN apt-get update && apt-get install -y postgresql-client

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_NO_INTERACTION=1
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
ENV POETRY_VIRTUALENVS_CREATE=true

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN pip install poetry
RUN poetry install --no-root --no-interaction

COPY . .

COPY ./wait-for-postgres.sh .
COPY ./docker-entrypoint.sh .
RUN chmod +x /app/wait-for-postgres.sh
RUN chmod +x /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]

EXPOSE 8000

CMD ["poetry", "run", "gunicorn", "bibliotype.wsgi:application", "--bind", "0.0.0.0:8000", "--timeout", "120"]

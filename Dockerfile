FROM python:3.13-slim

RUN apt-get update && apt-get install -y postgresql-client

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_NO_INTERACTION=1 
ENV POETRY_VIRTUALENVS_IN_PROJECT=true 
ENV POETRY_VIRTUALENVS_CREATE=true 

WORKDIR /app

COPY ./wait-for-postgres.sh .
COPY ./docker-entrypoint.sh .

RUN pip install poetry

COPY pyproject.toml poetry.lock ./

RUN poetry install --no-root --no-interaction

COPY . .

ENTRYPOINT ["/app/docker-entrypoint.sh"]

EXPOSE 8000

CMD ["poetry", "run", "gunicorn", "bibliotype.wsgi:application", "--bind", "0.0.0.0:8000"]

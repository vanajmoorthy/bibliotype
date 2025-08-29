FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV POETRY_NO_INTERACTION=1 
ENV POETRY_VIRTUALENVS_IN_PROJECT=true 
ENV POETRY_VIRTUALENVS_CREATE=true 

WORKDIR /app

RUN pip install poetry

COPY pyproject.toml poetry.lock ./

RUN poetry install --no-root --no-interaction

COPY . .

EXPOSE 8000

CMD ["/app/.venv/bin/python", "manage.py", "runserver", "0.0.0.0:8000"]

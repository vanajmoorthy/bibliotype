# Project Overview: Bibliotype

Bibliotype is a lightweight web application designed to analyze a user's reading history from Goodreads or StoryGraph export CSV files. It generates a personalized "Reading DNA" dashboard, offering visual insights into reading statistics, genre breakdowns, author affinities, and more. The project aims to provide a "Spotify Wrapped"-like experience for reading data.

**Key Technologies:**
*   **Backend:** Python 3.11+, Django 5.x, Pandas
*   **Frontend:** Django Templates, Tailwind CSS v4, Alpine.js, Chart.js
*   **Database:** SQLite (development), PostgreSQL (production)
*   **Process Management:** Honcho (or Foreman)

## Building and Running

This project requires both Python (for Django) and Node.js/npm (for Tailwind CSS).

### Prerequisites

*   Python 3.11+
*   Node.js and npm

### Setup Instructions

1.  **Clone the repository (if not already done):**
    ```bash
    git clone <repository_url>
    cd bibliotype
    ```

2.  **Python Backend Setup:**
    *   **Create and activate a virtual environment:**
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```
    *   **Install Python dependencies:**
        This project uses `pyproject.toml` for project metadata. You will likely need a `requirements.txt` file for `pip` or use a tool like `poetry` or `pipenv`.
        If `requirements.txt` exists:
        ```bash
        pip install -r requirements.txt
        ```
        If not, you may need to generate it or install dependencies via `poetry install` if `poetry.lock` is present.
    *   **Apply database migrations:**
        ```bash
        python manage.py migrate
        ```

3.  **Frontend (Tailwind CSS) Setup:**
    *   **Install Node.js dependencies:**
        ```bash
        npm install
        ```

### Running the Application

To run the full application, you need to start both the Django development server and the Tailwind CSS watcher.

1.  **Start the Django development server:**
    ```bash
    python manage.py runserver
    ```

2.  **In a separate terminal, start the Tailwind CSS watcher:**
    ```bash
    npm run dev
    ```
    This command watches for changes in `static/src/input.css` and `core/templates/**/*.html` and compiles the CSS to `static/dist/output.css`.

Alternatively, if `Honcho` or `Foreman` is installed, you can use the `Procfile` to run both processes concurrently:
```bash
honcho start
# or
foreman start
```

## Development Conventions

*   **Python Code Formatting:** The project uses `black` and `isort` for Python code formatting, configured with a line length of 120 characters (as indicated in `pyproject.toml`).
*   **Styling:** Tailwind CSS is used for styling, with its configuration in `tailwind.config.js` set to scan HTML templates in `core/templates/` for classes.
*   **Testing:** (No explicit testing commands or frameworks were immediately apparent from the initial file scan. Please refer to project-specific documentation or existing test files for how to run tests. A common Django testing command is `python manage.py test`.)

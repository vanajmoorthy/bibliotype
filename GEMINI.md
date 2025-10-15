# Project Overview: Bibliotype

Bibliotype is a lightweight web application designed to analyze a user's reading history from Goodreads or StoryGraph export CSV files. It generates a personalized "Reading DNA" dashboard, offering visual insights into reading statistics, genre breakdowns, author affinities, and more. The project aims to provide a "Spotify Wrapped"-like experience for reading data.

**Key Technologies:**
*   **Backend:** Python 3.11+, Django 5.x, Pandas
*   **Frontend:** Django Templates, Tailwind CSS v4, Alpine.js, Chart.js
*   **Database:** SQLite (development), PostgreSQL (production)
*   **Process Management:** Honcho (or Foreman)
*   **Containerization:** Docker, Docker Compose

## Project Structure

The project is organized into several key directories:

*   `bibliotype/`: The main Django project directory, containing settings, and configurations.
*   `core/`: The core application logic, including models, views, forms, and services.
    *   `management/commands`: Custom Django management commands for tasks like seeding the database and analyzing data.
    *   `services/`: Business logic services for interacting with external APIs (Google Books) and performing data analysis.
    *   `templates/`: Django templates for rendering the frontend.
*   `csv/`: Sample CSV files for testing and development.
*   `static/`: Static files, including CSS and JavaScript.
*   `scraped_html/`: HTML files scraped from various sources for book data.
*   `.github/workflows/`: GitHub Actions workflows for CI/CD.

## Building and Running

This project can be run using either a local Python environment or Docker. Docker is the recommended method for local development.

### Docker (Recommended)

This method creates a consistent, isolated environment with a dedicated PostgreSQL database, mirroring a production setup.

**1. Prerequisites**

*   Docker and Docker Compose
*   Poetry

**2. Installation & Setup**

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/bibliotype.git
    cd bibliotype
    ```

2.  **Create your environment file:**
    Create a file named `.env` in the project root.
    ```env
    # .env

    SECRET_KEY="generate-a-new-secret-key"
    GEMINI_API_KEY="your-real-gemini-api-key"

    # Credentials for the local PostgreSQL container
    POSTGRES_DB=bibliotype_db
    POSTGRES_USER=bibliotype_user
    POSTGRES_PASSWORD=yoursecurepassword123
    ```

3.  **Build and Run the Containers:**
    ```bash
    docker-compose -f docker-compose.local.yml up --build -d
    ```

**3. Database Setup (First Time Only)**

1.  **Apply Database Migrations:**
    ```bash
    docker-compose -f docker-compose.local.yml exec web poetry run python manage.py migrate
    ```

2.  **Load Initial Data:**
    ```bash
    docker-compose -f docker-compose.local.yml exec web poetry run python manage.py loaddata core/fixtures/initial_data.json
    ```

3.  **Create a Superuser:**
    ```bash
    docker-compose -f docker-compose.local.yml exec web poetry run python manage.py createsuperuser
    ```

You can now access the application at **`http://127.0.0.1:8000`**.

### Local Python Environment

This project requires both Python (for Django) and Node.js/npm (for Tailwind CSS).

**1. Prerequisites**

*   Python 3.11+
*   Node.js and npm

**2. Setup Instructions**

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
        ```bash
        pip install -r requirements.txt
        ```
    *   **Apply database migrations:**
        ```bash
        python manage.py migrate
        ```

3.  **Frontend (Tailwind CSS) Setup:**
    *   **Install Node.js dependencies:**
        ```bash
        npm install
        ```

**3. Running the Application**

To run the full application, you need to start both the Django development server and the Tailwind CSS watcher.

1.  **Start the Django development server:**
    ```bash
    python manage.py runserver
    ```

2.  **In a separate terminal, start the Tailwind CSS watcher:**
    ```bash
    npm run dev
    ```

Alternatively, if `Honcho` or `Foreman` is installed, you can use the `Procfile` to run both processes concurrently:
```bash
honcho start
# or
foreman start
```

## Development Conventions

*   **Python Code Formatting:** The project uses `black` and `isort` for Python code formatting, configured with a line length of 120 characters (as indicated in `pyproject.toml`).
*   **Styling:** Tailwind CSS is used for styling, with its configuration in `tailwind.config.js` set to scan HTML templates in `core/templates/` for classes.
*   **Testing:** A common Django testing command is `python manage.py test`.

## Deployment

The application is deployed to a production environment on a fresh Ubuntu 22.04 server (e.g., a DigitalOcean VPS). The stack uses Docker Compose, Nginx as a reverse proxy, and GitHub Actions for fully automated CI/CD. For detailed instructions, see the `README.md` file.
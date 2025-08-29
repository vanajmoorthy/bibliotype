# 🧬 Bibliotype - Your Reading DNA

Bibliotype is a web application that generates a personalized “Reading DNA” dashboard from a user's Goodreads or StoryGraph export file. It provides visual insights into reading habits and preferences, presented in a fun, shareable, neobrutalist UI inspired by "Wrapped" style summaries.

The app uses a powerful Python backend with Pandas for data analysis and calls the Gemini API to generate a creative, AI-powered "vibe" for each user's unique reading taste.

https://github.com/user-attachments/assets/41540178-f67a-4a48-9105-1a687f034c23


---



## TODO

- Don't show tiles if there is no data for them
- add borders to chart segments to make look consistent
- long author names and genre names cutting of count when hovering on chart
- distribution of length of books
- get rid of books per year
- ai text explanation of most controversial ratings and possible explanations
- make most controversial ratings tab look better
- different colours for different reader types
- pixel square background for banner
- few sentence ai generated bio summary
- ui for login and sign up
- make upload modal icon better and pixel art
- support StoryGraph
- set up public profile
- compare book lengths, number of books read in total, number read per year, average book lengths, number of pages read with global averages
  
## ✨ Features

- **Robust Data Analysis:** Ingests Goodreads export `.csv` files and performs detailed analysis using Pandas.
- **AI-Powered Vibe:** Utilizes Google's Gemini API to generate a creative, multi-phrase "vibe" that poetically summarizes the user's reading taste.
- **Rich Analytics & Dashboard:**
  - **Reader Archetype:** Assigns users a primary "Reader Type" (e.g., *Classic Collector*, *Tome Tussler*).
  - **Core Stats:** Total books & pages read, average rating.
  - **Community Benchmarking:** Compares user stats (like average book length and total books read) against the global Bibliotype user base, showing percentiles.
  - **Taste Analysis:** Identifies top authors and genres, enriched with data from the Open Library API.
  - **Niche vs. Mainstream:** Calculates a "Mainstream Meter" score and highlights the user's most niche book based on community read counts.
  - **Review Insights:** Performs sentiment analysis on user reviews to find their most positive and negative takes.
- **User Accounts & Sharing:**
  - Full user authentication (signup with email, login, logout).
  - Ability to save and update your Bibliotype to your profile.
  - Publicly shareable profile pages (e.g., `bibliotype.com/u/username`).
- **Performant & Scalable:**
  - API calls are cached server-side using Django's cache framework.
  - Caching logic prevents re-running expensive AI generation for unchanged data.
  - Important user data is stored in indexed database fields for efficient querying.

## 🛠️ Tech Stack

- **Backend:** Django 5.x, Python 3.13+
- **Dependency Management:** Poetry
- **Data Processing:** Pandas
- **AI Integration:** Google Generative AI (Gemini)
- **Database:** PostgreSQL (production), SQLite (fallback for non-Docker dev)
- **Containerization:** Docker, Docker Compose
- **Frontend:** Tailwind CSS, Alpine.js, Chart.js

---

## 🚀 Getting Started (Docker & Poetry)

This is the recommended method for local development. It creates a consistent, isolated environment with a dedicated PostgreSQL database, mirroring a production setup.

### 1. Prerequisites

- Docker and Docker Compose
- Poetry
- An environment file for your secrets.

### 2. Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/bibliotype.git
    cd bibliotype
    ```

2.  **Create your environment file:**
    Create a file named `.env` in the project root. This file is ignored by Git and will hold your secret keys.
    ```env
    # .env

    # Generate a new secret key for your project
    SECRET_KEY="django-insecure-your-secret-key-here"

    # Get your API key from Google AI Studio
    GEMINI_API_KEY="your-real-gemini-api-key"

    # Credentials for the local PostgreSQL container
    POSTGRES_DB=bibliotype_db
    POSTGRES_USER=bibliotype_user
    POSTGRES_PASSWORD=yoursecurepassword123
    ```

3.  **Build and Run the Containers:**
    From the project root, run the following command. This will build the Django image, pull the Postgres image, and start both services.
    ```bash
    docker-compose up --build
    ```
    The application will be running at **`http://127.0.0.1:8000`**.

### 3. Database Setup (First Time Only)

The first time you start the Docker environment, you need to set up the database and load your initial data.

Open a **new terminal window** (while `docker-compose up` is running in the other) and run these commands:

1.  **Apply database migrations:**
    ```bash
    docker-compose exec web poetry run python manage.py migrate
    ```

2.  **(Optional) Load seed data:**
    To populate your database with popular books and community analytics for a richer experience, run the seeders:
    ```bash
    docker-compose exec web poetry run python manage.py seed_books
    docker-compose exec web poetry run python manage.py seed_analytics
    ```

3.  **(Optional) Create a superuser:**
    ```bash
    docker-compose exec web poetry run python manage.py createsuperuser
    ```

---

## 🏛️ Project Structure

The project is a standard Django application, containerized with Docker.

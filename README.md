# 🧬 Bibliotype - Your Reading DNA

Bibliotype is a lightweight web app that generates a personalized “Reading DNA” dashboard from a user's Goodreads or StoryGraph export file. It provides visual insights into reading habits, preferences, and statistics, presented in a fun, neobrutalist UI inspired by "Wrapped" style summaries.

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

note: the cache lives on the server side? would it be better to save this data in my sqlite db? then i can also save some other data anonymously and tell the user things like book lengths, number of books read in total, number read per year, average book lengths, number of pages read compared with global averages which i can get somewhere and hardcode but also compare these values against other bibliotype users and i can even give them a rating for how mainstream their taste is in both genres and authors and books and give them their most niche author and book. i can keep "books" with their data in my db and increment a value every time someone has read them and calculate nicheness scores and stuff

## ✨ Features

- **CSV Upload:** Supports both Goodreads and StoryGraph export `.csv` files.
- **Data Analysis:** A powerful backend script written in Python with Pandas analyzes the user's reading history.
- **Dynamic Dashboard:** The results page is an adaptive dashboard that only displays analytics for which there is sufficient data.
- **Rich Analytics:**
  - Core stats: Total books read, total pages read, average rating.
  - Time-based analysis: Books and average rating per year.
  - Reading preferences: Top authors and top genres (enriched via the Open Library API).
  - StoryGraph Exclusives: Common moods and reading pace distribution.
  - Insightful highlights: Most "controversial" books and sentiment analysis of the most positive/negative reviews.
- **Modern Frontend:** Styled with Tailwind CSS v4, with interactive elements powered by Alpine.js and charts by Chart.js.
- **Performant:** API calls for genre data are cached using Django's file-based cache to ensure fast subsequent loads.

## 🛠️ Tech Stack

- **Backend:** Django 5.x, Python 3.11+
- **Data Processing:** Pandas
- **Frontend Build:** Tailwind CSS v4 CLI, `npm`
- **Frontend Libraries:** Alpine.js, Chart.js
- **Database:** SQLite (development)
- **Process Manager:** Honcho / Foreman

---

## 🚀 Getting Started

Follow these instructions to get a local development environment running.

### 1. Prerequisites

- Python 3.11+
- Node.js and `npm`
- A process manager like `honcho` (`pip install honcho`) or `foreman` (`gem install foreman`).

### 2. Installation & Setup

1.  **Clone the repository:**

    ```bash
    git clone <your-repo-url>
    cd bibliotype
    ```

2.  **Set up the Python environment:**

    ```bash
    # Create and activate a virtual environment
    python3 -m venv venv
    source venv/bin/activate

    # Install Python dependencies
    pip install -r requirements.txt
    ```

3.  **Set up the Frontend environment:**

    ```bash
    # Install Node.js dependencies (tailwindcss, etc.)
    npm install
    ```

4.  **Prepare the Django application:**
    ```bash
    # Apply database migrations
    python manage.py migrate
    ```

### 3. Running the Development Server

This project uses a `Procfile` to run both the Django backend and the Tailwind CSS build process simultaneously.

From the project root, simply run:

```bash
honcho start
```

(or `foreman start` if you prefer)

This single command will:

1.  Start the Django development server on `http://127.0.0.1:8000`.
2.  Start the Tailwind CLI watcher, which will automatically rebuild your CSS file (`static/dist/output.css`) whenever you make changes to your templates or `static/src/input.css`.

You can now access the application at **`http://127.0.0.1:8000`** in your web browser.

---

## 🏛️ Project Structure

The project is organized into a main Django project folder (`bibliotype_project`), a core application (`core`), and a root-level frontend build system.

```
/
├── core/                  # Main Django app for all application logic
│   ├── migrations/
│   ├── templates/core/    # All HTML templates reside here
│   ├── analytics.py       # The heart of the project: all CSV parsing and data analysis
│   ├── models.py          # Django data models (e.g., UserProfile)
│   ├── views.py           # Handles HTTP requests and renders templates
│   └── urls.py            # URL routing for the core app
│
├── bibliotype_project/    # Main Django project configuration
│   ├── settings.py        # Project settings (INSTALLED_APPS, database, etc.)
│   └── urls.py            # Root URL configuration
│
├── static/                # Static asset directory
│   ├── src/
│   │   └── input.css      # The source CSS file where the Tailwind theme is defined
│   └── dist/
│       └── output.css     # The final, compiled CSS file (auto-generated)
│
├── manage.py              # Django's command-line utility
├── package.json           # Defines frontend dependencies and build scripts
├── tailwind.config.js     # Configures Tailwind (e.g., content paths)
└── Procfile               # Defines processes for Honcho/Foreman to run
```

### Key Files Explained

- **`core/analytics.py`**: This is the most important file in the project. It contains the `generate_reading_dna` function, which takes the raw CSV content and performs all the data cleaning, analysis, API calls (with caching), and statistical calculations. If you want to add a new analytic, this is the place to start.
- **`static/src/input.css`**: This is the single source of truth for the application's visual theme. It imports the base Tailwind styles and defines all custom colors, fonts, and shadows using the `@theme` directive.
- **`tailwind.config.js`**: This file's primary job is to tell the Tailwind CLI which template files to scan for class names.
- **`core/templates/core/base.html`**: The main site template. It includes all necessary CSS and JS files and defines the navigation bar and overall page structure.
- **`core/templates/core/home.html`**: The landing page, featuring the interactive drag-and-drop file upload component powered by Alpine.js.
- **`core/templates/core/dna_results.html`**: The dashboard template, which uses conditional Django template tags (`{% if %}`) to adaptively render the analytics cards based on the data provided by `analytics.py`.

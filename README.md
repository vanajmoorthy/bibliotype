# 🧬 Bibliotype - Your Reading DNA

Bibliotype is a web application that generates a personalised “Reading DNA” dashboard from a user's Goodreads or StoryGraph export file and provides visual insights into reading habits and preferences.

The app uses a Python backend with Pandas for data analysis and calls the Gemini API to generate a creative, AI-powered vibe for each user's unique reading taste.

https://github.com/user-attachments/assets/41540178-f67a-4a48-9105-1a687f034c23


## TODO

- ✅ distribution of length of books 
- improve mainstream meter
- improve community stats look
- hover states for all buttons
- long author names and genre names cutting of count when hovering on chart
- allow user to delete profile
- ✅ ~get rid of books per year~ 
- ^ make this like more granular month by month and scrollable and show genres in every month
- ~ai text explanation of most controversial ratings and possible explanations~ 
- make most controversial ratings tab look better
- different colours for different reader types
- pixel square background for banner
- make upload modal icon better and pixel art
- support StoryGraph
- upload to instagram story
- ai moodboard/collage. different options of things to upload to instagram 
- SEO stuff
- favicon ⚠️
- adjust copy ⚠️
- ⚠️ make "instructions" button and modal on home page ✅
- posthog integration
- check ui on loading pages for logged in and non logged in
- ui elements for community stats
- update tests
- add cron job to check publishers for mainstreamness
- check lighthouse scores 
- add privacy statement? ToS
- add posthog tracking
- fix prod db
- add silk?
- add little loading text phrases when generating bibliotype for anonymous and logged in
- <img width="150"  alt="71354" src="https://github.com/user-attachments/assets/d6f95eea-2c09-460f-a184-f7406dfbc11b" />
- average "contrarian" score under most controversial ratings with phrases like "my, you're contrarian"
- sign up form validate password and all on blur
- store all books for a user and do similarity score set up for recommendations
- "books you may like"/"books similar readers enjoyed"
- use ai to explain why they might like them and summarise them 
- how similar are you/similarity percentage for 2 or more people
- add "input your csv" link



### Phase 1: Finalize Backend Data Foundation
Upgrade the Database Schema:

- Create a Publisher model with fields for name, a normalized_name for de-duplication, and an is_mainstream boolean flag
- Convert the Book.publisher field from a simple text field to a proper relationship with the new Publisher model.

 Overhaul All Data Scrapers:

- Update the remaining scripts (scrape_nyt, scrape_goodreads, etc.) to use the new "gold standard" logic, saving to the unified Book and Author models.
- Ensure all scrapers use the normalized_name for de-duplicating authors and publishers to prevent duplicates like "J.D. Salinger" vs. "J. D. Salinger".
- Modify scrapers to capture publish_year and page_count whenever the source provides it.

Wipe and Perform a Clean Reseed:

- Completely clear the Book, Author, and Publisher tables in your database.
- Run all of your updated scraping scripts to populate the database with a clean, de-duplicated foundation of culturally significant books.
- Run the enrich_books.py command to fill in missing metadata from the Google Books API.

### Phase 2: Refine Backend Analytics & Logic
Finalize the "Mainstream Score" System:

- Review and finalize the point values in your SCORE_CONFIG based on your complete set of data sources.
- Run the update_scores.py script to calculate a definitive mainstream_score for every book in the database.

Overhaul and Balance Reader Types:

- Review all existing reader type logic to ensure it is balanced and effective.
- Crucially, update the "Small Press Supporter" type to use the new, highly accurate Publisher.is_mainstream flag instead of a fuzzy text search.
- Brainstorm and implement new reader types based on the rich data now available (e.g., "Award Aficionado", "Canon Collector").

Implement Community Popularity Stat Calculation:

- In the backend (generate_reading_dna_task), calculate the popularity brackets for all books on Bibliotype based on global_read_count.
- Determine the read counts that correspond to the top 10%, 10-30%, 30-50%, 50-70%, 70-90%, and bottom 10% of books.
- Write the logic to analyze a user's library and calculate the percentage of their books that fall into each of these six brackets.
- The output should be a data structure ready for a pie chart (e.g., {'Top 10%': 27, '10-30%': 45, ...}).

### Phase 3: Harden the Core Application Logic
(Goal: Make the user-facing DNA generation process robust, efficient, and respectful of API limits.)

Refactor the User Upload Task (generate_reading_dna_task):

- Implement a strict DB-First, API-Fallback strategy for enriching user-uploaded books. The task must always check your own database before making any external API calls.
- Integrate an API Quota Management system. The Celery task must track the daily usage of the Google Books API and switch to a fallback (like Open Library) or simply skip enrichment for the day if the limit is reached.
- Ensure the task reliably and atomically increments the global_read_count for every book a user has read.

### Phase 4: Build New User-Facing Features

(Goal: Translate all the powerful backend data into an engaging and insightful user experience.)

Design and Build the "Mainstream Meter":
- Create a new UI component that visualizes the user's average mainstream_score.
- ~This component should provide a clear, fun explanation of what their score means (e.g., "Niche Navigator", "Cultured Curator", "Mainstream Maven").~

Design and Build the "Bibliotype Popularity" Pie Chart:

- Create a pie chart component that displays the breakdown calculated in Phase 2.
- Each slice of the pie should represent a popularity bracket.

Implement hover-over tooltips for each slice with your custom descriptions:

- Top 10%: "Very Popular!"
- 10-30%: "Quite Popular"
- 30-50%: "Above Average to Average"
 - 50-70%: "A Bit Below Average"
- 70-90%: "Woah, Quite Niche!"
- 90-100%: "What Are You Reading?!"

## ✨ Features

- ** Data Analysis:** Ingests Goodreads export `.csv` files and performs detailed analysis using Pandas.
- **AI-Powered Vibe:** Utilizes Google's Gemini API to generate a creative, multi-phrase "vibe" that poetically summarizes the user's reading taste.
- **Analytics & Dashboard:**
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
- **AI Integration:** Gemini
- **Database:** PostgreSQL (production), SQLite (fallback for non-Docker dev)
- **Containerization:** Docker, Docker Compose
- **Frontend:** Tailwind CSS, Alpine.js, Chart.js


## 🚀 Getting Started (Docker & Poetry)

This is the recommended method for local development. It creates a consistent, isolated environment with a dedicated PostgreSQL database, mirroring a production setup.

### 1. Prerequisites

- Docker and Docker Compose
- Poetry

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

    SECRET_KEY="generate-a-new-secret-key"
    GEMINI_API_KEY="your-real-gemini-api-key"

    # Credentials for the local PostgreSQL container
    POSTGRES_DB=bibliotype_db
    POSTGRES_USER=bibliotype_user
    POSTGRES_PASSWORD=yoursecurepassword123
    ```

3.  **Build and Run the Containers:**
    From the project root, run the following command. The `-d` flag runs the services in the background.
    ```bash
    docker-compose -f docker-compose.local.yml up --build -d
    ```

### 3. Database Setup (First Time Only)

The first time you start the Docker environment, you need to set up the database. Open a **new terminal window** and run these commands:

1.  **Apply Database Migrations:**
    This command creates all the necessary tables in the new PostgreSQL database.
    ```bash
    docker-compose -f docker-compose.local.yml exec web poetry run python manage.py migrate
    ```

2.  **Load Initial Data:**
    This command populates the database with a large catalog of books and pre-calculated community analytics from a local fixture file. This is the fastest way to get started.
    ```bash
    docker-compose -f docker-compose.local.yml exec web poetry run python manage.py loaddata core/fixtures/initial_data.json
    ```

3.  **Create a Superuser:**
    This allows you to access the Django admin panel at `/admin/`.
    ```bash
    docker-compose -f docker-compose.local.yml exec web poetry run python manage.py createsuperuser
    ```

You can now access the application at **`http://127.0.0.1:8000`**.

#### Optional: Refreshing the Fixture File

If you update the book list in `seed_books.py` and want to regenerate the `initial_data.json` fixture, follow these steps:
1.  `docker-compose -f docker-compose.local.yml down -v`
2.  `docker-compose -f docker-compose.local.yml up --build -d`
3.  `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py migrate`
4.  `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py seed_books`
5.  `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py seed_analytics`
6.  `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py dumpdata core.Book core.Author core.Genre core.AggregateAnalytics --indent 2 > core/fixtures/initial_data.json`
7.  Commit the updated `initial_data.json` file to Git.

## 🚀 Deploying to Production

This guide outlines the steps to deploy the application to a production environment on a fresh Ubuntu 22.04 server (e.g., a DigitalOcean VPS). The stack uses Docker Compose, Nginx as a reverse proxy, and GitHub Actions for fully automated CI/CD.

### 1. Initial Server Setup

1.  **Create an Ubuntu 22.04 Server:**
    *   Provision a new VPS and ensure you can connect via SSH using your public key.

2.  **Create a Deployment User:**
    *   Log in as `root` and create a dedicated non-root user for the deployment.
        ```bash
        # Replace 'deploy' with your preferred username
        adduser deploy
        ```
    *   Grant this user `sudo` privileges and add them to the `docker` group (this requires installing Docker first, see next step).
        ```bash
        usermod -aG sudo deploy
        ```
    *   Copy your SSH key to the new user so you can log in directly:
        ```bash
        # This command copies the keys from the root user
        rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/
        ```
    *   Log out and log back in as your new `deploy` user.

3.  **Install Software & Configure Firewall:**
    *   Install Docker, Nginx, and Certbot.
        ```bash
        # Install Docker
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker $USER # Add current user to docker group

        # Install Nginx and Certbot
        sudo apt update
        sudo apt install nginx python3-certbot-nginx -y
        ```
    *   Configure the firewall to allow web and SSH traffic.
        ```bash
        sudo ufw allow OpenSSH
        sudo ufw allow 'Nginx Full'
        sudo ufw enable
        ```
    *   **Important:** Log out and log back in to apply the Docker group permissions. Verify with `docker ps`.

### 2. Project Setup on the Server

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/your-username/bibliotype.git app
    cd app
    ```

2.  **Create the Production `.env` File:**
    Create a new `.env` file for production secrets. **Use strong, unique credentials.**
    ```bash
    nano .env
    ```
    Paste and edit the following content:
    ```ini
    # .env (Production)

    SECRET_KEY="..."
    GEMINI_API_KEY="..."

    POSTGRES_DB=bibliotype_prod_db
    POSTGRES_USER=bibliotype_prod_user
    POSTGRES_PASSWORD="..."

    DEBUG=False
    ALLOWED_HOSTS="your_domain.com,www.your_domain.com"
    ```

3.  **Create the Static Files Directory:**
    This empty folder on the host will be mapped into the container so Nginx can access the collected static files.
    ```bash
    mkdir staticfiles
    ```

### 3. Nginx & SSL Configuration

1.  **Create Nginx Config:**
    Create a new configuration file for your site.
    ```bash
    sudo nano /etc/nginx/sites-available/bibliotype
    ```
    Paste the following configuration, replacing `your_domain.com` and `/home/deploy/app/` with your actual values.
    ```nginx
    server {
        listen 80;
        server_name your_domain.com www.your_domain.com;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name your_domain.com www.your_domain.com;

        # Path for static files
        location /static/ {
            alias /home/deploy/app/staticfiles/;
        }

        # Proxy requests to the Django app
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # SSL settings will be added by Certbot below
    }
    ```

2.  **Enable the Site & Get SSL Certificate:**
    *   Activate the configuration by creating a symlink.
        ```bash
        sudo ln -s /etc/nginx/sites-available/bibliotype /etc/nginx/sites-enabled/
        ```
    *   **Point your domain's DNS A records** to your server's IP address.
    *   Run Certbot to obtain an SSL certificate and automatically update the Nginx config.
        ```bash
        sudo certbot --nginx -d your_domain.com -d www.your_domain.com
        ```

### 4. Setting Up GitHub Actions for CI/CD

1.  **Create a Deploy-Specific SSH Key:**
    On your **local machine**, create a new SSH key pair dedicated to this deployment. Do not use your personal key.
    ```bash
    ssh-keygen -t ed25519 -C "github-deploy-bibliotype" -f ~/.ssh/bibliotype_deploy_key
    ```

2.  **Add Public Key to Server:**
    Copy the content of the **public key** (`cat ~/.ssh/bibliotype_deploy_key.pub`) and paste it as a new line in your server's `/home/deploy/.ssh/authorized_keys` file.

3.  **Add Secrets to GitHub Repository:**
    Go to `Your Repo > Settings > Secrets and variables > Actions` and add the following repository secrets:
    *   `DO_SSH_HOST`: Your server's IP address.
    *   `DO_SSH_USERNAME`: Your deployment username (e.g., `deploy`).
    *   `DO_SSH_KEY`: The content of the **private key** (`cat ~/.ssh/bibliotype_deploy_key`).
    *   `DOCKERHUB_USERNAME`: Your Docker Hub username.
    *   `DOCKERHUB_TOKEN`: A Docker Hub access token.

4.  **Configure Passwordless `sudo`:**
    The deployment script needs to fix file permissions. Allow your deploy user to run `sudo` without a password.
    *   Run `sudo visudo` on your server.
    *   Add this line at the very bottom of the file (replace `deploy` if you used a different username):
        ```
        deploy ALL=(ALL) NOPASSWD: ALL
        ```

### 5. First Deployment

Commit your final `docker-compose.prod.yml`, `.github/workflows/deploy.yml`, and `settings.py` files to your repository and push to the `main` branch.

```bash
git push origin main
```

The GitHub Action will now run and automatically build, test, and deploy your application. Subsequent pushes to `main` will automatically update the live site.


# 🧬 Bibliotype

Bibliotype is a web application that generates a personalised “Reading DNA” dashboard from a user's Goodreads or StoryGraph export file and provides visual insights into reading habits and preferences

The app uses a Python backend with Pandas for data analysis and calls the Gemini API to generate a creative, AI-powered vibe for each user's unique reading taste.

https://github.com/user-attachments/assets/41540178-f67a-4a48-9105-1a687f034c23


## TODO / Known Undone Work

A living backlog of everything known to be unfinished, deferred, or in-progress. Grouped
by theme; roughly prioritised within each group. `🙅 = not started`, `🚧 = partial/in-progress`,
`❓ = needs verification`. Sourced from code TODOs, `docs/plans/`, `docs/handoffs/`, and
in-flight worktrees — see those docs for full detail.

### Ops / deploy — do first (added 2026-07-31)
- **Google Books API key** — rotate/replace it and set its restriction to "none" or an IP
  allowlist (NOT "HTTP referrers") so server-side enrichment stops 403ing; make sure the Books
  API is enabled on the project; update prod `.env`; `docker compose -f docker-compose.prod.yml up -d --force-recreate web worker`; delete the old key.
- Confirm prod redeployed the latest `main` (settings-modal hardening + redesign, enrichment
  timeout widening + log key-redaction, live-enrichment recompute) and that web/worker
  restarted on the new image.
- Re-test a Goodreads upload end to end: enrichment should finish instead of hanging around
  ~93%, with far fewer Open Library timeouts and no Google Books 403s.
- Browser smoke-test the settings modal: change password; privacy/recs toggles (neobrutalist
  switches); confirm password managers can fill/save. (Email change was **removed** — see below.)
- Post-deploy monitoring: no synthetic user tests / error-rate dashboards / enrichment-uptime
  tracking exist in-repo. 🙅

### Genre & fiction/nonfiction split 🚧
- Lock down the canonical genre set and improve mapping (`CANONICAL_GENRE_MAP` / `GENRE_PRIORITY`
  in `core/dna_constants.py`, `core/services/genre_classification.py`); improve the
  fiction/non-fiction split. 8 canonical genres landed (PR #120/#121); coverage still partial.
- Better Open Library subject → canonical genre matching; Goodreads enrichment only covers
  ~65% of books (StoryGraph ~80%+ via tag extraction).
- `STORYGRAPH_TAG_TO_GENRE` mappings are lossy (e.g. memoir→biography, mystery→thriller) —
  may need refinement.
- Genre-split round-trip tests: fixtures for both Goodreads + StoryGraph asserting split
  counts sum correctly. ❓
- See `docs/plans/2026-03-02-feat-genre-accuracy-and-fiction-nonfiction-split-plan.md` and
  `docs/GENRES.md`.

### Reader type 🚧 (landed PR #125; decisions deferred)
- Adjust reader-type calculations. Over a 200-library synthetic corpus, 9 of 20 types never
  win (Nature Nut Case, Social Savant, History Hound, Comfort Rereader, Series Slayer, Modern
  Maverick, Rapacious Reader, Tome Tussler, Novella Navigator, Eclectic Reader) — decision on
  retiring/cleaning them up is deferred pending review.
- Distribution-domination cap relaxed from ≤25% to ≤30% (Fantasy Fanatic ~26.5%, structural to
  the test corpus) — can be tightened in follow-up.
- Reread/series signals aren't guard-gated like genre/page/year signals; tiny libraries with
  rereads can still score Comfort Rereader / Series Slayer. Documented as defensible; may refine.
- Per-type visuals: different colour per type? pixel-square banner background? animated? 🙅
- Legacy: all 20 types kept, version-gated via `reader_type_scores_version`; pre-PR13 profiles
  show raw leaderboard scores without `%`.
- See `docs/plans/2026-07-04-feat-reader-type-overhaul-plan.md`, `core/services/dna/reader_type.py`,
  `core/tests/test_reader_type_distribution.py`.

### Comparative analytics & controversial ratings redesign 🚧
- Decision (2026-07-29): use **colour posters + swipe deck** for Comparative Analytics and
  **podium bars** for Most Controversial Ratings. Prototype partials exist under
  `core/templates/core/partials/dna/ideas/` (`idea1/2/3_comparative.html` — incl. the "You vs
  The World" scoreboard — `idea2_controversial.html`, `podium_bars.html`) but are **not landed**;
  when implemented they replace `comparative_analytics_card.html` / `controversial_ratings_card.html`.
- Mobile swipe-deck snap-scroll needs a Tailwind rebuild; preserve enrichment-banner
  (opacity-60) + "Still enriching" behaviour.
- **Global averages**: recompute the comparative "global averages" from community aggregates
  once N is large enough (literature-derived constants today — `GLOBAL_AVERAGES_SOURCES`).
- Link/refresh the methodology-page sources for comparative analytics.
- See `docs/plans/2026-07-29-feat-comparative-controversial-redesign-prompt.md`.

### Live enrichment 🚧 (PR #127 landed reader-type recompute; stat updates incomplete)
- Live stat updates only touch 3 text nodes (`#stat-pages`, `#mainstream-score`,
  `#stat-avg-length`). Still stale during enrichment: top-genres list, top-genres donut chart
  (Chart.js instance never updated), fiction/nonfiction split (numbers + chart), book extremes,
  and most comparative-analytics body text. Extend `_compute_enrichment_stats` +
  `enrichment_status_view` and wire `Alpine.store('enrichment')` to drive chart updates.
- Reader type doesn't update live for users who were mid-enrichment when PR #127 shipped
  (no `reader_type_csv_context` in stored `dna_data`) — only new uploads get it.
- Polling backoff: currently a fixed 5s. Stretch to 10–15s once percent > 90% to cut DB churn
  during the long tail.
- Banner lifecycle (x-show → x-if): PR #131 landed; ❓ verify banner DOM unmounts cleanly and
  dead `$store.enrichment` refs are gone.
- See `docs/plans/live-enrichment-updates-plan.md`.

### Enrichment performance & robustness 🚧
- Re-upload re-enriches already-attempted books that got zero genres; add a
  `last_enrichment_attempt` timestamp + 24h skip to avoid wasteful API calls.
- Speed: ~5–13s/book. Direct ISBN lookup for StoryGraph books (skip the search call) unclear
  if landed. `ENABLE_PARALLEL_ENRICHMENT` exists (default off).
- Concurrent uploads from the same user can hang at 50% — need "revoke previous upload".
- Banner overlap on small cards (key stats, comparative sub-tiles) — add `pt-8` / thinner banner.
- Skeleton cohesion pass: the three skeleton templates work but have inconsistent copy ("Still
  figuring out…" / "Still discovering…" / "Still fetching…") and incoherent design.
- Cover-art probe `<img>` absolute-positioning needs a designer pass to coordinate with the
  comparative-analytics tile.
- See `docs/plans/enrichment-ux-improvements.md`.

### StoryGraph follow-ups 🚧 (core support landed, PR #98)
- Extract `Moods` / `Pace` for DNA scoring + vibe generation (Read Count for reread detection
  is done; Moods/Pace wiring unclear). ❓
- Existing-book title-update policy: `update_or_create` ISBN match keeps the oldest row's
  title. Decide: update on match, or document keep-oldest as the invariant (likely intentional).
- Book extremes (longest/shortest) missing for StoryGraph during enrichment (no page counts) —
  skeleton exists but see cohesion pass above.

### Recommendations & similarity 🚧
- "How similar are you?" — similarity percentage between 2+ **public** users, with a dedicated
  comparison page (N-way, not just 1:1). 🙅
- Uniqueness badge: verify the `uniqueness-badge` django-waffle switch is actually deployed;
  badge shows "One of a kind" / "Pretty unique" from `max_similarity_pct` (threshold 0.40). ❓
- Eligible-pool-count display ("out of over X readers" with magnitude-aware flooring) — verify
  `recommendations_pool_size()` + `friendly_floor()` landed. ❓
- Backwards-compat: old `recommendations_meta` lacks `max_similarity_pct`/uniqueness fields;
  template must degrade gracefully (count-only copy) until users regenerate.
- See `docs/plans/2026-07-10-feat-similar-readers-stat-and-uniqueness-badge-plan.md`.

### Book covers 🚧
- Covers are fetched + stored during enrichment but **only appear after a page refresh** —
  initial dashboard render shows crosshatch placeholders. Verify lazy-load / re-query after
  enrichment completes (`core/services/_book_urls.py`, `cover_url`).

### AI vibe / LLM 🙅
- Cache the AI vibe against a DNA-dictionary hash (~1-month TTL, refetch only if the dict
  changed) so the same library reuses a vibe instead of re-hitting Gemini — also avoids
  generating many vibes during testing.
- Add PostHog metrics for LLM vibe quality/usage.
- Improve the AI-generated vibe itself.

### UI/UX polish
- Add a show/reveal-password button; the "forgot password" link sits too close to the input.
- Fix the button hover animation (shadow-shrink timing).
- Long author/genre names get cut off (with counts) when hovering on charts — truncate +
  tooltip or widen the legend. 🙅

### Testing / QA ❓
- Verify integration tests (`test_integration.py`, `test_storygraph_integration.py`) reflect
  the post-PR#125 reader-type model and don't rely on stale assumptions.

### Cleanup / legacy debt (low priority)
- `Classic Collector → History Hound` compat mapping in `dna_constants.py`.
- Legacy recommendation shape support (pre-nested `rec['book']`).
- US-033 removed legacy DNA-stats backfill — no synthetic stats for missing fields (intended).

### Recently shipped (for reference, not TODO)
- ✅ Settings modal: redesign (merged profile box + pink Settings button, live public/private,
  neobrutalist toggle switches, wider modal, corner-overlap X), plus security hardening
  (rate limits, throttled endpoints, cache invalidation, form/autocomplete semantics).
- ✅ In-app **email change removed** entirely (was an unauthenticated-reauth takeover vector).
- ✅ StoryGraph CSV upload support (PR #98).
- ✅ Sign-up form validates password on blur.

##  Getting Started (Docker & Poetry)

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

## Deploying to Production

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


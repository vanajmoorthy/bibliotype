# 🧬 Bibliotype

Bibliotype is a web application that generates a personalised “Reading DNA” dashboard from a user's Goodreads or StoryGraph export file and provides visual insights into reading habits and preferences

The app uses a Python backend with Pandas for data analysis and calls the Gemini API to generate a creative, AI-powered vibe for each user's unique reading taste.

https://github.com/user-attachments/assets/41540178-f67a-4a48-9105-1a687f034c23


## TODO / Known Undone Work

A living backlog of everything known to be unfinished, deferred, or in-progress — sourced from
`docs/plans/`, `docs/handoffs/`, `docs/GENRES.md`, `docs/SCALING.md`,
`docs/scaling-implementation-plan.md`, `docs/ARCHITECTURE.md`, code, and in-flight worktrees.
Legend: 🙅 not started · 🚧 partial/in-progress · ❓ needs verification · ✅ shipped (kept briefly
for reference). See the linked docs for full detail.

### Ops / deploy — do first
- ~~**Google Books API key** — rotate/replace it and set its restriction to "none" or an IP allowlist~~
  ~~(NOT "HTTP referrers") so server-side enrichment stops 403ing; make sure the Books API is enabled~~
  ~~on the project; update prod `.env`;~~
  ~~`docker compose -f docker-compose.prod.yml up -d --force-recreate web worker`; delete the old key.~~
- **Re-enrich existing books on the VPS** — run `enrich_books --process-all` to re-fetch/merge Google
  Books genres into books already in the DB. Until this runs, existing users' genres + fiction/nonfiction
  splits only refresh when they re-upload. (Pending in both the 2026-07-22 and 2026-07-29 handoffs.)
- Re-test a Goodreads upload end to end: enrichment should finish instead of hanging around ~93%, with
  far fewer Open Library timeouts and no Google Books 403s.
- Post-deploy monitoring gap: no synthetic user tests, error-rate dashboards, or enrichment-uptime
  tracking exist in-repo. 🙅
- ✅ Prod is on the latest `main` (settings-modal redesign + hardening, comparative/controversial
  redesign, small-text readability — the redesign was verified live on the deployed site).

### Genre & fiction/nonfiction split 🚧
- Lock down the canonical genre set and improve mapping (`CANONICAL_GENRE_MAP` / `GENRE_PRIORITY` in
  `core/dna_constants.py`, `core/services/genre_classification.py`). 8 canonical genres landed
  (PR #120/#121); coverage still partial.
- **~35% of books get zero genres** on some Goodreads libraries — Open Library / Google Books subjects
  don't match the canonical map (case-insensitivity, "fiction" / "literary fiction" aliases). StoryGraph
  is higher (~80%+ via tag extraction). Confirm how much the #120/#121 overhaul actually closed this. ❓
- `STORYGRAPH_TAG_TO_GENRE` mappings are lossy (e.g. memoir→biography, mystery→thriller) — may need
  refinement.
- Known classification simplifications (`docs/GENRES.md` §8): `poetry` is always classified as fiction;
  ambiguous genres are always stored as the *fiction* default in the Genre DB (the nonfiction variant
  exists only as an analysis-time label, e.g. "classic fiction" never "classic nonfiction"); the
  StoryGraph `Tags` column is not parsed for shelf-style signals (format unverified — only the tag→genre
  map applies).
- Genre-split round-trip tests: fixtures for both Goodreads + StoryGraph asserting split counts sum
  correctly. ❓
- See `docs/plans/2026-03-02-feat-genre-accuracy-and-fiction-nonfiction-split-plan.md`, `docs/GENRES.md`.

### Reader type 🚧 (overhaul landed PR #125; several decisions deferred)
- **Retirement decision pending a verdict to Vanaj.** Over a 200-library synthetic corpus, ~9–11 of 20
  types never win (candidates: Nature Nut Case, Social Savant, History Hound, Comfort Rereader, Series
  Slayer, Modern Maverick, Rapacious Reader, Tome Tussler, Novella Navigator, Eclectic Reader). Decide
  whether to drop/clean any.
- Distribution-domination cap relaxed ≤25% → ≤30% (Fantasy Fanatic ~26.5%, structural to the test
  corpus) — can be tightened later.
- Reread/series signals aren't `MIN_SIGNAL_BOOKS`-guarded like genre/page/year signals; tiny libraries
  with rereads can still score Comfort Rereader / Series Slayer. Documented as defensible; may refine.
- **Series Slayer is unearnable for StoryGraph uploads** (no series markers) — permanent limitation.
- No fleet `regenerate_dna` on prod and no backfill of `reader_type_csv_context` — existing profiles keep
  their old vocabulary/scoring until users re-upload (intentional). `AnonymizedReadingProfile.reader_type`
  keeps the old vocabulary permanently (no migration).
- Out of scope / future: per-type share-card palettes + pixel-art mascots/banners; percentile-based
  community-relative scoring ("you're in the top X% of rereaders"); different colour / animated banner
  per type. 🙅
- See `docs/plans/2026-07-04-feat-reader-type-overhaul-plan.md`, `core/services/dna/reader_type.py`,
  `core/tests/test_reader_type_distribution.py`.

### Live enrichment 🚧 (PR #127 landed reader-type recompute; stat/chart updates incomplete)
- ~~Live stat updates only touch 3 text nodes (`#stat-pages`, `#mainstream-score`, `#stat-avg-length`).~~
  ~~Still stale during enrichment: top-genres list, top-genres donut (Chart.js instance never updated),~~
  ~~fiction/nonfiction split (numbers + chart), book extremes, and most comparative-analytics body text.~~
  ~~Extend `_compute_enrichment_stats` + `enrichment_status_view` and wire `Alpine.store('enrichment')` to~~
  ~~drive chart updates.~~
- Reader type doesn't update live for users who were mid-enrichment when PR #127 shipped (no
  `reader_type_csv_context` in stored `dna_data`) — only new uploads get it.
- Live reader-type *movement* (PR 14) was never browser-verified — hard to stage in-progress enrichment
  locally; eyeball on a genuinely fresh upload post-deploy. ❓
- ~~Polling backoff: fixed 5s today. Stretch to 10–15s once percent > 90% to cut DB churn during the long tail.~~
- ~~Banner lifecycle (x-show → x-if): PR #131 landed; verify the banner DOM unmounts cleanly and dead~~
  ~~`$store.enrichment` refs are gone.~~
- See `docs/plans/live-enrichment-updates-plan.md`.

### Enrichment performance & robustness 🚧
- ~~Re-upload re-enriches already-attempted books that got zero genres; add a `last_enrichment_attempt`~~
  ~~timestamp + 24h skip. Re-upload can also *show complete prematurely* when old books dominate~~
  ~~`all_attempted` — scope the completion check to the current upload session.~~
- **Verify the enrichment cache doesn't expire** — `SCALING.md` Phase A leans on caching enrichment
  indefinitely; confirm current behaviour in `book_enrichment_service.py`. ❓
- Speed ~5–13s/book. ~~Direct ISBN lookup for StoryGraph books (skip the search call) unclear if landed.~~
  `ENABLE_PARALLEL_ENRICHMENT` exists (default off); book-sync uses a single-threaded executor as a
  deliberate placeholder for future parallelism.
- ~~Concurrent uploads from the same user can hang at 50% — need a reject-while-pending or~~
  ~~revoke-previous-upload guard.~~
- ~~Banner overlap on small cards (key stats, comparative sub-tiles) — add `pt-8` / thinner banner.~~
- ~~Skeleton cohesion pass: the three skeleton templates work but have inconsistent copy ("Still figuring~~
  ~~out…" / "Still discovering…" / "Still fetching…") and incoherent design.~~
- Cover-art probe `<img>` absolute-positioning needs a designer pass to coordinate with the
  comparative-analytics tile.
- See `docs/plans/enrichment-ux-improvements.md`, `docs/plans/2026-04-06-feat-enrichment-ux-and-performance-plan.md`.

### Scaling, performance & infra 🙅 (none of `docs/scaling-implementation-plan.md` applied yet)
- **Phase 1 (app):** ~~Gunicorn threaded workers~~; DB `conn_max_age` + health checks; Celery task time
  limits + worker recycling; ~~frontend polling backoff (3s→10s / 5s→15s)~~.
- **Phase 2 (VPS):** 1 GB swap + swappiness tuning; nginx tuning (`client_max_body_size`, gzip, static
  caching, rate limiting); Redis `maxmemory` cap; small-RAM Postgres tuning; per-container `mem_limit`s.
- **Phase 3 (ops):** Silk `.prof` disk cleanup; Docker + Django log rotation; daily Postgres backup cron;
  UFW firewall; Uptime Robot monitoring; resource-monitoring cron.
- **Google Books free quota ceiling (10k calls/day)** binds with many cold uploads. Mitigation ladder
  (unbuilt): lean on Open Library + cache enrichment indefinitely → paid GB tier → pre-enrich popular
  books from a curated NYT/Goodreads-top-1000 list so cold uploads hit cache.
- **Single Celery worker** serializes uploads until a 2nd worker is added (needs the $12+ tier).
- ~~**Deploy migration race:** web + worker both run `migrate`; the worker can die on a duplicate-index~~
  ~~error with no restart policy. Gate migrations to web only, or add `restart: unless-stopped` on the worker.~~
- Deferred to higher tiers: PgBouncer / managed Postgres, WhiteNoise, Cloudflare CDN, Sentry/APM,
  splitting web + Celery onto separate droplets.
- See `docs/SCALING.md`, `docs/scaling-implementation-plan.md`.

### Security & auth follow-ups 🚧
- **US-017 email enumeration** not fully closed — signup redirects still differ by whether the email
  exists; the real fix is an email-verification flow (deferred as feature work).
- ~~**US-024 double-dispatch window** — `_save_dna_to_profile` doesn't set the dispatch sentinel before~~
  ~~`.delay()`; a poll in the millisecond window can double-dispatch the recommendations task (bounded to~~
  ~~2, idempotent). Worth a follow-up story.~~
- **`is_public` now defaults to `True`** for new signups — confirm that's the intended launch posture.
- Settings-modal cache lag: other users' `similar_users_{id}` / `user_recommendations_{id}` caches may
  hold a now-private user until TTL expiry — accepted as low-severity, not fixed.
- ✅ In-app email change removed entirely (was an unauthenticated-reauth account-takeover vector).

### Recommendations & similarity 🚧
- "How similar are you?" — similarity percentage between 2+ **public** users on a dedicated N-way
  comparison page (not just 1:1). 🙅
- **Remove the legacy `min_overlap_pct` write + fallback** once most active users have regenerated —
  still present at `core/tasks/recommendations.py:103` ("kept one release for stale templates/meta").
- Tune the 0.40 "weak match" uniqueness threshold once real `max_similarity_pct` distributions are
  visible (a PostHog bucket can inform it).
- ~~Verify the `uniqueness-badge` django-waffle switch is deployed (badge shows "One of a kind" /~~
  ~~"Pretty unique"), and the eligible-pool-count display ("out of over X readers", magnitude-aware~~
  ~~flooring — `recommendations_pool_size()` + `friendly_floor()`).~~
- Backwards-compat: old `recommendations_meta` lacks `max_similarity_pct`/uniqueness fields; the template
  must degrade to count-only copy until users regenerate.
- See `docs/plans/2026-07-10-feat-similar-readers-stat-and-uniqueness-badge-plan.md`.

### Book covers 🚧
- ~~Covers are fetched + stored during enrichment but **only appear after a page refresh** — the initial~~
  ~~dashboard render shows crosshatch placeholders. Verify lazy-load / re-query after enrichment completes~~
  ~~(`core/services/_book_urls.py`, `cover_url`).~~

### AI vibe / LLM 🙅
- ~~Cache the AI vibe against a DNA-dictionary hash (~1-month TTL, refetch only if the dict changed) so an~~
  ~~identical library reuses a vibe instead of re-hitting Gemini — also avoids generating many vibes during~~
  ~~testing.~~
- Add PostHog metrics for LLM vibe quality/usage; improve the generated vibe itself.

### Comparative analytics — residual (redesign ✅ shipped, PRs #140/#141)
- **Recompute the "global averages" from community aggregates** once N is large enough — they're
  literature-derived constants today (`GLOBAL_AVERAGES_SOURCES`). ~~Note it as "future work" on the~~
  ~~methodology page.~~
- (Redesign done: colour-poster swipe deck + podium bars, 50/50 profile row, always-3-line legend with
  the World Avg item as the methodology source link, finish-date caveat moved to the methodology page.)

### UI / UX polish 🚧
- **Share cards** (`core/templates/core/partials/share/*`) still use `text-[10px]` / `text-xs` —
  deliberately skipped in the site-wide readability bump because they're fixed-canvas 320×568 PNG
  downloads and bumping overflows the frame. A future pass must redesign the layout to enlarge the text. 🙅
- neo-3d shadow bevel follow-ups: purple variants in `small_button.html` / `small_link_button.html` were
  skipped (they use `hover:bg-opacity-90` instead of the standard shadow-shrink chain); cards/modals/inputs
  not evaluated (would need a stateless `neo-3d-static` utility — YAGNI for now); consider `@property`
  transitions for the bevel.
- ~~Add a show/reveal-password button; the "forgot password" link sits too close to the input.~~
- ~~Fix the button hover animation (shadow-shrink timing).~~
- Long author/genre names get cut off (with counts) when hovering on charts — truncate + tooltip or widen
  the legend. 🙅
- ~~Let users delete their profile from the settings panel.~~

### Testing / QA ❓
- Verify integration tests (`test_integration.py`, `test_storygraph_integration.py`) reflect the
  post-PR#125 reader-type model and don't rely on stale assumptions.
- Wire StoryGraph `Moods` / `Pace` into DNA scoring + vibe generation (Read Count for reread detection is
  done; Moods/Pace wiring unclear). ❓

### Cleanup / legacy debt (low priority)
- `EXCLUDED_GENRES` dedup refactor (~1000 duplicate lines) in `dna_constants.py` — mechanical, low value.
- `Classic Collector → History Hound` compat mapping in `dna_constants.py`.
- Legacy recommendation shape support (pre-nested `rec['book']`).
- Existing-book title-update policy: `update_or_create` ISBN match keeps the oldest row's title — decide
  update-on-match vs document keep-oldest as the invariant (likely intentional).
- US-033 removed the legacy DNA-stats backfill — no synthetic stats for missing fields (intended).

### Recently shipped (reference, not TODO)
- ✅ Comparative Analytics → colour-poster swipe deck; Most Controversial Ratings → podium bars;
  number-line legend cleanup (sorted, always 3 lines, World Avg = source link); site-wide small-text
  readability bump; 50/50 profile + Update box row (PRs #140/#141).
- ✅ Settings modal redesign + security hardening (rate limits, throttled endpoints, cache invalidation);
  in-app email change removed.
- ✅ Reader-type overhaul (PR #125); similar-readers stat + uniqueness badge; neo-3d shadow bevels;
  StoryGraph CSV upload (PR #98); live-enrichment reader-type recompute (PR #127/#131).

## Backlog / Known Undone Work

Compiled from `docs/handoffs/`, the reader-type & live-enrichment plans in `docs/plans/`,
`docs/GENRES.md`, and recent sessions. **The freeform list above is partly stale** — the
settings modal, reader-type colours/pixel banners, the methodology page, StoryGraph support,
password toggles, and the `x-if` enrichment-banner lifecycle have all since shipped.

### Reader types
- **Type-retirement decision is deferred — all 20 types are still kept.** Over a 200-library
  synthetic corpus only 11/20 ever won; these never did: History Hound, Nature Nut Case, Social
  Savant, Comfort Rereader, Series Slayer, Modern Maverick, Rapacious Reader, Tome Tussler,
  Novella Navigator, Eclectic Reader (all remain reachable via engineered libraries). Decide
  whether to drop any. Decision doc: `docs/plans/2026-07-04-feat-reader-type-overhaul-plan.md`;
  calibration histogram prints in `test_no_type_dominates_distribution`.
- **Distribution-domination cap was relaxed 25% → 30%** (Fantasy Fanatic sits ~26.5%, largely
  structural to the test corpus) and the Eclectic ≥1% lower bound was dropped. Tighten later if
  you want a stricter guarantee.
- **Reread / Series signals aren't `MIN_SIGNAL_BOOKS`-guarded** — a tiny library (<10 books) with
  rereads can still score Comfort Rereader / Series Slayer. Possible future refinement, not a bug.
- **No backfill** of reader types for existing users; they recompute on next upload.

### Genre & fiction/non-fiction
- **Post-deploy genre refresh not yet run on the VPS:** `manage.py enrich_books --process-all`
  re-fetches and merges Google Books genres into existing books (also resets
  `google_books_last_checked`). Until it runs, existing users' genres/splits only refresh on
  re-upload. *(pending manual VPS action)*
- **Canonical-genre mapping is still being tightened** to improve genre accuracy and the
  fiction/non-fiction split (ongoing).
- **Known simplification:** the Genre DB always stores the *fiction* default for ambiguous genres
  ("classic fiction", never "classic nonfiction"); the non-fiction variant only exists as an
  analysis-time label (`docs/GENRES.md` §8).
- Long author/genre names truncate the count on chart hover.

### Live enrichment (dashboard reactivity)
- ~~**Live-enrichment "part 1" appears unfinished:** the top-genres list+donut, fiction/non-fiction~~
  ~~numbers+pie, and book-extremes tiles still freeze until the completion reload — only ~3 stats~~
  ~~swap live. Spec: `docs/plans/live-enrichment-updates-plan.md` (PR1 section). *(verify current~~
  ~~behaviour — PR 12 was never in the "done" set of the 2026-07-29 handoff.)*~~
- ~~**Book covers only appear after a manual refresh** on DNA generation; they should load in place.~~
- Reader-type live recompute shipped (PR 14) but hasn't been eyeballed on a genuinely fresh upload
  post-deploy.
- ~~Concurrent uploads can stick at 50% — revoke the previous upload.~~
- ~~Polling is a fixed 5s; stretch to 10–15s once percent > 90% to cut DB churn on the long tail.~~

### Comparative-analytics stats
- **Derive the global-average constants from community data.** `avg_books_per_year` (now 4, a
  survey median), `avg_book_length_pages` (375) and `avg_publish_year` (2009) are hand-set in
  `GLOBAL_AVERAGES` (`core/dna_constants.py`). Once `AggregateAnalytics` holds enough readers,
  replace them with community-derived baselines — the methodology page already promises this.
- **`avg_publish_year` (2009) is the weakest figure** — no authoritative source measures "the
  average publication year of books people read"; it's an explicit best-effort estimate. Strongest
  fix: compute a median from a named dataset or from our own corpus.

### AI vibe / LLM
- ~~**Cache LLM vibes by library-hash:** reuse the generated vibe when an identical library (same~~
  ~~dictionary hash) is uploaded instead of re-calling Gemini; cache ~1 month, refetch only when the~~
  ~~dictionary changes. Avoids redundant generations during testing.~~
- Improve the generated vibe and add LLM metrics to PostHog.

### Similarity
- Multi-user similarity: a dedicated "how similar are you" page comparing 2+ (public) users.

### Deploy / infra
- **Deploy propagation lag:** `docker-compose.prod.yml`'s `web` service has both `build: .` and
  `image:`, and the deploy runs `docker compose up` without a `git pull`, so the live site can lag
  noticeably after a push before the container swaps (looks stale right after deploy, then
  resolves). Consider `up --no-build` / pull-only to make deploys deterministic.
- **US-017:** signup user-enumeration is reduced but not eliminated; a full fix needs an
  email-verification flow (deferred user-facing work).
- **Ripe legacy-compat cleanups:** the pre-US-002 in-flight `task_id` shim and the US-032
  recommendations dual-shape fallback were 30-day paths and are now safe to remove.

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


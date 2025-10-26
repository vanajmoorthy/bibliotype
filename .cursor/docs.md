# Bibliotype - Comprehensive Project Documentation

## 🧬 Project Overview

**Bibliotype** is a Django-based web application that generates personalized "Reading DNA" dashboards from users' Goodreads or StoryGraph export files. It provides visual insights into reading habits and preferences using AI-powered analysis.

### Key Features
- **Data Analysis**: Ingests Goodreads/StoryGraph CSV exports and performs detailed analysis using Pandas
- **AI-Powered Vibe**: Utilizes Google's Gemini API to generate creative, multi-phrase "vibe" summaries
- **Reader Archetypes**: Assigns users primary "Reader Types" (e.g., Classic Collector, Tome Tussler)
- **Community Benchmarking**: Compares user stats against global Bibliotype user base
- **Taste Analysis**: Identifies top authors and genres with enriched data
- **Niche vs Mainstream**: Calculates "Mainstream Meter" scores
- **Review Insights**: Performs sentiment analysis on user reviews
- **User Accounts & Sharing**: Full authentication with publicly shareable profiles

## 🛠️ Tech Stack

### Backend
- **Framework**: Django 5.x
- **Language**: Python 3.13+
- **Database**: PostgreSQL (production), SQLite (fallback)
- **Task Queue**: Celery with Redis
- **AI Integration**: Google Gemini API
- **Data Processing**: Pandas
- **Caching**: Redis

### Frontend
- **Styling**: Tailwind CSS 4.x
- **JavaScript**: Alpine.js
- **Charts**: Chart.js
- **Font**: VT323 (retro/pixel art style)

### Infrastructure
- **Containerization**: Docker, Docker Compose
- **Dependency Management**: Poetry
- **Deployment**: Nginx reverse proxy, SSL via Certbot
- **CI/CD**: GitHub Actions

## 📁 Project Structure

```
bibliotype/
├── bibliotype/           # Django project settings
│   ├── settings.py      # Main configuration
│   ├── urls.py          # URL routing
│   ├── celery.py        # Celery configuration
│   └── wsgi.py          # WSGI application
├── core/                 # Main Django app
│   ├── models.py        # Database models
│   ├── views.py         # View functions
│   ├── urls.py          # App URL patterns
│   ├── forms.py         # Django forms
│   ├── tasks.py         # Celery background tasks
│   ├── services/        # Business logic services
│   │   ├── dna_analyser.py      # Core DNA analysis
│   │   ├── llm_service.py       # AI/Gemini integration
│   │   ├── author_service.py    # Author mainstream checking
│   │   └── publisher_service.py  # Publisher analysis
│   ├── templates/       # HTML templates
│   │   ├── base.html           # Base template
│   │   ├── home.html           # Upload page
│   │   ├── dna_display.html    # Results dashboard
│   │   ├── login.html          # Authentication
│   │   └── public_profile.html # Shareable profiles
│   ├── management/      # Django management commands
│   └── fixtures/        # Database seed data
├── static/              # Static assets
│   ├── src/input.css    # Tailwind source
│   └── dist/output.css  # Compiled CSS
├── csv/                 # Sample/test CSV files
├── docker-compose.local.yml  # Local development
├── docker-compose.prod.yml   # Production deployment
└── requirements.txt     # Python dependencies
```

## 🗄️ Database Models

### Core Models

#### UserProfile
- Extends Django's User model
- Stores DNA data as JSON
- Tracks reading vibe and caching
- Manages public/private profile settings

#### Book
- Central book entity with normalized titles
- Links to Author and Publisher models
- Tracks global read counts
- Stores enrichment data (genres, ratings, etc.)

#### Author
- Author information with mainstream status
- Normalized names for deduplication
- Popularity scoring system

#### Publisher
- Publisher hierarchy support
- Mainstream classification
- Parent/subsidiary relationships

#### Genre
- Canonical genre mapping
- Used for categorization and analysis

#### AggregateAnalytics
- Singleton model for community statistics
- Stores percentile distributions
- Powers comparative analytics

## 🔄 Application Flow

### 1. File Upload Process
```
User uploads CSV → Validation → Celery Task → DNA Analysis → Results Display
```

### 2. DNA Analysis Pipeline
1. **Data Cleaning**: Parse CSV, filter "read" books
2. **Database Sync**: Create/update Book/Author/Publisher records
3. **Enrichment**: Fetch additional metadata from APIs
4. **Analysis**: Calculate reader types, statistics, percentiles
5. **AI Generation**: Create personalized "vibe" using Gemini
6. **Storage**: Save to user profile (authenticated) or cache (anonymous)

### 3. Reader Type Assignment
Uses scoring system based on:
- Book length preferences (Tome Tussler vs Novella Navigator)
- Genre preferences (Fantasy Fanatic, Non-Fiction Ninja)
- Publication era (Classic Collector, Modern Maverick)
- Publisher type (Small Press Supporter)
- Reading volume (Rapacious Reader)
- Genre diversity (Versatile Valedictorian)

## 🎨 UI/UX Design

### Design System
- **Retro/Pixel Art Aesthetic**: VT323 font, bold borders, shadow effects
- **Color Palette**: Brand colors (yellow, pink, cyan, green, purple)
- **Neumorphism**: Shadow effects for depth
- **Responsive**: Mobile-first design with Tailwind

### Key Components
- **Upload Interface**: Drag-and-drop CSV upload with instructions modal
- **Dashboard**: Comprehensive reading DNA display with charts
- **Charts**: Books per year, genre distribution, author breakdown
- **Mainstream Meter**: Visual gauge showing mainstream vs niche preferences
- **Public Profiles**: Shareable profile pages

## 🔧 Development Setup

### Prerequisites
- Docker and Docker Compose
- Poetry (for dependency management)

### Local Development
1. Clone repository
2. Create `.env` file with required variables:
   ```
   SECRET_KEY="your-secret-key"
   GEMINI_API_KEY="your-gemini-api-key"
   POSTGRES_DB=bibliotype_db
   POSTGRES_USER=bibliotype_user
   POSTGRES_PASSWORD=yourpassword
   ```
3. Start containers: `docker-compose -f docker-compose.local.yml up --build -d`
4. Run migrations: `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py migrate`
5. Load initial data: `docker-compose -f docker-compose.local.yml exec web poetry run python manage.py loaddata core/fixtures/initial_data.json`
6. Access at `http://localhost:8000`

### Key Management Commands
- `seed_books`: Populate database with book catalog
- `seed_analytics`: Generate community statistics
- `enrich_books`: Fetch additional book metadata
- `merge_duplicates`: Clean up duplicate records

## 🚀 Production Deployment

### Infrastructure
- Ubuntu 22.04 server
- Docker containers
- Nginx reverse proxy
- SSL certificates via Certbot
- GitHub Actions CI/CD

### Environment Variables
- `SECRET_KEY`: Django secret key
- `GEMINI_API_KEY`: Google Gemini API key
- `POSTGRES_*`: Database credentials
- `DEBUG=False`: Production mode
- `ALLOWED_HOSTS`: Domain configuration

## 📊 Data Sources & APIs

### Primary Data Sources
- **Goodreads**: CSV export format
- **StoryGraph**: CSV export format
- **Google Books API**: Book metadata enrichment
- **Open Library**: Fallback metadata source

### Data Processing
- **Pandas**: CSV parsing and analysis
- **VADER Sentiment**: Review sentiment analysis
- **Custom Algorithms**: Reader type scoring, mainstream detection

## 🔍 Key Features Deep Dive

### Mainstream Meter
- Calculates percentage of books from mainstream publishers/authors
- Visual gauge showing niche vs mainstream preferences
- Based on publisher hierarchy and author popularity

### Community Analytics
- Tracks global read counts for books
- Calculates percentile rankings
- Provides comparative statistics

### AI-Powered Vibe Generation
- Uses Gemini API to create personalized reading descriptions
- Cached based on book list hash to avoid regeneration
- Generates creative, poetic summaries of reading taste

### Reader Type System
- 12+ distinct reader archetypes
- Scoring algorithm based on multiple factors
- Explanatory descriptions for each type

## 🧪 Testing

### Test Structure
- Unit tests for individual components
- Integration tests for full workflows
- E2E tests for user journeys

### Test Files
- `test_tasks_unit.py`: Celery task testing
- `test_tasks_integration.py`: Full pipeline testing
- `test_views_e2e.py`: End-to-end user flows

## 📈 Performance Considerations

### Caching Strategy
- Redis for session storage and task results
- DNA data caching to avoid regeneration
- API response caching for external calls

### Database Optimization
- Indexed fields for common queries
- Normalized names for efficient lookups
- Singleton pattern for analytics data

### Background Processing
- Celery for heavy computation
- ThreadPoolExecutor for API calls
- Rate limiting for external APIs

## 🔐 Security & Privacy

### Data Handling
- Ephemeral processing for anonymous users
- Secure storage for authenticated users
- No permanent storage of uploaded files

### Authentication
- Django's built-in user system
- Email-based login
- Session management

### API Security
- Rate limiting on external API calls
- User-Agent headers for identification
- Error handling for API failures

## 🚧 Future Enhancements

### Planned Features
- StoryGraph support expansion
- Instagram story sharing
- AI moodboard generation
- Book recommendation system
- User similarity matching
- Enhanced SEO optimization

### Technical Improvements
- PostHog analytics integration
- Enhanced caching strategies
- Performance monitoring
- Automated testing expansion

## 📝 Development Notes

### Code Organization
- Service layer pattern for business logic
- Celery tasks for background processing
- Template inheritance for consistent UI
- Utility functions for common operations

### Error Handling
- Comprehensive exception handling
- User-friendly error messages
- Logging for debugging
- Graceful degradation for API failures

### Data Quality
- Normalization for consistent data
- Deduplication strategies
- Validation for user inputs
- Cleanup utilities for data maintenance

---

This documentation provides a comprehensive overview of the Bibliotype project, covering architecture, features, development setup, and deployment considerations. The application successfully combines data analysis, AI integration, and modern web development practices to create an engaging reading personality analysis tool.

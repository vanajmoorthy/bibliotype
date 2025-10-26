import logging
from django.utils import timezone
from ..models import AnonymizedReadingProfile, AnonymousUserSession, Author

logger = logging.getLogger(__name__)

def anonymize_session(anonymous_session):
    """Convert an AnonymousUserSession to AnonymizedReadingProfile"""
    
    dna = anonymous_session.dna_data
    user_stats = dna.get('user_stats', {})
    
    # Only anonymize if meets quality thresholds
    total_books = user_stats.get('total_books_read', 0)
    if total_books < 10:
        logger.info(f"Skipping anonymization of session {anonymous_session.session_key}: too few books ({total_books})")
        return None
    
    AnonymizedReadingProfile.objects.create(
        total_books_read=total_books,
        reader_type=dna.get('reader_type', 'Eclectic Reader'),
        genre_distribution=anonymous_session.genre_distribution or {},
        author_distribution=anonymous_session.author_distribution or {},
        average_rating=dna.get('average_rating_overall') if isinstance(dna.get('average_rating_overall'), (int, float)) else None,
        avg_book_length=user_stats.get('avg_book_length'),
        avg_publish_year=user_stats.get('avg_publish_year'),
        mainstream_score=dna.get('mainstream_score_percent'),
        genre_diversity_count=len(anonymous_session.genre_distribution or {}),
        top_book_ids=anonymous_session.top_books_data or [],
        source='anonymous',
    )
    
    anonymous_session.anonymized = True
    anonymous_session.save()
    
    logger.info(f"Anonymized session {anonymous_session.session_key}")
    return True


def batch_anonymize_expired_sessions():
    """Batch process expired sessions for anonymization"""
    
    expired_sessions = AnonymousUserSession.objects.filter(
        expires_at__lt=timezone.now(),
        anonymized=False
    )
    
    anonymized_count = 0
    for session in expired_sessions:
        try:
            if anonymize_session(session):
                anonymized_count += 1
        except Exception as e:
            logger.error(f"Error anonymizing session {session.session_key}: {e}")
    
    # Clean up old anonymized sessions (keep for 30 days after anonymization)
    cleanup_date = timezone.now() - timezone.timedelta(days=30)
    AnonymousUserSession.objects.filter(
        anonymized=True,
        expires_at__lt=cleanup_date
    ).delete()
    
    return anonymized_count



"""Recommendations task: build and persist per-user recommendation payloads."""

import logging

from celery import shared_task
from django.contrib.auth.models import User
from django.utils import timezone

logger = logging.getLogger(__name__)


# name pinned: wire name must survive the package split (queued msgs + beat)
@shared_task(bind=True, max_retries=3, name="core.tasks.generate_recommendations_task")
def generate_recommendations_task(self, user_id: int):
    """
    Generate and store recommendations for a user after their DNA is created/updated.
    This runs asynchronously so it doesn't slow down DNA generation.
    """
    from ..cache_utils import safe_cache_delete
    from ..services.recommendation_service import get_recommendations_for_user

    # Track whether the task is terminally complete. Cleared on retry so
    # the sentinel set in display_dna_view stays in place across the retry
    # countdown and continues to block duplicate dispatches.
    clear_sentinel_on_exit = True

    try:
        user = User.objects.get(pk=user_id)
        profile = user.userprofile

        # Only generate if user has DNA data
        if not profile.dna_data:
            logger.warning(f"Cannot generate recommendations for user {user_id}: no DNA data")
            return None

        logger.info(f"Generating recommendations for user {user_id}")

        recommendations = get_recommendations_for_user(user, limit=6)

        # Imported here to avoid a circular import with core.views.
        from ..views import BADGE_COLOR_MAP

        processed_recs = []
        for rec in recommendations:
            book = rec["book"]
            processed_rec = {
                "book_id": book.id,
                "book_title": book.title,
                "book_author": book.author.name,
                "book_average_rating": book.average_rating,
                "confidence": rec.get("confidence", 0),
                "confidence_pct": int(rec.get("confidence", 0) * 100),
                "score": rec.get("score", 0),
                "recommender_count": rec.get("recommender_count", 0),
                "genre_alignment": rec.get("genre_alignment", 0),
                "sources": rec.get("sources", []),
                "explanation_components": rec.get("explanation_components", {}),
                # US-032: bake the nested book dict templates expect, so
                # views no longer need to reconstruct it on every render.
                "book": {
                    "id": book.id,
                    "title": book.title,
                    "author": {"name": book.author.name},
                    "average_rating": book.average_rating,
                },
            }

            primary_source_user = None
            best_similarity = 0
            for source in rec.get("sources", []):
                if source.get("type") == "similar_user":
                    if source.get("similarity_score", 0) > best_similarity:
                        best_similarity = source.get("similarity_score", 0)
                        primary_source_user = source

            if primary_source_user:
                # US-032: bake badge_class alongside the source so the view
                # can skip the legacy expansion when "book" is already set.
                match_quality = primary_source_user.get("match_quality", "")
                primary_source_user["badge_class"] = BADGE_COLOR_MAP.get(match_quality, "bg-brand-purple")
                processed_rec["primary_source_user"] = primary_source_user

            processed_recs.append(processed_rec)

        similar_user_set = set()
        min_overlap_pct = None
        for rec in processed_recs:
            for source in rec.get("sources", []):
                if source.get("type") == "similar_user" and source.get("user_id"):
                    similar_user_set.add(source["user_id"])
                    similarity = source.get("similarity_score", 0)
                    overlap = int(round(similarity * 100))
                    if min_overlap_pct is None or overlap < min_overlap_pct:
                        min_overlap_pct = overlap

        recommendations_meta = {
            "similar_users_count": len(similar_user_set),
            "min_overlap_pct": min_overlap_pct or 0,
        }

        profile.recommendations_data = processed_recs
        profile.recommendations_meta = recommendations_meta
        profile.recommendations_generated_at = timezone.now()
        profile.save(update_fields=["recommendations_data", "recommendations_meta", "recommendations_generated_at"])

        # Also clear the cache so fresh data is used
        safe_cache_delete(f"user_recommendations_{user_id}")

        logger.info(f"Successfully generated and stored {len(processed_recs)} recommendations for user {user_id}")
        return len(processed_recs)

    except User.DoesNotExist:
        logger.warning(f"User with id {user_id} not found for recommendations generation")
        return None
    except Exception as e:
        logger.error(f"Error generating recommendations for user {user_id}: {e}", exc_info=True)
        # Task is being re-queued; the sentinel must outlive this attempt so
        # dashboard polls don't dispatch a duplicate while the retry is pending.
        clear_sentinel_on_exit = False
        raise self.retry(countdown=60 * (2**self.request.retries), exc=e)
    finally:
        # Clear the dispatch sentinel set in display_dna_view so the next
        # dashboard poll can spawn a fresh task if needed.
        if clear_sentinel_on_exit:
            safe_cache_delete(f"recs_dispatching_{user_id}")

"""Upload + task-status views: CSV upload, DNA/task polling, and enrichment status endpoints."""

import logging
from io import StringIO

import pandas as pd
from celery.result import AsyncResult
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from ..analytics.events import track_file_upload_started
from ..cache_utils import DNA_CACHE_TTL, safe_cache_get, safe_cache_set
from ..tasks import generate_reading_dna_task
from ._helpers import _compute_enrichment_progress

logger = logging.getLogger(__name__)

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024  # 10MB CSV upload limit

# Upload validation caps (US-018). 50k rows comfortably covers the largest
# Goodreads exports observed in the wild; 100 cols is well above any real
# export schema and rejects pathological inputs before pandas does deep work.
MAX_UPLOAD_ROWS = 50000
MAX_UPLOAD_COLUMNS = 100


@require_POST
def upload_view(request):
    csv_file = request.FILES.get("csv_file")

    if not csv_file or not csv_file.name.endswith(".csv"):
        messages.error(request, "Please upload a valid .csv file.")
        return redirect("core:home")

    try:
        if csv_file.size > MAX_UPLOAD_SIZE_BYTES:
            messages.error(request, "File is too large. Please upload an export smaller than 10MB.")
            return redirect("core:home")

        # Track file upload started
        track_file_upload_started(request, csv_file.size)

        # utf-8-sig transparently strips BOM if present (some exports include it)
        csv_content = csv_file.read().decode("utf-8-sig")

        # US-018: pre-flight validation. Read only the first MAX_UPLOAD_ROWS rows
        # so a pathological CSV can't exhaust memory before we even start the
        # task. Then verify column count and schema look like Goodreads or
        # StoryGraph before passing csv_content downstream.
        try:
            df_head = pd.read_csv(StringIO(csv_content), nrows=MAX_UPLOAD_ROWS)
        except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError):
            messages.error(
                request,
                "CSV could not be parsed. Please upload a valid Goodreads or StoryGraph export.",
            )
            return redirect("core:home")

        if len(df_head.columns) > MAX_UPLOAD_COLUMNS:
            messages.error(
                request,
                f"CSV has too many columns (limit {MAX_UPLOAD_COLUMNS}). "
                "Please upload a Goodreads or StoryGraph export.",
            )
            return redirect("core:home")

        head_columns = set(df_head.columns)
        if not ({"Title", "Author"}.issubset(head_columns) or {"Title", "Authors"}.issubset(head_columns)):
            messages.error(
                request,
                "CSV does not look like a Goodreads or StoryGraph export. "
                "Expected columns: Title and Author/Authors.",
            )
            return redirect("core:home")

        # If the input exceeded the row cap, re-serialize the truncated head so
        # the downstream task doesn't reload the full file. For typical-sized
        # uploads (well under the cap) we pass csv_content through unchanged.
        if len(df_head) >= MAX_UPLOAD_ROWS:
            csv_content = df_head.to_csv(index=False)

        if request.user.is_authenticated:
            # Clear old session data so the view will use the updated profile data
            request.session.pop("dna_data", None)

            # If a previous upload is still running, revoke it before dispatching
            # the new one. Without this, both tasks contend on the same Book/Author
            # row locks in Postgres and the new task's progress bar stalls until
            # the old task releases its locks ("stuck at 50%").
            prior_task_id = request.user.userprofile.pending_dna_task_id
            if prior_task_id:
                try:
                    prior_result = AsyncResult(prior_task_id)
                    if not prior_result.ready():
                        prior_result.revoke(terminate=True, signal="SIGTERM")
                        logger.info(
                            f"Revoked pending DNA task {prior_task_id} for user {request.user.id} "
                            f"because a new upload superseded it"
                        )
                except Exception as e:
                    # Best-effort: a failed revoke shouldn't block the new upload.
                    # The nonce mechanism still protects enrichment tasks; the worst
                    # case is brief lock contention until the prior task finishes.
                    logger.warning(
                        f"Failed to revoke prior DNA task {prior_task_id} for user {request.user.id}: {e}"
                    )

            # Store upload nonce BEFORE dispatching the task so enrichment tasks
            # from previous uploads can detect they've been superseded. Using uuid
            # rather than task ID since the task hasn't been created yet.
            import uuid

            upload_nonce = str(uuid.uuid4())
            safe_cache_set(f"upload_nonce_{request.user.id}", upload_nonce, timeout=DNA_CACHE_TTL)

            result = generate_reading_dna_task.delay(csv_content, request.user.id)

            # Save the pending task ID to track regeneration progress.
            # Use .update() instead of fetching + saving the related object — the
            # eager task path runs the DNA save inline, and a cached stale
            # UserProfile instance here would clobber the DNA the task just wrote.
            from ..models import UserProfile

            UserProfile.objects.filter(user=request.user).update(pending_dna_task_id=result.id)

            messages.success(
                request,
                "Success! We're updating your Reading DNA. Your dashboard will update automatically when it's ready!",
            )

            processing_url = reverse("core:display_dna") + "?processing=true"

            return redirect(processing_url)
        else:
            # Ensure the session has a session_key so we can bind ownership of
            # the task to this caller. Without this, an attacker who guesses or
            # leaks the task_id could pull another visitor's DNA into their
            # own session.
            if request.session.session_key is None:
                request.session.save()
            session_key = request.session.session_key

            result = generate_reading_dna_task.delay(csv_content, None, session_key)
            task_id = result.id

            request.session["anonymous_task_id"] = task_id
            safe_cache_set(f"task_owner_{task_id}", session_key, DNA_CACHE_TTL)
            request.session.save()

            return redirect("core:task_status", task_id=task_id)

    except Exception as e:
        logger.error(f"Unexpected error in upload_view: {e}", exc_info=True)
        messages.error(request, "An unexpected error occurred. Please try again.")
        return redirect("core:home")


def check_dna_status_view(request):
    # For anonymous users, they don't have pending task tracking
    if not request.user.is_authenticated:
        return JsonResponse({"status": "PENDING"})

    profile = request.user.userprofile
    profile.refresh_from_db()  # Ensure we get the latest data from the database

    # If there's a pending task ID, DNA is still being generated
    if profile.pending_dna_task_id:
        try:
            result = AsyncResult(profile.pending_dna_task_id)

            # Check for failure first
            if result.state == "FAILURE":
                profile.pending_dna_task_id = None
                profile.save(update_fields=["pending_dna_task_id"])
                return JsonResponse({"status": "FAILURE", "error": "An error occurred while processing your file."})

            info = result.info or {}
            current = info.get("current")
            total = info.get("total")
            stage = info.get("stage", "")
            progress = None
            if current is not None or total is not None or stage:
                percent = None
                if isinstance(current, int) and isinstance(total, int) and total > 0:
                    percent = round((current * 100) / total)
                progress = {"current": current, "total": total, "percent": percent, "stage": stage}
            return JsonResponse({"status": "PENDING", "progress": progress})
        except Exception:
            return JsonResponse({"status": "PENDING"})

    # Otherwise, check if DNA data exists
    if profile.dna_data:
        return JsonResponse({"status": "SUCCESS"})
    else:
        return JsonResponse({"status": "PENDING"})


@login_required
def check_recommendations_status_view(request):
    """AJAX endpoint to check if recommendations have been generated."""
    profile = request.user.userprofile
    profile.refresh_from_db()

    if profile.recommendations_data:
        return JsonResponse({"status": "ready"})
    return JsonResponse({"status": "pending"})


@login_required
def enrichment_status_view(request):
    """AJAX endpoint for polling enrichment progress. Returns updated stats for live dashboard updates."""
    profile = request.user.userprofile
    dna_data = profile.dna_data
    if not dna_data:
        return JsonResponse({"pending": False})

    progress = _compute_enrichment_progress(request.user, profile, dna_data)
    if progress is None or not progress["pending"]:
        return JsonResponse({"pending": False})

    user_stats = dna_data.get("user_stats", {})
    return JsonResponse({
        **progress,
        "updated_stats": {
            "total_pages_read": user_stats.get("total_pages_read", 0),
            "avg_book_length": user_stats.get("avg_book_length", 0),
            "top_genres": dna_data.get("top_genres", []),
            "mainstream_score_percent": dna_data.get("mainstream_score_percent", 0),
            "fiction_nonfiction_split": dna_data.get("fiction_nonfiction_split"),
        },
    })


def task_status_view(request, task_id):
    return render(request, "core/task_status.html", {"task_id": task_id})


def get_task_result_view(request, task_id):
    from ..models import AnonymousUserSession

    # US-002 (hardened post-review): refuse task_ids that aren't bound to the
    # caller's session. Both cache-miss AND mismatch fail closed — the previous
    # "warn-and-allow" legacy path still wrote DNA into the requester's session,
    # which combined with the signup view's `if "dna_data" in request.session`
    # branch to re-open the original hijack.
    owner = safe_cache_get(f"task_owner_{task_id}")
    caller_key = request.session.session_key
    is_owner = owner is not None and caller_key is not None and owner == caller_key
    if not is_owner:
        import hashlib

        caller_hash = hashlib.sha256(caller_key.encode()).hexdigest()[:12] if caller_key else "none"
        if owner is None:
            # TTL expired or task_id never bound (pre-US-001 in-flight, or invalid).
            logger.info(
                "task_owner cache miss",
                extra={"task_id": task_id, "caller_hash": caller_hash},
            )
        else:
            owner_hash = hashlib.sha256(owner.encode()).hexdigest()[:12]
            logger.warning(
                "task_owner mismatch — cross-session access attempted",
                extra={"task_id": task_id, "owner_hash": owner_hash, "caller_hash": caller_hash},
            )
        return JsonResponse({"status": "FORBIDDEN"}, status=403)

    cached_result = safe_cache_get(f"dna_result_{task_id}")
    if cached_result is not None:
        request.session["dna_data"] = cached_result
        # Also store book IDs and ratings from AnonymousUserSession if it exists
        if request.session.session_key:
            try:
                anon_session = AnonymousUserSession.objects.get(session_key=request.session.session_key)
                request.session["book_ids"] = anon_session.books_data or []
                request.session["top_book_ids"] = anon_session.top_books_data or []
                # Use getattr for backwards compatibility if migration hasn't run yet
                request.session["book_ratings"] = getattr(anon_session, "book_ratings", None) or {}
            except AnonymousUserSession.DoesNotExist:
                pass
        request.session.save()

        return JsonResponse(
            {
                "status": "SUCCESS",
                "redirect_url": reverse("core:display_dna"),
            }
        )

    result = AsyncResult(task_id)

    if result.state == "SUCCESS":
        dna_data = result.get()

        request.session["dna_data"] = dna_data
        # Also store book IDs and ratings from AnonymousUserSession if it exists
        if request.session.session_key:
            try:
                anon_session = AnonymousUserSession.objects.get(session_key=request.session.session_key)
                request.session["book_ids"] = anon_session.books_data or []
                request.session["top_book_ids"] = anon_session.top_books_data or []
                # Use getattr for backwards compatibility if migration hasn't run yet
                request.session["book_ratings"] = getattr(anon_session, "book_ratings", None) or {}
            except AnonymousUserSession.DoesNotExist:
                pass
        request.session.save()

        return JsonResponse(
            {
                "status": "SUCCESS",
                "redirect_url": reverse("core:display_dna"),
            }
        )
    elif result.state == "PROGRESS":
        info = result.info or {}
        current = info.get("current")
        total = info.get("total")
        stage = info.get("stage", "")
        percent = None
        if isinstance(current, int) and isinstance(total, int) and total > 0:
            percent = round((current * 100) / total)
        progress = {"current": current, "total": total, "percent": percent, "stage": stage}
        return JsonResponse({"status": "PENDING", "progress": progress})
    elif result.state == "FAILURE":
        return JsonResponse({"status": "FAILURE", "error": "An error occurred during processing."})
    else:
        return JsonResponse({"status": "PENDING"})

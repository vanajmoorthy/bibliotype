"""Backfill genres for books that have none, using the free-tier LLM chain.

Enrichment has already run on these books: Google Books gives only "Fiction"
(dropped as too generic) or nothing, and Open Library gives no usable subjects.
Many are still well known, or translated editions of well-known books, so an
LLM can classify them from title and author. Obscure books must come back as
"unknown" rather than guessed.

Default is a dry run. `--apply` writes genres, only to books that still have
none at write time, and sets `Book.genres_from_llm` so they can be audited or
undone. Run `regenerate_dna` afterwards so profiles pick the new genres up.
"""

import json
import logging
import time

from celery.exceptions import SoftTimeLimitExceeded
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.dna_constants import EXCLUDED_GENRES, GENRE_PRIORITY
from core.models import Book, Genre
from core.services import _llm

logger = logging.getLogger(__name__)

BATCH_SIZE = 25
MAX_GENRES_PER_BOOK = 3
SAMPLE_SIZE = 30
UNKNOWN_SAMPLE_SIZE = 10
# Pause between batches to stay inside free-tier requests-per-minute limits.
PAUSE_SECONDS = 2
UNKNOWN = "unknown"

# The canonical genres the model may choose from, in priority order (most specific first).
CANONICAL_GENRES = [g for g in GENRE_PRIORITY if g not in EXCLUDED_GENRES]
_CANONICAL_SET = frozenset(CANONICAL_GENRES)


def build_prompt(books):
    """Prompt asking for 1-3 canonical genres per book, or "unknown"."""
    lines = "\n".join(
        json.dumps(
            {"id": b.id, "title": b.title, "author": b.author.name, "publish_year": b.publish_year},
            ensure_ascii=False,
        )
        for b in books
    )
    return f"""You classify books into genres from a fixed list.

Genres: {", ".join(CANONICAL_GENRES)}

For each book below, give 1 to {MAX_GENRES_PER_BOOK} genres from the list, most specific first. Some titles are translated editions (for example a Turkish or Spanish title of an English novel): classify the original work.

Only classify a book you actually recognise. If you do not recognise this specific book, answer "{UNKNOWN}". Never guess from words in the title, and never use a genre that is not in the list.

Books (one JSON object per line):
{lines}

Respond with only a JSON object of the form:
{{"results": [{{"id": 123, "genres": ["fantasy", "adventure"]}}, {{"id": 456, "genres": "{UNKNOWN}"}}]}}
Include every id exactly once."""


def _coerce_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def parse_response(data, batch_ids):
    """Turn raw model output into {book_id: answer}, never trusting its shape.

    An answer is UNKNOWN, or a (valid_genres, invalid_count) tuple. Ids not in
    the batch and malformed entries are ignored; books the model skipped are
    simply absent from the result. Besides the requested {"results": [...]},
    a bare list of entries or an {"<id>": genres} mapping is accepted, since
    weaker free models drift into those shapes.
    """
    if isinstance(data, dict) and "results" in data:
        entries = data["results"]
    elif isinstance(data, dict):
        entries = [{"id": key, "genres": value} for key, value in data.items()]
    else:
        entries = data
    if not isinstance(entries, list):
        return {}

    answers = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        book_id = _coerce_id(entry.get("id"))
        if book_id not in batch_ids or book_id in answers:
            continue

        raw = entry.get("genres")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue

        names = [g.strip().lower() for g in raw if isinstance(g, str) and g.strip()]
        if all(name == UNKNOWN for name in names):
            answers[book_id] = UNKNOWN
            continue

        valid, invalid = [], 0
        for name in names:
            if name in _CANONICAL_SET:
                if name not in valid:
                    valid.append(name)
            elif name != UNKNOWN:
                invalid += 1
        valid.sort(key=GENRE_PRIORITY.index)
        answers[book_id] = (valid[:MAX_GENRES_PER_BOOK], invalid)
    return answers


class Command(BaseCommand):
    help = "Suggest genres for books with none via the LLM chain. Dry run by default; --apply writes them."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write genres (default is a dry run).")
        parser.add_argument("--limit", type=int, help="Max books to send (default all).")
        parser.add_argument(
            "--after-id",
            type=int,
            help="Only books with an id above this, to continue from the last id a previous run printed.",
        )

    def _log(self, msg):
        self.stdout.write(msg)
        logger.info(f"llm_backfill_genres: {msg}")

    def _warn(self, msg):
        self.stdout.write(self.style.WARNING(msg))
        logger.warning(f"llm_backfill_genres: {msg}")

    def handle(self, *args, **options):
        apply = options["apply"]
        limit = options.get("limit")
        after_id = options.get("after_id")

        if not _llm.is_configured():
            raise CommandError("No LLM provider is configured (set GEMINI_API_KEY or GROQ_API_KEY).")

        qs = Book.objects.filter(genres__isnull=True).select_related("author").order_by("id")
        if after_id:
            qs = qs.filter(id__gt=after_id)
        books = list(qs[:limit] if limit else qs)

        mode = "APPLY" if apply else "DRY RUN"
        self._log(f"[{mode}] {len(books)} books with no genres to send in batches of {BATCH_SIZE}.")
        if not books:
            return

        stats = {
            "sent": 0,
            "classified": 0,
            "unknown": 0,
            "no_answer": 0,
            "invalid_dropped": 0,
            "only_invalid": 0,
            "failed_batches": 0,
            "written": 0,
            "skipped_has_genres": 0,
        }
        samples, unknown_samples = [], []
        last_id = None
        stopped_early = False
        total_batches = (len(books) + BATCH_SIZE - 1) // BATCH_SIZE

        for index in range(total_batches):
            batch = books[index * BATCH_SIZE : (index + 1) * BATCH_SIZE]
            if index:
                time.sleep(PAUSE_SECONDS)
            try:
                self._run_batch(batch, index + 1, total_batches, apply, stats, samples, unknown_samples)
            except SoftTimeLimitExceeded:
                # Raised inside the Celery worker by the admin Command Runner's time limit:
                # stop cleanly so the summary (and --after-id to resume from) still prints.
                self._warn(f"Time limit reached during batch {index + 1}; stopping early.")
                stopped_early = True
                break
            last_id = batch[-1].id

        self._print_summary(mode, stats, samples, unknown_samples, last_id, stopped_early, apply)

    def _run_batch(self, batch, number, total, apply, stats, samples, unknown_samples):
        batch_ids = {b.id for b in batch}
        stats["sent"] += len(batch)
        try:
            data = _llm.generate_json(build_prompt(batch), temperature=0, max_tokens=2048)
        except SoftTimeLimitExceeded:
            raise
        except Exception as e:  # generate_json shouldn't raise, but one bad batch must never end the run
            logger.error(f"llm_backfill_genres: batch {number} raised: {e}", exc_info=True)
            data = None
        if data is None:
            stats["failed_batches"] += 1
            self._warn(f"Batch {number}/{total} failed (LLM chain returned nothing usable); continuing.")
            return

        answers = parse_response(data, batch_ids)
        for book in batch:
            answer = answers.get(book.id)
            if answer is None:
                stats["no_answer"] += 1
                continue
            if answer == UNKNOWN:
                stats["unknown"] += 1
                if len(unknown_samples) < UNKNOWN_SAMPLE_SIZE:
                    unknown_samples.append(book)
                continue

            genres, invalid = answer
            stats["invalid_dropped"] += invalid
            if not genres:
                stats["only_invalid"] += 1
                continue
            stats["classified"] += 1
            if len(samples) < SAMPLE_SIZE:
                samples.append((book, genres))
            if apply:
                if self._write_genres(book, genres):
                    stats["written"] += 1
                else:
                    stats["skipped_has_genres"] += 1

        self._log(f"Batch {number}/{total}: {len(answers)} of {len(batch)} answered.")

    @staticmethod
    def _write_genres(book, genres):
        """Set genres only if the book still has none. Returns whether it wrote."""
        with transaction.atomic():
            locked = Book.objects.select_for_update().get(pk=book.pk)
            if locked.genres.exists():
                return False
            locked.genres.add(*[Genre.objects.get_or_create(name=g)[0] for g in genres])
            Book.objects.filter(pk=book.pk).update(genres_from_llm=True)
        return True

    def _print_summary(self, mode, stats, samples, unknown_samples, last_id, stopped_early, apply):
        self._log("")
        self._log(f"=== Summary ({mode}) ===")
        self._log(f"Books sent:               {stats['sent']}")
        self._log(f"Classified:               {stats['classified']}")
        self._log(f"Unknown:                  {stats['unknown']}")
        self._log(f"No answer from model:     {stats['no_answer']}")
        self._log(f"Only invalid genres:      {stats['only_invalid']}")
        self._log(f"Invalid genres dropped:   {stats['invalid_dropped']}")
        self._log(f"Failed batches:           {stats['failed_batches']}")
        if apply:
            self._log(f"Written:                  {stats['written']}")
            self._log(f"Skipped (gained genres):  {stats['skipped_has_genres']}")
        if last_id is not None:
            self._log(f"Last book id processed:   {last_id} (pass --after-id {last_id} to continue)")
        if stopped_early:
            self._warn("Run stopped early; not every selected book was sent.")

        if samples:
            self._log("")
            self._log(f"--- Sample suggestions ({len(samples)}) ---")
            for book, genres in samples:
                self._log(f"#{book.id} {self._describe(book)} -> {', '.join(genres)}")
        if unknown_samples:
            self._log("")
            self._log(f"--- Sample unknowns ({len(unknown_samples)}) ---")
            for book in unknown_samples:
                self._log(f"#{book.id} {self._describe(book)}")
        if not apply:
            self._log("")
            self._log("Dry run: nothing was written. Re-run with --apply to save these genres.")

    @staticmethod
    def _describe(book):
        year = f" ({book.publish_year})" if book.publish_year else ""
        return f'"{book.title}" by {book.author.name}{year}'

"""Reading vibe: reuse the cached LLM vibe when the library is unchanged, otherwise generate one."""

import logging
import random

from core.services.llm_service import VIBE_COLORS, generate_vibe_with_llm

logger = logging.getLogger(__name__)


def attach_reading_vibe(dna, user, new_data_hash):
    """Set reading_vibe, vibe_data_hash and reading_vibe_color on dna in place."""
    from core.analytics.events import track_vibe_requested

    reading_vibe = []
    vibe_color = None
    if user:
        profile = user.userprofile
        if profile.vibe_data_hash == new_data_hash and profile.reading_vibe:
            logger.debug("Vibe data is unchanged. Using cached vibe from database")
            track_vibe_requested(cache_hit=True, user_id=user.id)
            reading_vibe = profile.reading_vibe
            vibe_color = (profile.dna_data or {}).get("reading_vibe_color")
        else:
            logger.info("Vibe data has changed. Generating a new vibe with LLM...")
            track_vibe_requested(cache_hit=False, user_id=user.id)
            reading_vibe = generate_vibe_with_llm(dna)
    else:
        logger.info("Anonymous user. Generating a new vibe with LLM...")
        track_vibe_requested(cache_hit=False, is_anonymous=True)
        reading_vibe = generate_vibe_with_llm(dna)

    # A failed LLM call returns None. Persist an empty vibe but leave the
    # hash unset, so the next DNA run regenerates instead of caching the
    # failure forever (the cached-vibe branch above requires a hash match).
    dna["reading_vibe"] = reading_vibe or []
    dna["vibe_data_hash"] = new_data_hash if reading_vibe else None
    # The banner colour is minted once, alongside the vibe, and rides in
    # dna_data so every later page load shows the same highlight.
    dna["reading_vibe_color"] = vibe_color or random.choice(VIBE_COLORS)

"""
Human-looking usernames for seeded users.

Seed users surface in the UI ("Your closest match is @..."), so they need
handles that read like real readers rather than `seed_012__someone_repo`.
Names are derived deterministically from a stable key (the seed email), so
re-running the seeder or the rename migration gives the same handle.

Handles follow the signup form's rules: lowercase, at most 15 characters.
"""

import hashlib

MAX_USERNAME_LENGTH = 15

# fmt: off
FIRST_NAMES = [
    "Maya", "Leo", "Ava", "Noah", "Zara", "Theo", "Isla", "Omar", "Nina", "Felix",
    "Priya", "Jonah", "Elena", "Kai", "Ruth", "Sam", "Amara", "Hugo", "Clara", "Ravi",
    "Lucy", "Ezra", "Mei", "Owen", "Sofia", "Arjun", "Hana", "Milo", "Ines", "Caleb",
    "Freya", "Tariq", "June", "Mateo", "Iris", "Kofi", "Rosa", "Eli", "Anya", "Jude",
    "Leila", "Finn", "Nora", "Dev", "Esme", "Yusuf", "Tess", "Ben", "Aisha", "Rory",
    "Lena", "Diego", "Mira", "Cole", "Yuki", "Max", "Ada", "Idris", "Bea", "Luca",
]

LAST_NAMES = [
    "Okafor", "Bennett", "Patel", "Nguyen", "Garcia", "Kim", "Murphy", "Silva", "Cohen", "Haddad",
    "Larsen", "Mensah", "Rossi", "Tanaka", "Walsh", "Novak", "Reyes", "Shah", "Brooks", "Ito",
    "Moreau", "Adeyemi", "Fischer", "Lopez", "Quinn", "Sato", "Ward", "Khan", "Hughes", "Costa",
    "Price", "Ahmed", "Lindqvist", "Park", "Doyle", "Mendes", "Clarke", "Osei", "Marsh", "Varga",
    "Hale", "Iyer", "Byrne", "Castro", "Ellis", "Nakamura", "Reid", "Bauer", "Chen", "Foster",
]
# fmt: on


def _candidates(key):
    """Yield handles for `key` in a stable, key-specific order."""
    digest = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
    n_pairs = len(FIRST_NAMES) * len(LAST_NAMES)
    start = digest % n_pairs
    for offset in range(n_pairs):
        pair = (start + offset) % n_pairs
        first = FIRST_NAMES[pair % len(FIRST_NAMES)].lower()
        last = LAST_NAMES[pair // len(FIRST_NAMES)].lower()
        # Mix a few handle styles so the pool doesn't look machine-made.
        style = (digest >> 8) % 3
        if style == 0:
            handle = f"{first}{last}"
        elif style == 1:
            handle = f"{first}_{last}"
        else:
            handle = f"{first}{last[0]}"
        if len(handle) > MAX_USERNAME_LENGTH:
            handle = f"{first}_{last[0]}"
        yield first.capitalize(), handle


def seed_identity(key, is_taken):
    """
    Return `(first_name, username)` for the seed user identified by `key`.

    `is_taken(username)` must report whether a username is already in use
    (case-insensitively); the first free handle in the key's sequence wins.
    """
    for first_name, handle in _candidates(key):
        if not is_taken(handle):
            return first_name, handle
    raise RuntimeError("Seed name pool exhausted")

from django.db import migrations

# Seeded users used to be named after their source CSV
# (`seed_012__someone_repo`), which leaked into the UI as
# "Your closest match is @seed_012__...". Give them human-looking handles and
# patch the usernames already baked into stored recommendations so links to
# their public profiles keep working.
SEED_EMAIL_DOMAIN = "seed.bibliotype.invalid"
OLD_USERNAME_PREFIX = "seed_"


def rename_seed_users(apps, schema_editor):
    from core.seed_names import seed_identity

    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("core", "UserProfile")

    renamed = {}  # user_id -> (old_username, new_username)
    seed_users = User.objects.filter(
        email__endswith=f"@{SEED_EMAIL_DOMAIN}", username__startswith=OLD_USERNAME_PREFIX
    ).order_by("email")
    for user in seed_users:
        first_name, handle = seed_identity(
            user.email, lambda name: User.objects.filter(username__iexact=name).exists()
        )
        renamed[user.id] = (user.username, handle)
        user.username = handle
        user.first_name = first_name
        user.save(update_fields=["username", "first_name"])

    if not renamed:
        return

    old_to_new = dict(renamed.values())
    profiles = UserProfile.objects.exclude(recommendations_data__isnull=True).only(
        "id", "recommendations_data", "recommendations_meta"
    )
    for profile in profiles.iterator():
        changed = False
        for rec in profile.recommendations_data or []:
            sources = list(rec.get("sources") or [])
            if rec.get("primary_source_user"):
                sources.append(rec["primary_source_user"])
            for source in sources:
                if source.get("user_id") in renamed:
                    source["username"] = renamed[source["user_id"]][1]
                    changed = True
        meta = profile.recommendations_meta or {}
        if meta.get("closest_match_username") in old_to_new:
            meta["closest_match_username"] = old_to_new[meta["closest_match_username"]]
            changed = True
        if changed:
            profile.save(update_fields=["recommendations_data", "recommendations_meta"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_userprofile_is_public_default_true"),
    ]

    operations = [
        migrations.RunPython(rename_seed_users, migrations.RunPython.noop),
    ]

from django.db import migrations

# Waffle switch gating the "Pretty unique" / "One of a kind" reader badge.
# Created inactive; toggle at /admin/waffle/switch/ once the user pool is
# large enough for uniqueness to be meaningful.
SWITCH_NAME = "uniqueness-badge"


def create_switch(apps, schema_editor):
    Switch = apps.get_model("waffle", "Switch")
    Switch.objects.get_or_create(
        name=SWITCH_NAME,
        defaults={"active": False, "note": "Show the reader-uniqueness badge in the recommendations grid."},
    )


def remove_switch(apps, schema_editor):
    Switch = apps.get_model("waffle", "Switch")
    Switch.objects.filter(name=SWITCH_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_userprofile_userprofile_recs_partial_idx"),
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_switch, remove_switch),
    ]

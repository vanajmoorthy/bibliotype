"""Drop stored recommendations saved before US-032 baked the nested `book` dict.

The views used to rebuild `rec["book"]` from the flat `book_*` keys at render time.
That fallback is gone, so clear any list still holding a flat rec: the dashboard
treats `recommendations_data=None` as "not generated yet" and regenerates it.
"""

from django.db import migrations


def clear_legacy_shape_recommendations(apps, schema_editor):
    UserProfile = apps.get_model("core", "UserProfile")
    stale_ids = [
        pk
        for pk, recs in UserProfile.objects.filter(recommendations_data__isnull=False)
        .values_list("pk", "recommendations_data")
        .iterator()
        if isinstance(recs, list) and any(isinstance(rec, dict) and "book" not in rec for rec in recs)
    ]
    UserProfile.objects.filter(pk__in=stale_ids).update(recommendations_data=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_unmappedsubject_suggestions"),
    ]

    operations = [
        migrations.RunPython(clear_legacy_shape_recommendations, migrations.RunPython.noop),
    ]

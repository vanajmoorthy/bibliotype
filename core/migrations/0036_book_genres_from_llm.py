from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_clear_legacy_shape_recommendations"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="genres_from_llm",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]

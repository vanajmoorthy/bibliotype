from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_unmappedsubject_suggestions"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="genres_from_llm",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]

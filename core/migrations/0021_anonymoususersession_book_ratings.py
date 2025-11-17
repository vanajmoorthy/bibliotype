# Generated manually to add book_ratings field
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_userprofile_visible_in_recommendations_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='anonymoususersession',
            name='book_ratings',
            field=models.JSONField(default=dict, help_text='{"book_id": rating} for rating correlation'),
        ),
    ]


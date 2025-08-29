import random

from django.core.management.base import BaseCommand

from core.models import AggregateAnalytics  # Make sure to import your model from the correct app


class Command(BaseCommand):
    help = "Seeds the AggregateAnalytics model with realistic fake data for testing."

    def handle(self, *args, **kwargs):
        self.stdout.write("Deleting old aggregate analytics data...")
        AggregateAnalytics.objects.all().delete()

        self.stdout.write("Seeding new aggregate analytics data for 500 fake users...")

        analytics = AggregateAnalytics.get_instance()

        total_profiles = 500

        # --- Generate realistic distributions ---

        # Total Books Read (Distribution centered around 50-150)
        books_dist = {}
        for _ in range(total_profiles):
            # Use random.choices to create a weighted distribution
            num_books = random.choices(
                population=[random.randint(10, 49), random.randint(50, 149), random.randint(150, 300)],
                weights=[0.3, 0.6, 0.1],
                k=1,
            )[0]
            bucket_size = 25
            lower_bound = int(num_books // bucket_size * bucket_size)
            bucket = f"{lower_bound}-{lower_bound + bucket_size - 1}"
            books_dist[bucket] = books_dist.get(bucket, 0) + 1

        # Average Book Length (Distribution centered around 250-400 pages)
        length_dist = {}
        for _ in range(total_profiles):
            avg_length = random.choices(
                population=[random.randint(150, 249), random.randint(250, 399), random.randint(400, 600)],
                weights=[0.25, 0.6, 0.15],
                k=1,
            )[0]
            bucket_size = 50
            lower_bound = int(avg_length // bucket_size * bucket_size)
            bucket = f"{lower_bound}-{lower_bound + bucket_size - 1}"
            length_dist[bucket] = length_dist.get(bucket, 0) + 1

        # Average Publish Year (Distribution centered around 1990-2015)
        year_dist = {}
        for _ in range(total_profiles):
            avg_year = random.choices(
                population=[random.randint(1970, 1989), random.randint(1990, 2015), random.randint(2016, 2023)],
                weights=[0.2, 0.65, 0.15],
                k=1,
            )[0]
            bucket_size = 10
            lower_bound = int(avg_year // bucket_size * bucket_size)
            bucket = f"{lower_bound}-{lower_bound + bucket_size - 1}"
            year_dist[bucket] = year_dist.get(bucket, 0) + 1

        # --- Update and save the analytics instance ---
        analytics.total_profiles_counted = total_profiles
        analytics.total_books_read_dist = books_dist
        analytics.avg_book_length_dist = length_dist
        analytics.avg_publish_year_dist = year_dist

        analytics.save()

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded analytics with {total_profiles} profiles."))

"""
Upload CSVs for test users and generate their DNA
"""
import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.tasks import generate_reading_dna_task


class Command(BaseCommand):
    help = 'Upload CSVs for test users and generate their DNA'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start-server',
            action='store_true',
            help='Start development server after uploading'
        )

    def handle(self, *args, **options):
        # Find test users
        test_users = User.objects.filter(username__startswith='test_reader')
        
        self.stdout.write(f"Found {test_users.count()} test users")
        
        for user in test_users:
            csv_file = f'csv/goodreads_library_export {user.username}.csv'
            
            if not os.path.exists(csv_file):
                self.stdout.write(self.style.WARNING(f"CSV not found for {user.username}: {csv_file}"))
                continue
            
            self.stdout.write(f"Uploading CSV for {user.username}...")
            
            # Read CSV content
            with open(csv_file, 'r', encoding='utf-8') as f:
                csv_content = f.read()
            
            # Generate DNA
            try:
                result = generate_reading_dna_task(csv_content, user.id, None)
                self.stdout.write(self.style.SUCCESS(f"DNA generated for {user.username}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error generating DNA for {user.username}: {e}"))
        
        self.stdout.write(self.style.SUCCESS("\nUpload complete!"))
        self.stdout.write("\nTest users ready with DNA:")
        for user in test_users:
            self.stdout.write(f"  - {user.username} (password: testpass123)")


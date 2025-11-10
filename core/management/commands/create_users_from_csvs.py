"""
Create users from all CSV files in the csv/ directory and upload their data
This command scans for all CSV files matching the pattern and creates users accordingly.
"""
import os
import glob
import re
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.tasks import generate_reading_dna_task


class Command(BaseCommand):
    help = 'Create users from all CSV files in csv/ directory and generate their DNA'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv-dir',
            type=str,
            default='csv',
            help='Directory containing CSV files (default: csv)'
        )
        parser.add_argument(
            '--password',
            type=str,
            default='testpass123',
            help='Password for created users (default: testpass123)'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip users that already exist'
        )
        parser.add_argument(
            '--pattern',
            type=str,
            default='goodreads_library_export *.csv',
            help='Glob pattern for CSV files (default: "goodreads_library_export *.csv")'
        )

    def extract_username_from_filename(self, filename):
        """
        Extract username from CSV filename.
        Examples:
        - 'goodreads_library_export test_reader1.csv' -> 'test_reader1'
        - 'goodreads_library_export synthetic_contemporary1.csv' -> 'synthetic_contemporary1'
        - 'goodreads_library_export anya.csv' -> 'anya'
        """
        # Remove directory path and extension
        basename = os.path.basename(filename)
        name_without_ext = os.path.splitext(basename)[0]
        
        # Remove the 'goodreads_library_export ' prefix
        prefix = 'goodreads_library_export '
        if name_without_ext.startswith(prefix):
            username = name_without_ext[len(prefix):].strip()
        else:
            # If pattern doesn't match, try to extract from filename
            username = name_without_ext.replace('goodreads_library_export', '').strip()
        
        # Clean up username (remove any invalid characters)
        # Django usernames can contain: letters, digits, @, +, ., -, _
        username = re.sub(r'[^\w@+.-]', '_', username)
        
        return username

    def handle(self, *args, **options):
        csv_dir = options['csv_dir']
        password = options['password']
        skip_existing = options['skip_existing']
        pattern = options['pattern']
        
        # Build full path pattern
        csv_pattern = os.path.join(csv_dir, pattern)
        
        self.stdout.write(f"Scanning for CSV files matching: {csv_pattern}")
        
        # Find all matching CSV files
        csv_files = glob.glob(csv_pattern)
        
        if not csv_files:
            self.stdout.write(self.style.WARNING(f"No CSV files found matching pattern: {csv_pattern}"))
            return
        
        self.stdout.write(f"Found {len(csv_files)} CSV file(s)")
        
        created_count = 0
        skipped_count = 0
        uploaded_count = 0
        error_count = 0
        
        for csv_file in sorted(csv_files):
            username = self.extract_username_from_filename(csv_file)
            
            if not username:
                self.stdout.write(self.style.WARNING(f"Could not extract username from: {csv_file}"))
                continue
            
            self.stdout.write(f"\nProcessing: {csv_file}")
            self.stdout.write(f"  Username: {username}")
            
            # Check if user exists
            user_exists = User.objects.filter(username=username).exists()
            
            if user_exists:
                if skip_existing:
                    self.stdout.write(self.style.WARNING(f"  User {username} already exists, skipping..."))
                    skipped_count += 1
                    continue
                else:
                    user = User.objects.get(username=username)
                    self.stdout.write(f"  User {username} already exists, will upload CSV...")
            else:
                # Create user
                email = f"{username}@test.bibliotype.com"
                try:
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=password,
                        first_name=username.replace('_', ' ').title()
                    )
                    self.stdout.write(self.style.SUCCESS(f"  Created user: {username}"))
                    created_count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  Error creating user {username}: {e}"))
                    error_count += 1
                    continue
            
            # Upload CSV and generate DNA
            if not os.path.exists(csv_file):
                self.stdout.write(self.style.WARNING(f"  CSV file not found: {csv_file}"))
                error_count += 1
                continue
            
            try:
                self.stdout.write(f"  Reading CSV file...")
                with open(csv_file, 'r', encoding='utf-8') as f:
                    csv_content = f.read()
                
                self.stdout.write(f"  Generating DNA (this may take a while)...")
                # Call the task directly (works when CELERY_TASK_ALWAYS_EAGER=True or task is callable)
                result = generate_reading_dna_task(csv_content, user.id, None)
                
                self.stdout.write(self.style.SUCCESS(f"  ✓ DNA generated for {username}"))
                uploaded_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  Error generating DNA for {username}: {e}"))
                error_count += 1
                import traceback
                self.stdout.write(traceback.format_exc())
        
        # Summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("Summary:"))
        self.stdout.write(f"  CSV files processed: {len(csv_files)}")
        self.stdout.write(f"  Users created: {created_count}")
        self.stdout.write(f"  Users skipped: {skipped_count}")
        self.stdout.write(f"  DNA uploads successful: {uploaded_count}")
        self.stdout.write(f"  Errors: {error_count}")
        self.stdout.write(f"\nAll users have password: {password}")
        self.stdout.write("=" * 60)


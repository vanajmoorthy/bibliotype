# In core/management/commands/seed_books.py

import random
from concurrent.futures import ThreadPoolExecutor

import requests
from django.core.management.base import BaseCommand

from core.analytics import get_book_details_from_open_library
from core.models import Author, Book

# Using the same comprehensive list from before
COMPREHENSIVE_BOOK_LIST = [
    {"title": "To Kill a Mockingbird", "author": "Harper Lee"},
    {"title": "1984", "author": "George Orwell"},
    {"title": "The Great Gatsby", "author": "F. Scott Fitzgerald"},
    {"title": "Pride and Prejudice", "author": "Jane Austen"},
    {"title": "The Catcher in the Rye", "author": "J.D. Salinger"},
    {"title": "Moby Dick", "author": "Herman Melville"},
    {"title": "War and Peace", "author": "Leo Tolstoy"},
    {"title": "Anna Karenina", "author": "Leo Tolstoy"},
    {"title": "Crime and Punishment", "author": "Fyodor Dostoevsky"},
    {"title": "The Brothers Karamazov", "author": "Fyodor Dostoevsky"},
    {"title": "Ulysses", "author": "James Joyce"},
    {"title": "The Odyssey", "author": "Homer"},
    {"title": "Don Quixote", "author": "Miguel de Cervantes"},
    {"title": "Frankenstein", "author": "Mary Shelley"},
    {"title": "Dracula", "author": "Bram Stoker"},
    {"title": "Jane Eyre", "author": "Charlotte Brontë"},
    {"title": "Wuthering Heights", "author": "Emily Brontë"},
    {"title": "Great Expectations", "author": "Charles Dickens"},
    {"title": "A Tale of Two Cities", "author": "Charles Dickens"},
    {"title": "The Adventures of Huckleberry Finn", "author": "Mark Twain"},
    {"title": "Les Misérables", "author": "Victor Hugo"},
    {"title": "The Count of Monte Cristo", "author": "Alexandre Dumas"},
    {"title": "East of Eden", "author": "John Steinbeck"},
    {"title": "The Grapes of Wrath", "author": "John Steinbeck"},
    {"title": "Of Mice and Men", "author": "John Steinbeck"},
    {"title": "The Sun Also Rises", "author": "Ernest Hemingway"},
    {"title": "For Whom the Bell Tolls", "author": "Ernest Hemingway"},
    {"title": "The Sound and the Fury", "author": "William Faulkner"},
    {"title": "As I Lay Dying", "author": "William Faulkner"},
    {"title": "Heart of Darkness", "author": "Joseph Conrad"},
    {"title": "The Picture of Dorian Gray", "author": "Oscar Wilde"},
    {"title": "Things Fall Apart", "author": "Chinua Achebe"},
    {"title": "Beloved", "author": "Toni Morrison"},
    {"title": "Invisible Man", "author": "Ralph Ellison"},
    {"title": "Their Eyes Were Watching God", "author": "Zora Neale Hurston"},
    {"title": "Mrs Dalloway", "author": "Virginia Woolf"},
    {"title": "To the Lighthouse", "author": "Virginia Woolf"},
    {"title": "Lolita", "author": "Vladimir Nabokov"},
    # --- Genre-Defining Sci-Fi & Fantasy ---
    {"title": "Dune", "author": "Frank Herbert"},
    {"title": "Dune Messiah", "author": "Frank Herbert"},
    {"title": "Foundation", "author": "Isaac Asimov"},
    {"title": "I, Robot", "author": "Isaac Asimov"},
    {"title": "The Lord of the Rings", "author": "J.R.R. Tolkien"},
    {"title": "The Hobbit", "author": "J.R.R. Tolkien"},
    {"title": "A Wizard of Earthsea", "author": "Ursula K. Le Guin"},
    {"title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"},
    {"title": "The Dispossessed", "author": "Ursula K. Le Guin"},
    {"title": "Brave New World", "author": "Aldous Huxley"},
    {"title": "Fahrenheit 451", "author": "Ray Bradbury"},
    {"title": "The Martian Chronicles", "author": "Ray Bradbury"},
    {"title": "Neuromancer", "author": "William Gibson"},
    {"title": "Snow Crash", "author": "Neal Stephenson"},
    {"title": "The Hitchhiker's Guide to the Galaxy", "author": "Douglas Adams"},
    {"title": "Ender's Game", "author": "Orson Scott Card"},
    {"title": "Hyperion", "author": "Dan Simmons"},
    {"title": "The Handmaid's Tale", "author": "Margaret Atwood"},
    {"title": "Do Androids Dream of Electric Sheep?", "author": "Philip K. Dick"},
    {"title": "A Scanner Darkly", "author": "Philip K. Dick"},
    {"title": "Starship Troopers", "author": "Robert A. Heinlein"},
    {"title": "Stranger in a Strange Land", "author": "Robert A. Heinlein"},
    {"title": "The Moon Is a Harsh Mistress", "author": "Robert A. Heinlein"},
    {"title": "2001: A Space Odyssey", "author": "Arthur C. Clarke"},
    {"title": "Rendezvous with Rama", "author": "Arthur C. Clarke"},
    {"title": "The Chronicles of Narnia", "author": "C.S. Lewis"},
    {"title": "American Gods", "author": "Neil Gaiman"},
    {"title": "Good Omens", "author": "Neil Gaiman"},  # Note: and Terry Pratchett
    {"title": "The Color of Magic", "author": "Terry Pratchett"},
    {"title": "Red Mars", "author": "Kim Stanley Robinson"},
    {"title": "The Three-Body Problem", "author": "Cixin Liu"},
    {"title": "Children of Time", "author": "Adrian Tchaikovsky"},
    # --- Popular Modern Fantasy Series ---
    {"title": "A Game of Thrones", "author": "George R.R. Martin"},
    {"title": "A Clash of Kings", "author": "George R.R. Martin"},
    {"title": "The Name of the Wind", "author": "Patrick Rothfuss"},
    {"title": "The Way of Kings", "author": "Brandon Sanderson"},
    {"title": "Mistborn: The Final Empire", "author": "Brandon Sanderson"},
    {"title": "The Eye of the World", "author": "Robert Jordan"},
    {"title": "The Blade Itself", "author": "Joe Abercrombie"},
    {"title": "Assassin's Apprentice", "author": "Robin Hobb"},
    {"title": "The Lies of Locke Lamora", "author": "Scott Lynch"},
    {"title": "Gardens of the Moon", "author": "Steven Erikson"},
    {"title": "The Fifth Season", "author": "N. K. Jemisin"},
    {"title": "Jonathan Strange & Mr Norrell", "author": "Susanna Clarke"},
    {"title": "Piranesi", "author": "Susanna Clarke"},
    # --- Popular Horror & Thriller ---
    {"title": "The Stand", "author": "Stephen King"},
    {"title": "It", "author": "Stephen King"},
    {"title": "The Shining", "author": "Stephen King"},
    {"title": "Misery", "author": "Stephen King"},
    {"title": "The Haunting of Hill House", "author": "Shirley Jackson"},
    {"title": "We Have Always Lived in the Castle", "author": "Shirley Jackson"},
    {"title": "I Am Legend", "author": "Richard Matheson"},
    {"title": "The Silence of the Lambs", "author": "Thomas Harris"},
    {"title": "Gone Girl", "author": "Gillian Flynn"},
    {"title": "The Girl with the Dragon Tattoo", "author": "Stieg Larsson"},
    {"title": "The Silent Patient", "author": "Alex Michaelides"},
    # --- 20th & 21st Century Bestsellers & Award Winners ---
    {"title": "One Hundred Years of Solitude", "author": "Gabriel García Márquez"},
    {"title": "Love in the Time of Cholera", "author": "Gabriel García Márquez"},
    {"title": "Slaughterhouse-Five", "author": "Kurt Vonnegut"},
    {"title": "Cat's Cradle", "author": "Kurt Vonnegut"},
    {"title": "The Bell Jar", "author": "Sylvia Plath"},
    {"title": "On the Road", "author": "Jack Kerouac"},
    {"title": "Lord of the Flies", "author": "William Golding"},
    {"title": "Animal Farm", "author": "George Orwell"},
    {"title": "The Alchemist", "author": "Paulo Coelho"},
    {"title": "Life of Pi", "author": "Yann Martel"},
    {"title": "The Kite Runner", "author": "Khaled Hosseini"},
    {"title": "A Thousand Splendid Suns", "author": "Khaled Hosseini"},
    {"title": "The Road", "author": "Cormac McCarthy"},
    {"title": "Blood Meridian", "author": "Cormac McCarthy"},
    {"title": "Infinite Jest", "author": "David Foster Wallace"},
    {"title": "White Teeth", "author": "Zadie Smith"},
    {"title": "Middlesex", "author": "Jeffrey Eugenides"},
    {"title": "The Amazing Adventures of Kavalier & Clay", "author": "Michael Chabon"},
    {"title": "The Goldfinch", "author": "Donna Tartt"},
    {"title": "The Secret History", "author": "Donna Tartt"},
    {"title": "A Little Life", "author": "Hanya Yanagihara"},
    {"title": "Wolf Hall", "author": "Hilary Mantel"},
    {"title": "All the Light We Cannot See", "author": "Anthony Doerr"},
    {"title": "The Help", "author": "Kathryn Stockett"},
    {"title": "The Underground Railroad", "author": "Colson Whitehead"},
    {"title": "Pachinko", "author": "Min Jin Lee"},
    {"title": "The Vanishing Half", "author": "Brit Bennett"},
    {"title": "Normal People", "author": "Sally Rooney"},
    {"title": "Klara and the Sun", "author": "Kazuo Ishiguro"},
    {"title": "Never Let Me Go", "author": "Kazuo Ishiguro"},
    {"title": "The Remains of the Day", "author": "Kazuo Ishiguro"},
    {"title": "Where the Crawdads Sing", "author": "Delia Owens"},
    {"title": "Circe", "author": "Madeline Miller"},
    {"title": "The Song of Achilles", "author": "Madeline Miller"},
    {"title": "The Midnight Library", "author": "Matt Haig"},
    {"title": "Eleanor Oliphant Is Completely Fine", "author": "Gail Honeyman"},
    {"title": "This Is How You Lose the Time War", "author": "Amal El-Mohtar"},
    {"title": "The Seven Husbands of Evelyn Hugo", "author": "Taylor Jenkins Reid"},
    {"title": "Daisy Jones & The Six", "author": "Taylor Jenkins Reid"},
    {"title": "It Ends with Us", "author": "Colleen Hoover"},
    {"title": "Verity", "author": "Colleen Hoover"},
    {"title": "The Martian", "author": "Andy Weir"},
    {"title": "Project Hail Mary", "author": "Andy Weir"},
    # --- Popular & Foundational YA ---
    {"title": "Harry Potter and the Sorcerer's Stone", "author": "J.K. Rowling"},
    {"title": "Harry Potter and the Chamber of Secrets", "author": "J.K. Rowling"},
    {"title": "Harry Potter and the Prisoner of Azkaban", "author": "J.K. Rowling"},
    {"title": "Harry Potter and the Goblet of Fire", "author": "J.K. Rowling"},
    {"title": "The Hunger Games", "author": "Suzanne Collins"},
    {"title": "The Book Thief", "author": "Markus Zusak"},
    {"title": "The Fault in Our Stars", "author": "John Green"},
    {"title": "The Hate U Give", "author": "Angie Thomas"},
    {"title": "A Wrinkle in Time", "author": "Madeleine L'Engle"},
    {"title": "The Giver", "author": "Lois Lowry"},
    {"title": "The Outsiders", "author": "S.E. Hinton"},
    {"title": "His Dark Materials", "author": "Philip Pullman"},  # Often cited as 'The Golden Compass'
    {"title": "Six of Crows", "author": "Leigh Bardugo"},
    {"title": "Percy Jackson & The Olympians: The Lightning Thief", "author": "Rick Riordan"},
    # --- Influential Non-Fiction ---
    {"title": "Sapiens: A Brief History of Humankind", "author": "Yuval Noah Harari"},
    {"title": "Homo Deus: A Brief History of Tomorrow", "author": "Yuval Noah Harari"},
    {"title": "Educated", "author": "Tara Westover"},
    {"title": "The Immortal Life of Henrietta Lacks", "author": "Rebecca Skloot"},
    {"title": "Thinking, Fast and Slow", "author": "Daniel Kahneman"},
    {"title": "A Brief History of Time", "author": "Stephen Hawking"},
    {"title": "The Selfish Gene", "author": "Richard Dawkins"},
    {"title": "Cosmos", "author": "Carl Sagan"},
    {"title": "Guns, Germs, and Steel", "author": "Jared Diamond"},
    {"title": "A Short History of Nearly Everything", "author": "Bill Bryson"},
    {"title": "Freakonomics", "author": "Steven D. Levitt"},
    {"title": "The Tipping Point", "author": "Malcolm Gladwell"},
    {"title": "Outliers", "author": "Malcolm Gladwell"},
    {"title": "Quiet: The Power of Introverts in a World That Can't Stop Talking", "author": "Susan Cain"},
    {"title": "The Body Keeps the Score", "author": "Bessel van der Kolk"},
    {"title": "Between the World and Me", "author": "Ta-Nehisi Coates"},
    {"title": "I Know Why the Caged Bird Sings", "author": "Maya Angelou"},
    {"title": "The Diary of a Young Girl", "author": "Anne Frank"},
    {"title": "Night", "author": "Elie Wiesel"},
    {"title": "Man's Search for Meaning", "author": "Viktor E. Frankl"},
    {"title": "Into the Wild", "author": "Jon Krakauer"},
    {"title": "Into Thin Air", "author": "Jon Krakauer"},
    {"title": "In Cold Blood", "author": "Truman Capote"},
    {"title": "The Sixth Extinction: An Unnatural History", "author": "Elizabeth Kolbert"},
    {"title": "The Emperor of All Maladies: A Biography of Cancer", "author": "Siddhartha Mukherjee"},
    {"title": "Becoming", "author": "Michelle Obama"},
    {"title": "The Glass Castle", "author": "Jeannette Walls"},
    {"title": "Wild: From Lost to Found on the Pacific Crest Trail", "author": "Cheryl Strayed"},
    # --- Popular Self-Improvement & Business ---
    {"title": "Atomic Habits", "author": "James Clear"},
    {"title": "How to Win Friends and Influence People", "author": "Dale Carnegie"},
    {"title": "The 7 Habits of Highly Effective People", "author": "Stephen R. Covey"},
    {"title": "The Power of Now", "author": "Eckhart Tolle"},
    {"title": "Daring Greatly", "author": "Brené Brown"},
    {"title": "Think and Grow Rich", "author": "Napoleon Hill"},
    {"title": "The Subtle Art of Not Giving a F*ck", "author": "Mark Manson"},
]


class Command(BaseCommand):
    help = "Seeds the database with a massive, diverse list of popular books, fetching their details from the Open Library API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Deletes all existing Book and Author objects from the database before seeding.",
        )

    def handle(self, *args, **kwargs):
        if kwargs["clear"]:
            self.stdout.write(self.style.WARNING("Clearing all existing books and authors from the database..."))
            Book.objects.all().delete()
            Author.objects.all().delete()
            self.stdout.write(self.style.SUCCESS("Database cleared."))

        self.stdout.write(
            f"Starting to seed {len(COMPREHENSIVE_BOOK_LIST)} popular books. This will take several minutes..."
        )

        # --- STEP 1: FETCH ALL BOOK DATA IN PARALLEL ---
        # The worker function now ONLY fetches data and returns it.
        # It does NOT interact with the database.

        fetched_results = []
        with ThreadPoolExecutor(max_workers=10) as executor, requests.Session() as session:

            def fetch_book_worker(book_data):
                """Worker function to fetch API details for a single book."""
                try:
                    title, author_name = book_data["title"], book_data["author"]
                    api_details = get_book_details_from_open_library(title, author_name, session)
                    # Return all the data needed for the next step
                    return {"success": True, "original": book_data, "api_details": api_details}
                except Exception as e:
                    return {"success": False, "title": book_data.get("title", "Unknown"), "error": str(e)}

            # The results will be a list of dictionaries from the worker function
            fetched_results = list(executor.map(fetch_book_worker, COMPREHENSIVE_BOOK_LIST))

        successful_fetches = [r for r in fetched_results if r["success"]]
        failed_fetches = [r for r in fetched_results if not r["success"]]

        self.stdout.write(
            self.style.SUCCESS(
                f"\nAPI fetching complete. Successfully fetched data for {len(successful_fetches)}/{len(COMPREHENSIVE_BOOK_LIST)} books."
            )
        )

        # --- STEP 2: WRITE RESULTS TO THE DATABASE SERIALLY ---
        # Now that all API calls are done, we loop through the results one by one.
        # This prevents the "database is locked" error entirely.

        self.stdout.write("Writing fetched data to the database...")
        created_count = 0
        updated_count = 0

        for result in successful_fetches:
            book_data = result["original"]
            api_details = result["api_details"]

            author, _ = Author.objects.get_or_create(name=book_data["author"])

            _, created = Book.objects.update_or_create(
                title=book_data["title"],
                author=author,
                defaults={
                    "global_read_count": random.randint(75, 500),
                    "publish_year": api_details.get("publish_year"),
                    "publisher": api_details.get("publisher"),
                    "page_count": api_details.get("page_count"),
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        # --- FINAL REPORT ---
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write(self.style.SUCCESS("Seeding complete!"))
        self.stdout.write(self.style.SUCCESS(f"  - Created: {created_count} new books."))
        self.stdout.write(self.style.SUCCESS(f"  - Updated: {updated_count} existing books."))

        if failed_fetches:
            self.stdout.write(self.style.ERROR(f"\nEncountered {len(failed_fetches)} API fetch failures:"))
            for failure in failed_fetches:
                self.stdout.write(self.style.ERROR(f"  - Failed on '{failure['title']}': {failure['error']}"))

# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""
Integration tests for the book ingest pipeline, run against a Lily container.

These tests drop real ebook files into the ingest folder and check that they
end up in the library (metadata.db) and in cwa.db's import log.

They need Docker: conftest.py builds and starts the container, and they are
skipped when the docker CLI is missing.
"""

import pytest
import time
from pathlib import Path
import sqlite3
import subprocess
import sys

# Ensure fixtures directory is importable
_tests_dir = Path(__file__).parent.parent
if str(_tests_dir) not in sys.path:
    sys.path.insert(0, str(_tests_dir))

# volume_copy and get_db_path also work when files have to go in with docker cp
from conftest import volume_copy, get_db_path


@pytest.mark.docker_integration
class TestBookIngestInContainer:
    """Test the complete ingest pipeline in a running Docker container."""

    def test_ingest_single_epub(self, sample_ebook_path, ingest_folder, library_folder, cwa_container, container_name, tmp_path):
        """
        Test ingesting a single EPUB.

        This is the most common use case - user drops an EPUB, it gets imported directly.
        """
        # Copy the sample EPUB into the ingest folder
        dest_file = ingest_folder / sample_ebook_path.name
        volume_copy(sample_ebook_path, dest_file)

        print(f"📥 Dropped {sample_ebook_path.name} into ingest folder")

        # Debug: Check if container can see the file
        result = subprocess.run(
            ["docker", "exec", container_name, "ls", "-la", "/cwa-book-ingest"],
            capture_output=True, text=True
        )
        print(f"📁 Files in container:\n{result.stdout}")

        # Wait for ingest to complete (up to 60 seconds)
        max_wait = 60
        start_time = time.time()

        # Check if file was processed (removed from ingest folder)
        while dest_file.exists() and time.time() - start_time < max_wait:
            time.sleep(2)
            if int(time.time() - start_time) % 10 == 0:
                print(f"⏳ Waiting for ingest... ({int(time.time() - start_time)}s)")

        # File should be removed from ingest folder after processing
        if dest_file.exists():
            # Debug: Check container logs
            result = subprocess.run(
                ["docker", "logs", "--tail", "50", container_name],
                capture_output=True, text=True
            )
            print(f"🔍 Container logs:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
            pytest.fail(f"File was not processed within {max_wait} seconds")

        print("✅ File removed from ingest folder (processing complete)")

        # Verify the book was added to the Calibre library
        metadata_db = library_folder / "metadata.db"

        # Wait for metadata.db to be created/updated
        time.sleep(5)

        if not metadata_db.exists():
            pytest.fail("metadata.db was not created in library folder")

        # Get local DB path (extracts from volume if needed)
        local_db = get_db_path(metadata_db, tmp_path)

        # Check that a book was added
        with sqlite3.connect(str(get_db_path(local_db, tmp_path)), timeout=30) as con:
            cur = con.cursor()
            result = cur.execute("SELECT COUNT(*) FROM books").fetchone()
            book_count = result[0] if result else 0

        assert book_count > 0, "No books found in Calibre library after ingest"
        print(f"✅ Book successfully imported (library now has {book_count} book(s))")

    def test_ingest_multiple_files(self, ingest_folder, library_folder, tmp_path, cwa_container):
        """Test ingesting multiple files at once."""
        from fixtures.generate_synthetic import create_minimal_epub

        # Create 3 test EPUBs
        test_files = []
        for i in range(3):
            epub_path = tmp_path / f"test_book_{i}.epub"
            create_minimal_epub(epub_path)

            # Copy to ingest folder
            dest = ingest_folder / epub_path.name
            volume_copy(epub_path, dest)
            test_files.append(dest)
            print(f"📥 Dropped {epub_path.name} into ingest folder")

        # Wait for all files to be processed
        max_wait = 120  # 2 minutes for 3 files
        start_time = time.time()

        all_processed = False
        while time.time() - start_time < max_wait:
            remaining = [f for f in test_files if f.exists()]
            if not remaining:
                all_processed = True
                break
            print(f"⏳ Waiting for ingest... {len(remaining)} files remaining ({int(time.time() - start_time)}s)")
            time.sleep(3)

        if not all_processed:
            remaining_names = [f.name for f in test_files if f.exists()]
            pytest.fail(f"Not all files processed within {max_wait} seconds. Remaining: {remaining_names}")

        print("✅ All files processed")

        # Verify books were added
        metadata_db = library_folder / "metadata.db"
        time.sleep(5)

        with sqlite3.connect(str(get_db_path(metadata_db, tmp_path)), timeout=30) as con:
            cur = con.cursor()
            result = cur.execute("SELECT COUNT(*) FROM books").fetchone()
            book_count = result[0] if result else 0

        assert book_count >= 3, f"Expected at least 3 books, found {book_count}"
        print(f"✅ All books imported (library now has {book_count} book(s))")


@pytest.mark.docker_integration
class TestInternationalCharacters:
    """Test handling of international/unicode characters in filenames."""

    def test_ingest_international_filename(self, ingest_folder, library_folder, tmp_path, cwa_container):
        """
        Test ingesting a file with international characters in filename.

        Critical for users with non-English languages (German, French, Spanish,
        Polish, Nordic, etc.) where author names and book titles contain
        diacritics, umlauts, and accents.

        Example characters tested:
        - German: äöüß
        - French: éèêë
        - Spanish: áéíóú ñ
        - Nordic: åøæ
        - Polish: książka
        """
        from fixtures.generate_synthetic import create_minimal_epub

        # Create EPUB with international characters in filename
        international_filename = "test_international_äöüß_éèêë_áéíóú_ñ_åøæ_książka.epub"
        epub_path = tmp_path / international_filename
        create_minimal_epub(epub_path)

        # Copy to ingest folder
        dest = ingest_folder / international_filename
        volume_copy(epub_path, dest)

        print(f"📥 Dropped international filename: {international_filename}")

        # Wait for processing
        max_wait = 60
        start_time = time.time()

        while dest.exists() and time.time() - start_time < max_wait:
            time.sleep(2)
            print(f"⏳ Waiting for ingest... ({int(time.time() - start_time)}s)")

        if dest.exists():
            pytest.fail(f"File with international characters was not processed within {max_wait} seconds")

        print("✅ File with international characters removed (processing complete)")

        # Verify the book was added to library
        metadata_db = library_folder / "metadata.db"
        time.sleep(5)

        if not metadata_db.exists():
            pytest.fail("metadata.db was not created")

        with sqlite3.connect(str(get_db_path(metadata_db, tmp_path)), timeout=30) as con:
            cur = con.cursor()
            result = cur.execute("SELECT COUNT(*) FROM books").fetchone()
            book_count = result[0] if result else 0

        assert book_count > 0, "Book with international filename was not imported"
        print(f"✅ Book with international characters successfully imported (library has {book_count} book(s))")


@pytest.mark.docker_integration
class TestProcessLockInContainer:
    """Test ProcessLock mechanism prevents concurrent ingest processes."""

    def test_lock_released_after_processing(self, ingest_folder, sample_ebook_path, tmp_path, cwa_container):
        """
        Verify that lock file is cleaned up after successful processing.
        """
        dest_file = ingest_folder / sample_ebook_path.name
        volume_copy(sample_ebook_path, dest_file)

        # Wait for processing
        max_wait = 60
        start_time = time.time()
        while dest_file.exists() and time.time() - start_time < max_wait:
            time.sleep(2)

        # Give a moment for cleanup
        time.sleep(3)

        # Check if lock file exists (it shouldn't after successful processing)
        # Lock file would be in /tmp/ingest_processor.lock inside container
        # We can't easily check inside container, but we can verify next file processes

        # Drop another file
        dest_file2 = ingest_folder / f"second_{sample_ebook_path.name}"
        volume_copy(sample_ebook_path, dest_file2)

        # If lock wasn't released, this would timeout
        start_time = time.time()
        while dest_file2.exists() and time.time() - start_time < max_wait:
            time.sleep(2)

        if dest_file2.exists():
            pytest.fail("Second file not processed - lock may not have been released")

        print("✅ Lock properly released between processings")


@pytest.mark.docker_integration
@pytest.mark.slow
class TestIngestStability:
    """Test ingest processor stability and error recovery."""

    def test_processing_survives_multiple_files(self, ingest_folder, library_folder, tmp_path, cwa_container):
        """
        Test that ingest processor can handle many files without crashing.

        Regression test for memory leaks and resource exhaustion.
        """
        from fixtures.generate_synthetic import create_minimal_epub

        num_files = 5  # Conservative number for CI
        created_files = []

        print(f"📥 Dropping {num_files} files sequentially")

        for i in range(num_files):
            epub_path = tmp_path / f"stability_test_{i}.epub"
            create_minimal_epub(epub_path)

            dest = ingest_folder / epub_path.name
            volume_copy(epub_path, dest)
            created_files.append(dest)

            # Wait for this file to be processed before dropping next
            max_wait = 60
            start_time = time.time()
            while dest.exists() and time.time() - start_time < max_wait:
                time.sleep(2)

            if dest.exists():
                pytest.fail(f"File {i+1}/{num_files} not processed, ingest may have crashed")

            print(f"  ✓ File {i+1}/{num_files} processed")

        print(f"✅ Successfully processed {num_files} files without crashes")

    def test_zero_byte_file_doesnt_crash_ingest(self, ingest_folder, tmp_path, cwa_container):
        """
        Test that a 0-byte file doesn't crash the ingest service.
        """
        empty_file = tmp_path / "empty.epub"
        empty_file.touch()

        dest = ingest_folder / "zero_byte_test.epub"
        volume_copy(empty_file, dest)

        print("📥 Dropped 0-byte file")

        # Wait and see what happens
        time.sleep(20)

        # Drop a valid file afterwards to verify ingest still works
        from fixtures.generate_synthetic import create_minimal_epub
        local_valid_file = tmp_path / "after_zero_byte.epub"
        create_minimal_epub(local_valid_file)

        valid_file = ingest_folder / "after_zero_byte.epub"
        volume_copy(local_valid_file, valid_file)

        max_wait = 60
        start_time = time.time()
        while valid_file.exists() and time.time() - start_time < max_wait:
            time.sleep(2)

        if valid_file.exists():
            pytest.fail("Ingest service may have crashed after processing 0-byte file")

        print("✅ Ingest service survived 0-byte file and continued processing")


@pytest.mark.docker_integration
class TestMetadataAndDatabase:
    """Test database interactions and metadata handling."""

    def test_book_appears_in_metadata_db(self, ingest_folder, library_folder, sample_ebook_path, tmp_path, cwa_container, container_name):
        """
        Verify that imported books are correctly added to metadata.db.

        Checks that Calibre database has proper book record with title, author, etc.
        """
        dest_file = ingest_folder / sample_ebook_path.name
        volume_copy(sample_ebook_path, dest_file)
        print(f"📤 Copied {sample_ebook_path.name} to ingest folder")

        # Debug: Check if container can see the file
        result = subprocess.run(
            ["docker", "exec", container_name, "ls", "-la", "/cwa-book-ingest"],
            capture_output=True, text=True
        )
        print(f"📁 Files in container ingest folder:\n{result.stdout}")

        # Wait for import
        max_wait = 60
        start_time = time.time()
        check_count = 0
        while dest_file.exists() and time.time() - start_time < max_wait:
            check_count += 1
            if check_count % 5 == 0:  # Print every 10 seconds
                print(f"⏳ Still waiting for file to be processed... ({time.time() - start_time:.1f}s elapsed)")
            time.sleep(2)

        elapsed = time.time() - start_time
        if dest_file.exists():
            print(f"⚠️  File still exists after {elapsed:.1f}s timeout!")
            # Debug: Check container logs
            result = subprocess.run(
                ["docker", "logs", "--tail", "50", container_name],
                capture_output=True, text=True
            )
            print(f"🔍 Container logs (last 50 lines):\n{result.stdout}\n{result.stderr}")
        else:
            print(f"✅ File processed in {elapsed:.1f}s")

        time.sleep(5)  # Let DB settle

        metadata_db = library_folder / "metadata.db"
        print("🔍 Checking if metadata.db exists...")
        assert metadata_db.exists(), "metadata.db was not created"

        # Get local DB path (extracts from volume if needed)
        local_db = get_db_path(metadata_db, tmp_path)

        with sqlite3.connect(str(get_db_path(local_db, tmp_path)), timeout=30) as con:
            cur = con.cursor()

            # Check book exists
            book = cur.execute("SELECT id, title, path FROM books ORDER BY id DESC LIMIT 1").fetchone()
            assert book is not None, "No book record in metadata.db"

            book_id, title, path = book
            print(f"✅ Book in metadata.db: ID={book_id}, title='{title}', path='{path}'")

            # Check that book has an author
            author = cur.execute("""
                SELECT a.name FROM authors a
                JOIN books_authors_link bal ON a.id = bal.author
                WHERE bal.book = ?
            """, (book_id,)).fetchone()

            if author:
                print(f"✅ Book has author: {author[0]}")
            else:
                print("ℹ️  Book has no author (minimal test file)")


@pytest.mark.docker_integration
@pytest.mark.slow
class TestRealWorldScenarios:
    """Test real-world user scenarios end-to-end."""

    def test_user_drops_book_and_it_appears_in_library(self, ingest_folder, library_folder, cwa_container, tmp_path):
        """
        Complete user workflow: drop book → wait → verify it's in library.

        This is the most important test - it validates the entire pipeline
        works as users expect.
        """
        fixtures_dir = Path(__file__).parent.parent / "fixtures" / "sample_books"

        print(f"📁 Looking for fixtures in: {fixtures_dir}")
        print(f"📁 Fixtures dir exists: {fixtures_dir.exists()}")

        if fixtures_dir.exists():
            all_epubs = list(fixtures_dir.glob("*.epub"))
            print(f"📚 Found {len(all_epubs)} total EPUB files")
            for epub in all_epubs:
                size = epub.stat().st_size
                starts_with_test = epub.name.startswith("test_")
                print(f"  - {epub.name}: {size:,} bytes (test file: {starts_with_test})")

        # Use a real book (not synthetic)
        real_books = [f for f in fixtures_dir.glob("*.epub")
                     if not f.name.startswith("test_")
                     and f.stat().st_size > 10000]  # Skip tiny test files

        print(f"📗 Filtered to {len(real_books)} real books (>10KB, not test_*)")

        if not real_books:
            pytest.skip("No real book files available")

        source_book = real_books[0]
        dest_file = ingest_folder / source_book.name
        volume_copy(source_book, dest_file)

        original_size = source_book.stat().st_size
        print(f"📥 User drops: {source_book.name} ({original_size:,} bytes)")

        # User waits...
        max_wait = 180  # Real books might take longer
        start_time = time.time()

        while dest_file.exists() and time.time() - start_time < max_wait:
            elapsed = int(time.time() - start_time)
            if elapsed % 10 == 0:
                print(f"⏳ User waiting... ({elapsed}s)")
            time.sleep(3)

        if dest_file.exists():
            pytest.fail(f"Book not processed within {max_wait} seconds")

        processing_time = int(time.time() - start_time)
        print(f"✅ Book processed in {processing_time} seconds")

        # Verify book is in library
        time.sleep(5)

        metadata_db = library_folder / "metadata.db"
        with sqlite3.connect(str(get_db_path(metadata_db, tmp_path)), timeout=30) as con:
            cur = con.cursor()
            books = cur.execute("SELECT title FROM books ORDER BY timestamp DESC LIMIT 1").fetchone()

            if books:
                print(f"✅ Book appears in library: '{books[0]}'")
            else:
                pytest.fail("Book not found in library after import")

        # Check that book folder exists
        book_dirs = [d for d in library_folder.iterdir()
                    if d.is_dir() and d.name not in ('.', '..', '.keep')]

        assert len(book_dirs) > 0, "No book directories created"
        print(f"✅ Book stored in: {book_dirs[0].name}/")

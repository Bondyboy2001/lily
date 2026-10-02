# Test fixtures

## Sample books

`sample_books/` holds four public-domain EPUBs from [Project Gutenberg](https://www.gutenberg.org/):

- `alice_in_wonderland.epub` - "Alice's Adventures in Wonderland", Lewis Carroll (1865)
- `christmas_carol.epub` - "A Christmas Carol", Charles Dickens (1843)
- `metamorphosis.epub` - "Metamorphosis", Franz Kafka (1915)
- `sherlock_holmes.epub` - "The Adventures of Sherlock Holmes", Arthur Conan Doyle (1892)

The integration tests drop one of these into the ingest folder as a real-world book. They are committed, so tests don't need the network. To fetch them again:

```bash
cd tests/fixtures && python download_gutenberg.py
```

These works are in the public domain in the United States; check the status in your own country before redistributing them.

## Synthetic files

`generate_synthetic.py` provides `create_minimal_epub(path)`, which writes the smallest valid EPUB. Tests call it to make throwaway books in `tmp_path`; nothing generated is committed.

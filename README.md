# December 1967 Comics

A searchable archive of every comic book cover-dated December 1967.

## Running

```
run.bat
```

Then open http://127.0.0.1:5057

## Storage

- `comics.db` — SQLite database, committed to git so data persists across machines.
- `covers/` — cover thumbnails, committed as image files.

## Schema

Single `comics` table with fields for publisher, title, issue number, cover
date, on-sale date, price, page count, cover image filename, writer, artist,
cover artist, editor, story titles, characters, synopsis, and notes.

Search is by publisher (sidebar) and free-text (title / character / writer /
artist / story titles).

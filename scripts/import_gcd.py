"""
Parse GCD advanced-search issue-level output (tab-separated paste) and load
into comics.db.

Expected line format:
  {country}{publisher}\tpreview{title} ({year} series) #{issue}[modifiers]\t{cover_date}\t{on_sale}

Skips [British] / [Canadian] variant reprints (same book, different distribution).
Prints a summary + any unparseable lines.
"""
import os
import re
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "comics.db")
SRC = os.path.join(BASE_DIR, "sources", "gcd-dec-1967.txt")

TITLE_RE = re.compile(r"^(?P<title>.+?) \((?P<series_year>\d{4}) series\) #(?P<rest>.+)$")
VARIANT_RE = re.compile(r"\s*\[(?P<variant>[^\]]+)\]\s*$")
SUBTITLE_RE = re.compile(r"^(?P<issue>[^\s].*?)\s+-\s+(?P<sub>.+)$")

SKIP_VARIANTS = {"British", "Canadian"}


def parse_line(line):
    line = line.rstrip("\n").rstrip("\r")
    if not line.strip():
        return None, "blank"
    parts = line.split("\t")
    if len(parts) < 4:
        return None, f"expected >=4 tab columns, got {len(parts)}"
    country_pub, title_field, cover_date, on_sale = parts[0], parts[1], parts[2], parts[3]

    # Country prefix
    if country_pub.startswith("US"):
        publisher = country_pub[2:].strip()
    else:
        # non-US or missing prefix; keep as-is
        publisher = country_pub.strip()

    if title_field.startswith("preview"):
        title_field = title_field[len("preview"):]

    m = TITLE_RE.match(title_field)
    if not m:
        return None, f"title pattern did not match: {title_field!r}"

    title = m.group("title").strip()
    series_year = m.group("series_year")
    rest = m.group("rest").strip()

    # Trailing [Variant] tag
    variant = None
    vm = VARIANT_RE.search(rest)
    if vm:
        variant = vm.group("variant").strip()
        rest = rest[: vm.start()].strip()

    if variant in SKIP_VARIANTS:
        return None, f"skip variant reprint: [{variant}]"

    # Optional " - Subtitle" (e.g., "51 - Dennis the Menace Christmas Special")
    subtitle = None
    sm = SUBTITLE_RE.match(rest)
    if sm:
        rest = sm.group("issue").strip()
        subtitle = sm.group("sub").strip()

    # Strip trailing " (something)" that duplicates the issue code, e.g. "R-1731 (R-1731)"
    rest = re.sub(r"\s+\([^)]+\)\s*$", "", rest).strip()

    issue_number = rest

    notes_parts = [f"GCD import; series began {series_year}"]
    if variant:
        notes_parts.append(f"variant: {variant}")
    if subtitle:
        notes_parts.append(f"subtitle: {subtitle}")

    return {
        "publisher": publisher,
        "title": title,
        "issue_number": issue_number,
        "cover_date": cover_date.strip(),
        "on_sale_date": on_sale.strip() if on_sale.strip() != "—" else None,
        "notes": "; ".join(notes_parts),
    }, None


def main():
    with open(SRC, encoding="utf-8") as f:
        lines = f.readlines()

    parsed = []
    skipped = []
    for i, line in enumerate(lines, 1):
        row, err = parse_line(line)
        if row is None:
            if err and err != "blank":
                skipped.append((i, err, line.rstrip()))
            continue
        parsed.append(row)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Wipe existing rows so re-runs are idempotent
    conn.execute("DELETE FROM comics")

    for r in parsed:
        conn.execute(
            """INSERT INTO comics
               (publisher, title, issue_number, cover_date, on_sale_date, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (r["publisher"], r["title"], r["issue_number"],
             r["cover_date"], r["on_sale_date"], r["notes"]),
        )
    conn.commit()

    print(f"Imported {len(parsed)} issues.")
    print(f"Skipped {len(skipped)} lines.")
    if skipped:
        print("\nSkipped lines:")
        for i, err, raw in skipped:
            print(f"  line {i}: {err}")
            print(f"    {raw}")

    print("\nBy publisher:")
    for row in conn.execute(
        "SELECT publisher, COUNT(*) n FROM comics "
        "GROUP BY publisher COLLATE NOCASE ORDER BY n DESC, publisher COLLATE NOCASE"
    ):
        print(f"  {row['n']:3}  {row['publisher']}")

    conn.close()


if __name__ == "__main__":
    main()

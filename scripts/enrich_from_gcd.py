"""
Enrich comics.db from the GCD SQLite dump at sources/gcd-*.db.

For each row in our comics table, find the matching GCD issue by
(series.name, series.year_began, issue.number) and populate:
  price, page_count, editor, cover_artist, writer, artist,
  story_titles, characters, synopsis.

Only the first non-variant match is used. Unmatched rows are printed at
the end so you can hand-fix them.
"""
import glob
import os
import re
import sqlite3
import sys
from collections import OrderedDict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUR_DB = os.path.join(BASE_DIR, "comics.db")

GCD_STORY_TYPE_COVER = 6
GCD_STORY_TYPE_COMIC = 19
GCD_STORY_TYPE_TEXT = 21


def find_gcd_db():
    trim = os.path.join(BASE_DIR, "sources", "gcd-dec1967-trim.db")
    if os.path.exists(trim):
        return trim
    matches = sorted(glob.glob(os.path.join(BASE_DIR, "sources", "gcd-*.db")))
    if not matches:
        sys.exit("No GCD dump found at sources/gcd-*.db")
    return matches[-1]


def dedup_join(values, sep="; "):
    """Concatenate strings, dropping empties and preserving order."""
    seen = OrderedDict()
    for v in values:
        if not v:
            continue
        v = v.strip()
        if v and v not in seen:
            seen[v] = None
    return sep.join(seen)


def get_credits(gcd, story_ids, credit_type_ids):
    """Return unique creator names across the given stories/credit types."""
    if not story_ids:
        return ""
    placeholders_s = ",".join("?" * len(story_ids))
    placeholders_c = ",".join("?" * len(credit_type_ids))
    q = f"""
        SELECT DISTINCT cr.gcd_official_name
        FROM gcd_story_credit sc
        JOIN gcd_creator cr ON cr.id = sc.creator_id
        WHERE sc.story_id IN ({placeholders_s})
          AND sc.credit_type_id IN ({placeholders_c})
          AND sc.deleted = 0
        ORDER BY cr.gcd_official_name
    """
    rows = gcd.execute(q, story_ids + credit_type_ids).fetchall()
    return dedup_join(r[0] for r in rows)


def get_characters(gcd, story_ids):
    if not story_ids:
        return ""
    placeholders = ",".join("?" * len(story_ids))
    q = f"""
        SELECT DISTINCT cnd.name
        FROM gcd_story_character sch
        JOIN gcd_character_name_detail cnd ON cnd.id = sch.character_id
        WHERE sch.story_id IN ({placeholders}) AND sch.deleted = 0
        ORDER BY cnd.sort_name
    """
    rows = gcd.execute(q, story_ids).fetchall()
    return dedup_join(r[0] for r in rows)


VOL_ISSUE_RE = re.compile(r"^v(\d+)#(.+)$")


def find_issue(gcd, title, series_year, issue_number, publisher_hint):
    """Return (issue_row, series_row) or (None, None)."""
    # Look up series by name + year. Publisher used only as tiebreaker.
    series_rows = gcd.execute(
        """SELECT s.id, s.name, s.year_began, p.name AS publisher
           FROM gcd_series s
           JOIN gcd_publisher p ON p.id = s.publisher_id
           WHERE s.name = ? AND s.year_began = ? AND s.deleted = 0""",
        (title, series_year),
    ).fetchall()

    if not series_rows:
        return None, None

    if len(series_rows) > 1 and publisher_hint:
        exact = [s for s in series_rows
                 if s["publisher"].lower() == publisher_hint.lower()]
        if exact:
            series_rows = exact

    # GCD stores volume separately: our "v14#12" == (volume='14', number='12')
    vm = VOL_ISSUE_RE.match(issue_number)
    if vm:
        volume, number = vm.group(1), vm.group(2)
        where = "series_id = ? AND number = ? AND volume = ?"
        params_tail = [number, volume]
    else:
        where = "series_id = ? AND number = ?"
        params_tail = [issue_number]

    for series in series_rows:
        issue = gcd.execute(
            f"""SELECT * FROM gcd_issue
                WHERE {where}
                  AND deleted = 0 AND variant_of_id IS NULL
                ORDER BY sort_code LIMIT 1""",
            [series["id"]] + params_tail,
        ).fetchone()
        if issue:
            return issue, series
    return None, None


def collect_story_data(gcd, issue_id):
    """Gather cover-artist, writer, artist, editor, story titles, characters,
    synopsis from the issue's stories."""
    stories = gcd.execute(
        """SELECT id, title, feature, type_id, script, pencils, inks,
                  editing, characters, synopsis, sequence_number
           FROM gcd_story
           WHERE issue_id = ? AND deleted = 0
           ORDER BY sequence_number""",
        (issue_id,),
    ).fetchall()

    cover_ids = [s["id"] for s in stories if s["type_id"] == GCD_STORY_TYPE_COVER]
    comic_ids = [s["id"] for s in stories if s["type_id"] == GCD_STORY_TYPE_COMIC]
    text_ids = [s["id"] for s in stories if s["type_id"] == GCD_STORY_TYPE_TEXT]

    # Credit-type ids per GCD (see scripts sanity output):
    # 1=script  2=pencils  3=inks  6=editing
    # Composite types that include script/pencils/inks: 10,11,12,13
    WRITER_TYPES = [1, 10, 11, 12, 13]
    PENCILS_TYPES = [2, 7, 8, 10, 11, 12, 13, 14]
    INKS_TYPES = [3, 7, 8, 10, 11, 12, 13, 14]
    EDIT_TYPES = [6]

    cover_pencils = get_credits(gcd, cover_ids, PENCILS_TYPES)
    cover_inks = get_credits(gcd, cover_ids, INKS_TYPES)
    cover_artist_parts = []
    if cover_pencils:
        cover_artist_parts.append(cover_pencils)
    if cover_inks and cover_inks != cover_pencils:
        cover_artist_parts.append(f"inks: {cover_inks}")
    cover_artist = "; ".join(cover_artist_parts)

    # Fall back to free-text fields if normalized credits are empty
    if not cover_artist:
        cover_free = dedup_join(
            (s["pencils"] or "") for s in stories if s["type_id"] == GCD_STORY_TYPE_COVER
        )
        cover_artist = cover_free

    writer = get_credits(gcd, comic_ids, WRITER_TYPES)
    if not writer:
        writer = dedup_join(
            (s["script"] or "") for s in stories if s["type_id"] == GCD_STORY_TYPE_COMIC
        )

    pencils = get_credits(gcd, comic_ids, PENCILS_TYPES)
    inks = get_credits(gcd, comic_ids, INKS_TYPES)
    artist_parts = []
    if pencils:
        artist_parts.append(pencils)
    if inks and inks != pencils:
        artist_parts.append(f"inks: {inks}")
    artist = "; ".join(artist_parts)
    if not artist:
        artist = dedup_join(
            (s["pencils"] or "") for s in stories if s["type_id"] == GCD_STORY_TYPE_COMIC
        )

    editor = get_credits(gcd, [s["id"] for s in stories], EDIT_TYPES)
    if not editor:
        editor = dedup_join(
            (s["editing"] or "") for s in stories
        )

    story_titles = dedup_join(
        (s["title"] or (f"[{s['feature']}]" if s["feature"] else ""))
        for s in stories
        if s["type_id"] in (GCD_STORY_TYPE_COMIC, GCD_STORY_TYPE_TEXT) and (s["title"] or s["feature"])
    )

    characters = get_characters(gcd, comic_ids + cover_ids)
    if not characters:
        characters = dedup_join(
            (s["characters"] or "") for s in stories
        )

    synopsis = dedup_join(
        (s["synopsis"] or "") for s in stories if s["type_id"] == GCD_STORY_TYPE_COMIC
    )

    return {
        "cover_artist": cover_artist or None,
        "writer": writer or None,
        "artist": artist or None,
        "editor": editor or None,
        "story_titles": story_titles or None,
        "characters": characters or None,
        "synopsis": synopsis or None,
    }


def main():
    gcd_path = find_gcd_db()
    print(f"Using GCD dump: {os.path.basename(gcd_path)}")
    gcd = sqlite3.connect(gcd_path)
    gcd.row_factory = sqlite3.Row

    ours = sqlite3.connect(OUR_DB)
    ours.row_factory = sqlite3.Row

    rows = ours.execute(
        "SELECT id, publisher, title, series_year, issue_number FROM comics"
    ).fetchall()

    matched = 0
    unmatched = []
    for row in rows:
        if row["series_year"] is None:
            unmatched.append((row, "no series_year"))
            continue

        issue, series = find_issue(
            gcd, row["title"], row["series_year"],
            row["issue_number"], row["publisher"],
        )
        if not issue:
            unmatched.append((row, "no matching GCD issue"))
            continue

        data = collect_story_data(gcd, issue["id"])
        ours.execute(
            """UPDATE comics SET
                 price = ?, page_count = ?,
                 cover_artist = ?, writer = ?, artist = ?, editor = ?,
                 story_titles = ?, characters = ?, synopsis = ?
               WHERE id = ?""",
            (
                issue["price"] or None,
                float(issue["page_count"]) if issue["page_count"] else None,
                data["cover_artist"], data["writer"], data["artist"], data["editor"],
                data["story_titles"], data["characters"], data["synopsis"],
                row["id"],
            ),
        )
        matched += 1

    ours.commit()
    print(f"\nEnriched {matched}/{len(rows)} issues.")
    if unmatched:
        print(f"\n{len(unmatched)} unmatched:")
        for row, why in unmatched:
            print(f"  [{row['publisher']}] {row['title']} ({row['series_year']}) #{row['issue_number']}  -- {why}")

    ours.close()
    gcd.close()


if __name__ == "__main__":
    main()

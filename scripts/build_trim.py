"""
Build a small SQLite database containing only December 1967 issues and
everything they reference, from the full GCD dump.

Output: sources/gcd-dec1967-trim.db

After it works, you can delete sources/gcd-<date>.db and re-download later
if you ever need something outside Dec 1967.
"""
import glob
import os
import sqlite3
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "sources", "gcd-dec1967-trim.db")


def find_full_dump():
    matches = sorted(glob.glob(os.path.join(BASE, "sources", "gcd-*.db")))
    matches = [m for m in matches if "trim" not in m]
    if not matches:
        sys.exit("No full GCD dump at sources/gcd-*.db")
    return matches[-1]


def main():
    src_path = find_full_dump()
    print(f"Source: {os.path.basename(src_path)}")
    if os.path.exists(OUT):
        os.remove(OUT)

    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(OUT)

    t0 = time.time()

    # 1) Copy the schema of every gcd_* table (we don't need Django/taggit)
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'gcd_%' ORDER BY name"
    )]
    for t in tables:
        ddl = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()[0]
        dst.execute(ddl)

    # 2) Find Dec 1967 issues (key_date column stores YYYY-MM-DD)
    dec_issue_ids = [r[0] for r in src.execute(
        "SELECT id FROM gcd_issue WHERE key_date LIKE '1967-12%' AND deleted = 0"
    )]
    print(f"December 1967 issues: {len(dec_issue_ids)}")

    def copy_where(table, where, params=()):
        rows = src.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
        if not rows:
            return 0
        placeholders = ",".join("?" * len(rows[0]))
        dst.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})", rows)
        return len(rows)

    def in_clause(ids):
        return "(" + ",".join(str(int(i)) for i in ids) + ")"

    # 3) Issues
    n = copy_where("gcd_issue", f"id IN {in_clause(dec_issue_ids)}")
    print(f"  gcd_issue: {n}")

    # 4) Series (referenced by the issues) + variants of those issues (base issues)
    variant_of_ids = [r[0] for r in dst.execute(
        "SELECT DISTINCT variant_of_id FROM gcd_issue WHERE variant_of_id IS NOT NULL"
    )]
    if variant_of_ids:
        n = copy_where("gcd_issue", f"id IN {in_clause(variant_of_ids)}")
        print(f"  gcd_issue (variant parents): {n}")

    series_ids = [r[0] for r in dst.execute(
        "SELECT DISTINCT series_id FROM gcd_issue"
    )]
    n = copy_where("gcd_series", f"id IN {in_clause(series_ids)}")
    print(f"  gcd_series: {n}")

    # 5) Publishers
    pub_ids = [r[0] for r in dst.execute(
        "SELECT DISTINCT publisher_id FROM gcd_series"
    )]
    n = copy_where("gcd_publisher", f"id IN {in_clause(pub_ids)}")
    print(f"  gcd_publisher: {n}")

    # 6) Stories for those issues
    issue_ids_in_dst = [r[0] for r in dst.execute("SELECT id FROM gcd_issue")]
    n = copy_where("gcd_story", f"issue_id IN {in_clause(issue_ids_in_dst)}")
    print(f"  gcd_story: {n}")

    # 7) Story credits + creators
    story_ids = [r[0] for r in dst.execute("SELECT id FROM gcd_story")]
    if story_ids:
        n = copy_where("gcd_story_credit", f"story_id IN {in_clause(story_ids)}")
        print(f"  gcd_story_credit: {n}")

    creator_ids = [r[0] for r in dst.execute(
        "SELECT DISTINCT creator_id FROM gcd_story_credit"
    )]
    if creator_ids:
        n = copy_where("gcd_creator", f"id IN {in_clause(creator_ids)}")
        print(f"  gcd_creator: {n}")

    # 8) Story characters + character name details + canonical characters
    if story_ids:
        n = copy_where("gcd_story_character", f"story_id IN {in_clause(story_ids)}")
        print(f"  gcd_story_character: {n}")

    cnd_ids = [r[0] for r in dst.execute(
        "SELECT DISTINCT character_id FROM gcd_story_character"
    )]
    if cnd_ids:
        n = copy_where("gcd_character_name_detail", f"id IN {in_clause(cnd_ids)}")
        print(f"  gcd_character_name_detail: {n}")

    char_ids = [r[0] for r in dst.execute(
        "SELECT DISTINCT character_id FROM gcd_character_name_detail"
    )]
    if char_ids:
        n = copy_where("gcd_character", f"id IN {in_clause(char_ids)}")
        print(f"  gcd_character: {n}")

    # 9) Lookup tables (small — copy in full)
    for t in ["gcd_credit_type", "gcd_story_type", "gcd_name_type",
              "gcd_character_role", "gcd_series_publication_type"]:
        if t in tables:
            n = copy_where(t, "1=1")
            print(f"  {t}: {n}")

    # 10) Indexes to make lookups instant
    dst.executescript("""
        CREATE INDEX IF NOT EXISTS idx_series_name_year
            ON gcd_series(name, year_began);
        CREATE INDEX IF NOT EXISTS idx_issue_series_number
            ON gcd_issue(series_id, number, volume);
        CREATE INDEX IF NOT EXISTS idx_story_issue
            ON gcd_story(issue_id);
        CREATE INDEX IF NOT EXISTS idx_story_credit_story
            ON gcd_story_credit(story_id);
        CREATE INDEX IF NOT EXISTS idx_story_char_story
            ON gcd_story_character(story_id);
    """)

    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()

    size_mb = os.path.getsize(OUT) / (1024 * 1024)
    print(f"\nWrote {OUT} ({size_mb:.1f} MB) in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

import os
import sqlite3
from flask import Flask, request, jsonify, render_template, send_from_directory, abort

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "comics.db")
COVERS_DIR = os.path.join(BASE_DIR, "covers")

app = Flask(__name__, template_folder="templates")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS comics (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                publisher      TEXT NOT NULL,
                title          TEXT NOT NULL,
                issue_number   TEXT,
                cover_date     TEXT DEFAULT 'December 1967',
                on_sale_date   TEXT,
                price          TEXT,
                page_count     INTEGER,
                cover_image    TEXT,
                writer         TEXT,
                artist         TEXT,
                cover_artist   TEXT,
                editor         TEXT,
                story_titles   TEXT,
                characters     TEXT,
                synopsis       TEXT,
                notes          TEXT,
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_comics_publisher ON comics(publisher COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_comics_title     ON comics(title COLLATE NOCASE);

            CREATE TRIGGER IF NOT EXISTS comics_updated_at
            AFTER UPDATE ON comics
            BEGIN
                UPDATE comics SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END;
        """)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/publishers")
def list_publishers():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT publisher, COUNT(*) AS n FROM comics "
            "GROUP BY publisher COLLATE NOCASE ORDER BY publisher COLLATE NOCASE"
        ).fetchall()
    return jsonify([{"publisher": r["publisher"], "count": r["n"]} for r in rows])


@app.route("/api/comics")
def list_comics():
    publisher = request.args.get("publisher", "").strip()
    q = request.args.get("q", "").strip()

    sql = "SELECT * FROM comics WHERE 1=1"
    params = []
    if publisher:
        sql += " AND publisher = ? COLLATE NOCASE"
        params.append(publisher)
    if q:
        sql += (
            " AND (title LIKE ? COLLATE NOCASE"
            " OR characters LIKE ? COLLATE NOCASE"
            " OR writer LIKE ? COLLATE NOCASE"
            " OR artist LIKE ? COLLATE NOCASE"
            " OR story_titles LIKE ? COLLATE NOCASE)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    sql += " ORDER BY publisher COLLATE NOCASE, title COLLATE NOCASE, issue_number"

    with get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/comics/<int:comic_id>")
def get_comic(comic_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM comics WHERE id = ?", (comic_id,)).fetchone()
    if not row:
        abort(404)
    return jsonify(dict(row))


@app.route("/api/comics", methods=["POST"])
def create_comic():
    data = request.get_json(force=True)
    fields = [
        "publisher", "title", "issue_number", "cover_date", "on_sale_date",
        "price", "page_count", "cover_image", "writer", "artist",
        "cover_artist", "editor", "story_titles", "characters",
        "synopsis", "notes",
    ]
    values = [data.get(f) for f in fields]
    with get_db() as conn:
        cur = conn.execute(
            f"INSERT INTO comics ({', '.join(fields)}) "
            f"VALUES ({', '.join('?' * len(fields))})",
            values,
        )
        new_id = cur.lastrowid
    return jsonify({"id": new_id}), 201


@app.route("/api/comics/<int:comic_id>", methods=["PUT"])
def update_comic(comic_id):
    data = request.get_json(force=True)
    fields = [
        "publisher", "title", "issue_number", "cover_date", "on_sale_date",
        "price", "page_count", "cover_image", "writer", "artist",
        "cover_artist", "editor", "story_titles", "characters",
        "synopsis", "notes",
    ]
    sets = ", ".join(f"{f} = ?" for f in fields)
    values = [data.get(f) for f in fields] + [comic_id]
    with get_db() as conn:
        cur = conn.execute(f"UPDATE comics SET {sets} WHERE id = ?", values)
        if cur.rowcount == 0:
            abort(404)
    return jsonify({"ok": True})


@app.route("/api/comics/<int:comic_id>", methods=["DELETE"])
def delete_comic(comic_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM comics WHERE id = ?", (comic_id,))
        if cur.rowcount == 0:
            abort(404)
    return jsonify({"ok": True})


@app.route("/covers/<path:filename>")
def cover(filename):
    return send_from_directory(COVERS_DIR, filename)


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5057, debug=True)

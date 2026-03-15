import json
import math
import os
import random
import sqlite3
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_file

app = Flask(__name__)

# --- Database ---

def get_db():
    postgres_url = os.environ.get("POSTGRES_URL")
    if postgres_url:
        import pg8000
        conn = pg8000.connect(dsn=postgres_url)
        conn.autocommit = True
        return conn, "postgres"
    else:
        db_path = os.path.join(os.path.dirname(__file__), "local.db")
        conn = sqlite3.connect(db_path)
        conn.isolation_level = None  # autocommit
        return conn, "sqlite"

def db_execute(conn, db_type, query, params=None):
    if db_type == "postgres":
        query = query.replace("?", "%s")
    cur = conn.cursor()
    cur.execute(query, params or [])
    return cur

def init_db():
    conn, db_type = get_db()
    if db_type == "postgres":
        db_execute(conn, db_type, """
            CREATE TABLE IF NOT EXISTS responses (
                id SERIAL PRIMARY KEY,
                question_id TEXT NOT NULL,
                true_answer TEXT NOT NULL,
                reported_answer TEXT NOT NULL,
                was_flipped BOOLEAN NOT NULL,
                epsilon REAL NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        db_execute(conn, db_type, """
            CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id TEXT NOT NULL,
                true_answer TEXT NOT NULL,
                reported_answer TEXT NOT NULL,
                was_flipped BOOLEAN NOT NULL,
                epsilon REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.close()

init_db()

# --- Questions ---

QUESTIONS = {
    "aroused": "Are you currently aroused?",
    "cheated": "Have you ever cheated on a partner?",
    "therapy": "Are you currently in therapy?",
    "drugs": "Have you used illegal drugs in the past year?",
    "fired": "Have you ever been fired from a job?",
    "crush": "Do you have a crush on a coworker?",
}

DEFAULT_QUESTION = "aroused"
FIXED_DELTA = 1e-5  # fixed delta, displayed but not used in randomized response

# --- Randomized Response ---

def randomized_response(true_answer, epsilon):
    """
    Randomized response with epsilon-differential privacy.
    With probability p = e^eps / (1 + e^eps), report truthfully.
    Otherwise, flip the answer.
    """
    p_truth = math.exp(epsilon) / (1 + math.exp(epsilon))
    if random.random() < p_truth:
        return true_answer, False
    else:
        flipped = "no" if true_answer == "yes" else "yes"
        return flipped, True

# --- Routes ---

@app.route("/")
def home():
    return send_file(os.path.join(os.path.dirname(__file__), "index.html"))

@app.route("/api/questions")
def get_questions():
    return jsonify({"questions": QUESTIONS, "default": DEFAULT_QUESTION, "delta": FIXED_DELTA})

@app.route("/api/submit", methods=["POST"])
def submit():
    data = request.json
    reported_answer = data.get("reported_answer", "").lower()
    epsilon = float(data.get("epsilon", 1.0))
    question_id = data.get("question_id", DEFAULT_QUESTION)

    if reported_answer not in ("yes", "no"):
        return jsonify({"error": "Answer must be yes or no"}), 400
    if epsilon < 0.01 or epsilon > 1.0:
        return jsonify({"error": "Epsilon must be between 0.01 and 1.0"}), 400
    if question_id not in QUESTIONS:
        return jsonify({"error": "Invalid question"}), 400

    # Server only sees the already-randomized answer. True answer never leaves the browser.
    conn, db_type = get_db()
    db_execute(conn, db_type,
        "INSERT INTO responses (question_id, true_answer, reported_answer, was_flipped, epsilon) VALUES (?, ?, ?, ?, ?)",
        [question_id, "unknown", reported_answer, False, epsilon]
    )
    conn.close()

    return jsonify({"ok": True})

@app.route("/api/results")
def results():
    question_id = request.args.get("question_id", DEFAULT_QUESTION)
    if question_id not in QUESTIONS:
        return jsonify({"error": "Invalid question"}), 400

    conn, db_type = get_db()

    # Only show responses from 1+ hour ago to prevent timing correlation
    if db_type == "postgres":
        time_filter = "AND created_at <= NOW() - INTERVAL '1 hour'"
    else:
        time_filter = "AND created_at <= datetime('now', '-1 hour')"

    cur2 = db_execute(conn, db_type,
        f"SELECT COUNT(*) FROM responses WHERE question_id = ? {time_filter}",
        [question_id]
    )
    total_delayed = cur2.fetchone()[0]

    # If there are old-enough results, use the time-filtered set
    # Otherwise fall back to showing all results
    if total_delayed > 0:
        used_filter = time_filter
        total = total_delayed
        all_recent = False
    else:
        used_filter = ""
        cur_all = db_execute(conn, db_type,
            "SELECT COUNT(*) FROM responses WHERE question_id = ?",
            [question_id]
        )
        total = cur_all.fetchone()[0]
        all_recent = True

    cur3 = db_execute(conn, db_type,
        f"SELECT reported_answer, COUNT(*) FROM responses WHERE question_id = ? {used_filter} GROUP BY reported_answer",
        [question_id]
    )
    raw_counts = {row[0]: row[1] for row in cur3.fetchall()}
    conn.close()

    yes_raw = raw_counts.get("yes", 0)
    no_raw = raw_counts.get("no", 0)

    return jsonify({
        "question": QUESTIONS[question_id],
        "question_id": question_id,
        "total": total,
        "reported_yes": yes_raw,
        "reported_no": no_raw,
        "delta": FIXED_DELTA,
        "all_recent": all_recent,
    })

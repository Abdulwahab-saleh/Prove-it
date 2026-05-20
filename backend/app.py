"""
PROVE IT — Backend API
Render.com deployment  |  gunicorn app:app

- Frontend hosted separately on Netlify
- This file serves API only (no HTML)
"""

from flask import Flask, request, jsonify
import sqlite3, os, uuid, json, random, string
from datetime import datetime

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════════
# CORS — allow all origins (Netlify frontend + local dev)
# ══════════════════════════════════════════════════════════════════════
@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"]  = "*"
    r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return r

@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        from flask import make_response
        res = make_response()
        res.headers["Access-Control-Allow-Origin"]  = "*"
        res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
        res.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return res, 200

# ══════════════════════════════════════════════════════════════════════
# PATHS & LOADERS
# ══════════════════════════════════════════════════════════════════════
BASE           = os.path.dirname(os.path.abspath(__file__))
DB_PATH        = os.path.join(BASE, 'proveit.db')
QUESTIONS_JSON = os.path.join(BASE, 'questions.json')
PROFILES_JSON  = os.path.join(BASE, 'profiles.json')

def load_questions_json() -> dict:
    if not os.path.exists(QUESTIONS_JSON):
        return {"expert": [], "human": []}
    with open(QUESTIONS_JSON, encoding='utf-8') as f:
        return json.load(f)

def load_profiles() -> dict:
    if not os.path.exists(PROFILES_JSON):
        return {}
    with open(PROFILES_JSON, encoding='utf-8') as f:
        return json.load(f)

def save_profiles(data: dict):
    with open(PROFILES_JSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def normalize_domain(domain_str: str) -> str:
    if not domain_str:
        return "General"
    return domain_str.strip()

def json_q_to_dict(q: dict, q_type: str, answered: bool = False) -> dict:
    return {
        "id":       str(q.get("id", "")),
        "text":     q.get("text", ""),
        "tag":      q.get("tag", q.get("domain", "GENERAL")),
        "domain":   q.get("domain", "General"),
        "urgency":  bool(q.get("urgency", False)),
        "urgent":   bool(q.get("urgency", False)),
        "answers":  q.get("answer_count", 0),
        "answered": answered,
        "q_type":   q_type,
    }

# ══════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════
@app.route('/health')
@app.route('/')
def health():
    return jsonify({"status": "ok", "service": "proveit-api"})

# ══════════════════════════════════════════════════════════════════════
# ROUTES — QUESTIONS
# ══════════════════════════════════════════════════════════════════════
@app.route('/get-questions', methods=['GET', 'POST'])
def get_questions():
    if request.method == 'POST':
        d      = request.json or {}
        domain = d.get('domain', 'General')
        seen   = set(str(x) for x in d.get('seenIds', []))
    else:
        domain = request.args.get('domain', 'General')
        seen   = set()

    data   = load_questions_json()
    result = {"expert": [], "human": []}
    target_domain = normalize_domain(domain)

    for q_type in ("expert", "human"):
        questions = data.get(q_type, [])
        if target_domain not in ('general', 'all', 'General'):
            filtered = [q for q in questions if normalize_domain(q.get('domain', '')) == target_domain]
            if not filtered:
                filtered = [q for q in questions if target_domain.lower() in str(q.get('domain', '')).lower()]
        else:
            filtered = questions
        if not filtered:
            filtered = questions
        random.shuffle(filtered)
        for q in filtered[:12]:
            result[q_type].append(json_q_to_dict(q, q_type, str(q['id']) in seen))

  # أسئلة المستخدمين من قاعدة البيانات
    conn = db()
    user_qs = conn.execute(
        "SELECT * FROM user_questions ORDER BY created_at DESC LIMIT 50"
    ).fetchall()
    conn.close()

    for q in user_qs:
        q_type = q['q_type'] if q['q_type'] in ('expert', 'human') else 'human'
        result[q_type].insert(0, {
            "id":       f"uq-{q['id']}",
            "text":     q['text'],
            "tag":      q['tag'],
            "domain":   q['domain'],
            "urgency":  bool(q['urgency']),
            "urgent":   bool(q['urgency']),
            "answers":  q['answer_count'],
            "answered": False,
            "q_type":   q_type,
        })

    return jsonify(result)

@app.route('/get-question', methods=['GET'])
def get_question_single():
    domain  = request.args.get('domain', 'General')
    q_type  = request.args.get('tab', 'expert')
    exclude = set(request.args.get('exclude', '').split(','))

    data      = load_questions_json()
    questions = data.get(q_type, [])
    target_domain = normalize_domain(domain)

    if target_domain not in ('general', 'all', 'General'):
        filtered = [q for q in questions if normalize_domain(q.get('domain', '')) == target_domain]
    else:
        filtered = questions
    if not filtered:
        filtered = questions

    available = [q for q in filtered if str(q['id']) not in exclude]
    if not available:
        available = filtered
    if not available:
        return jsonify({"error": "No question found"}), 404

    q = random.choice(available)
    return jsonify(json_q_to_dict(q, q_type))

# ══════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        name TEXT DEFAULT 'Anonymous',
        domain TEXT DEFAULT 'General',
        score INTEGER DEFAULT 0,
        answers INTEGER DEFAULT 0,
        rank_level INTEGER DEFAULT 1,
        cert_id TEXT,
        top_tag TEXT,
        skills TEXT DEFAULT '[]',
        streak INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        user_id TEXT
    );
    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        question_id TEXT NOT NULL,
        name TEXT DEFAULT 'Anonymous',
        domain TEXT,
        answer_text TEXT NOT NULL,
        pts INTEGER DEFAULT 0,
        rank_after TEXT,
        rank_up INTEGER DEFAULT 0,
        skills_earned TEXT DEFAULT '[]',
        created_at TEXT
    );
    """)
    conn.commit()
    conn.close()

RANKS      = ['Λ-01', 'Λ-02', 'Λ-03', 'Λ-04', 'Λ-05', 'Ω-01', 'Ω-02', 'Ω-APEX']
THRESHOLDS = [0, 5, 12, 22, 35, 52, 72, 95]

def calc_rank(n: int):
    idx = max(i for i, t in enumerate(THRESHOLDS) if n >= t)
    idx = min(idx, len(RANKS) - 1)
    return RANKS[idx], idx + 1

def upsert_profile(session_id: str, session_row: dict, skills: list):
    profiles = load_profiles()
    existing = profiles.get(session_id, {})
    rank_label, rank_lvl = calc_rank(session_row['answers'])
    certs = existing.get('certificates', [])
    profiles[session_id] = {
        "session_id":   session_id,
        "name":         session_row['name'],
        "domain":       session_row['domain'],
        "score":        session_row['score'],
        "answers":      session_row['answers'],
        "rank_label":   rank_label,
        "rank_level":   rank_lvl,
        "skills":       skills,
        "certificates": certs,
        "top_tag":      session_row.get('top_tag') or '',
        "streak":       session_row.get('streak', 0),
        "cert_id":      session_row.get('cert_id', ''),
        "joined_at":    existing.get('joined_at', datetime.utcnow().isoformat()),
        "updated_at":   datetime.utcnow().isoformat(),
    }
    save_profiles(profiles)
    return profiles[session_id]

# ══════════════════════════════════════════════════════════════════════
# SESSION ROUTES
# ══════════════════════════════════════════════════════════════════════
@app.route('/init-session',  methods=['POST'])
@app.route('/start-session', methods=['POST'])
def init_session():
    d = request.json or {}
    sid = d.get('session_id') or d.get('sessionId') or ('S-' + uuid.uuid4().hex[:12].upper())
    name = (d.get('name') or 'Anonymous').strip()[:40]
    domain = d.get('domain', 'General')
    now = datetime.utcnow().isoformat()

    conn = db()
    row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if row:
        if d.get('domain'):
            conn.execute("UPDATE sessions SET domain=?, updated_at=? WHERE id=?", (domain, now, sid))
            conn.commit()
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    else:
        cert = 'PI-' + ''.join(random.choices(string.digits, k=6))
        conn.execute(
            "INSERT INTO sessions (id, name, domain, score, answers, rank_level, cert_id, skills, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, name, domain, 0, 0, 1, cert, '[]', now, now)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()

    skills = json.loads(row['skills'] or '[]')
    upsert_profile(sid, dict(row), skills)
    rank, _ = calc_rank(row['answers'])
    return jsonify({"session_id": row['id'], "name": row['name'], "domain": row['domain'],
                    "score": row['score'], "answers": row['answers'], "rank": rank, "skills": skills})

@app.route('/submit-answer', methods=['POST'])
def submit_answer():
    d = request.json or {}
    sid  = d.get('sessionId') or d.get('session_id')
    qid  = d.get('questionId') or d.get('question_id')
    text = (d.get('answerText') or d.get('answer') or '').strip()

    conn = db()
    sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not sess:
        conn.close()
        return jsonify({"error": "Session not found"}), 404

    pts = max(10, min(len(text.split()) * 2, 60) + random.randint(1, 5))
    new_score   = sess['score'] + pts
    new_answers = sess['answers'] + 1
    rank_l, lvl = calc_rank(new_answers)

    conn.execute("UPDATE sessions SET score=?, answers=?, rank_level=? WHERE id=?", (new_score, new_answers, lvl, sid))
    conn.execute(
        "INSERT INTO answers (session_id, question_id, name, domain, answer_text, pts, rank_after, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (sid, qid, sess['name'], sess['domain'], text, pts, rank_l, datetime.utcnow().isoformat())
    )
    conn.commit()
    updated_sess = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()

    skills = json.loads(updated_sess['skills'] or '[]')
    upsert_profile(sid, dict(updated_sess), skills)
    return jsonify({"success": True, "pts": pts, "score": new_score, "answers": new_answers, "rank": rank_l})

@app.route('/get-question-answers', methods=['GET'])
def get_question_answers():
    qid = request.args.get('questionId')
    conn = db()
    rows = conn.execute(
        "SELECT name, answer_text, pts, created_at FROM answers WHERE question_id=? ORDER BY created_at DESC", (qid,)
    ).fetchall()
    conn.close()
    return jsonify([{"name": r['name'], "answerText": r['answer_text'], "pts": r['pts'], "time": r['created_at']} for r in rows])

@app.route('/my-profile')
def my_profile_route():
    sid = request.args.get('sessionId') or request.args.get('session_id')
    profiles = load_profiles()
    if sid in profiles:
        return jsonify(profiles[sid])
    return jsonify({"error": "Profile not found"}), 404

@app.route('/profile/<sid>')
def profile_single_route(sid):
    profiles = load_profiles()
    if sid in profiles:
        return jsonify(profiles[sid])
    return jsonify({"error": "Profile not found"}), 404

@app.route('/leaderboard-full')
@app.route('/leaderboard')
def leaderboard_route():
    domain = request.args.get('domain')
    profiles = load_profiles()
    list_profiles = list(profiles.values())
    if domain and domain.lower() != 'all':
        list_profiles = [p for p in list_profiles if normalize_domain(p.get('domain', '')) == normalize_domain(domain)]
    list_profiles.sort(key=lambda x: x.get('score', 0), reverse=True)

    conn = db()
    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_answers  = conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    conn.close()

    result = []
    for prof in list_profiles[:50]:
        result.append({
            "session_id":   prof.get('session_id', ''),
            "name":         prof.get('name', 'Anonymous'),
            "domain":       prof.get('domain', 'General'),
            "score":        prof.get('score', 0),
            "answers":      prof.get('answers', 0),
            "rank_label":   prof.get('rank_label', 'Λ-01'),
            "rank_level":   prof.get('rank_level', 1),
            "certificates": prof.get('certificates', []),
            "skills":       prof.get('skills', []),
        })
    return jsonify({"leaderboard": result, "meta": {
        "totalSessions": total_sessions,
        "totalAnswers":  total_answers,
        "domain":        domain or 'all',
    }})

@app.route('/stats')
def stats():
    conn = db()
    s = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    a = conn.execute("SELECT COUNT(*) FROM answers").fetchone()[0]
    conn.close()
    data = load_questions_json()
    q = sum(len(data.get(t, [])) for t in ('expert', 'human'))
    return jsonify({"totalSessions": s, "totalAnswers": a, "totalQuestions": q})

if __name__ == '__main__':
    # ══════════════════════════════════════════════════════════════════════
# ROUTE — SUBMIT NEW QUESTION (من المستخدمين)
# ══════════════════════════════════════════════════════════════════════
@app.route('/submit-question', methods=['POST'])
def submit_question():
    d = request.json or {}
    session_id    = d.get('session_id', '')
    question_text = (d.get('question_text') or '').strip()
    tab           = d.get('tab', 'human')
    domain        = d.get('domain', 'General') or 'General'
    urgency       = bool(d.get('urgency', False))

    if not question_text:
        return jsonify({"error": "Question text is required"}), 400

    q_type = tab if tab in ('expert', 'human') else 'human'
    now = datetime.utcnow().isoformat()

    conn = db()
    cur = conn.execute(
        "INSERT INTO user_questions (text, tag, domain, urgency, submitted_by, q_type, created_at) VALUES (?,?,?,?,?,?,?)",
        (question_text, q_type.upper(), domain, 1 if urgency else 0, session_id, q_type, now)
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return jsonify({"success": True, "id": new_id})

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        name TEXT DEFAULT 'Anonymous',
        domain TEXT DEFAULT 'General',
        score INTEGER DEFAULT 0,
        answers INTEGER DEFAULT 0,
        rank_level INTEGER DEFAULT 1,
        cert_id TEXT,
        top_tag TEXT,
        skills TEXT DEFAULT '[]',
        streak INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        user_id TEXT
    );
    CREATE TABLE IF NOT EXISTS answers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        question_id TEXT NOT NULL,
        name TEXT DEFAULT 'Anonymous',
        domain TEXT,
        answer_text TEXT NOT NULL,
        pts INTEGER DEFAULT 0,
        rank_after TEXT,
        rank_up INTEGER DEFAULT 0,
        skills_earned TEXT DEFAULT '[]',
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS user_questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        tag TEXT DEFAULT 'COMMUNITY',
        domain TEXT DEFAULT 'General',
        urgency INTEGER DEFAULT 0,
        answer_count INTEGER DEFAULT 0,
        submitted_by TEXT,
        q_type TEXT DEFAULT 'human',
        created_at TEXT
    );
    """)
    conn.commit()
    conn.close()

init_db()

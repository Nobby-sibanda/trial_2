import re
import json
import sqlite3
import time
import os
from datetime import datetime
from typing import Optional, Dict, List
import requests as req
from flask import Flask, render_template, request, jsonify
import google.generativeai as genai

# ─── CONFIG FROM ENVIRONMENT ─────────────────────────────────
TMDB_API_KEY   = os.environ.get("TMDB_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not TMDB_API_KEY or not GEMINI_API_KEY:
    raise RuntimeError(
        "Missing API keys. Set TMDB_API_KEY and GEMINI_API_KEY "
        "in your .env file or environment variables."
    )

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel(
    model_name="models/gemini-2.0-flash",
    generation_config={"temperature": 0.1, "top_p": 0.9, "max_output_tokens": 3000}
)

TMDB_BASE = "https://api.themoviedb.org/3"
DB_PATH   = os.environ.get("DB_PATH", "/data/movie_cache.db")

WARNING_CATEGORIES = [
    "violence_gore", "self_harm", "suicidal_ideation",
    "miscarriage_pregnancy_loss", "sexual_content_nudity",
    "animal_abuse", "substances", "language",
    "horror_intensity", "flashing_lights"
]

# ─── DEFAULT PROMPT (editable via UI) ────────────────────────
DEFAULT_PROMPT_TEMPLATE = """You are a film content expert with encyclopedic knowledge of movies.
Use your ACTUAL TRAINING KNOWLEDGE of this specific film to generate precise content warnings.

Movie: "{title}" ({year})
MPAA Rating: {rating}
Genres: {genres}
TMDB Overview: {overview}
TMDB Keywords: {keywords}

INSTRUCTIONS:
- Use what you KNOW about this film. Do not just guess from the overview.
- If a content category DEFINITELY occurs in this film, set confidence to 1.0 (confirmed).
- If a category DEFINITELY does NOT occur, set severity to 0 and confidence to 1.0 (confirmed absent).
- Only use confidence < 1.0 when you genuinely do not know (obscure/unfamiliar film).
- severity scale: 0=absent, 1=mild/brief, 2=moderate, 3=severe or graphic
- notes: write a concise, SPOILER-FREE explanation (e.g. "Multiple intense fight scenes with blood")
- flashing_lights: does the film contain strobe effects, rapid cuts, or flashing visuals that could trigger photosensitive epilepsy? Be specific.

Return ONLY valid JSON, no markdown fences, no commentary:
{{
  "disclaimer": "Gemini knowledge-based",
  "spoiler_free": {{
    "violence_gore": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "self_harm": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "suicidal_ideation": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "miscarriage_pregnancy_loss": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "sexual_content_nudity": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "animal_abuse": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "substances": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "language": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "horror_intensity": {{"severity": 0, "confidence": 1.0, "notes": ""}},
    "flashing_lights": {{"severity": 0, "confidence": 1.0, "notes": ""}}
  }}
}}"""

app = Flask(__name__)

# In-memory state (per-process; fine for single-user local/demo use)
app_state = {
    "current_movie": None,
    "conversation_history": [],
    "prompt_template": DEFAULT_PROMPT_TEMPLATE
}


# ─── GEMINI CALL WITH RETRY ───────────────────────────────────
def call_gemini(prompt: str, retries: int = 4) -> str:
    for attempt in range(retries):
        try:
            return gemini_model.generate_content(prompt).text.strip()
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Resource exhausted" in msg or "RESOURCE_EXHAUSTED" in msg:
                wait = 15 * (attempt + 1)
                print(f"Rate limited. Waiting {wait}s (retry {attempt+1}/{retries})...")
                time.sleep(wait)
            else:
                return f"Error: {msg}"
    return "Error: Rate limit exceeded. Please wait a minute and try again."


# ─── DATABASE ────────────────────────────────────────────────
class MovieDatabase:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS movies
            (tmdb_id INTEGER PRIMARY KEY, title TEXT, year TEXT,
             imdb_id TEXT, runtime_min INTEGER, metadata_json TEXT, last_updated TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS content_warnings
            (tmdb_id INTEGER PRIMARY KEY, warnings_json TEXT, last_updated TEXT,
             FOREIGN KEY (tmdb_id) REFERENCES movies(tmdb_id))""")
        conn.commit()
        conn.close()

    def save_movie(self, tmdb_id, data):
        conn = sqlite3.connect(self.db_path)
        conn.cursor().execute(
            "INSERT OR REPLACE INTO movies VALUES (?,?,?,?,?,?,?)",
            (tmdb_id, data.get("title"), data.get("year"), data.get("imdb_id"),
             data.get("runtime_min"), json.dumps(data), datetime.now().isoformat())
        )
        conn.commit(); conn.close()

    def get_movie(self, tmdb_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.cursor().execute(
            "SELECT metadata_json FROM movies WHERE tmdb_id=?", (tmdb_id,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None

    def save_warnings(self, tmdb_id, data):
        conn = sqlite3.connect(self.db_path)
        conn.cursor().execute(
            "INSERT OR REPLACE INTO content_warnings VALUES (?,?,?)",
            (tmdb_id, json.dumps(data), datetime.now().isoformat())
        )
        conn.commit(); conn.close()

    def get_warnings(self, tmdb_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.cursor().execute(
            "SELECT warnings_json FROM content_warnings WHERE tmdb_id=?", (tmdb_id,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None


# ─── DATA FETCHER ────────────────────────────────────────────
class MovieDataFetcher:
    @staticmethod
    def _get(url, params=None):
        r = req.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def search_movie(query, max_results=5):
        data = MovieDataFetcher._get(
            f"{TMDB_BASE}/search/movie",
            {"api_key": TMDB_API_KEY, "query": query, "include_adult": False}
        )
        return (data.get("results") or [])[:max_results]

    @staticmethod
    def build_bundle(tmdb_id):
        d = MovieDataFetcher._get(f"{TMDB_BASE}/movie/{tmdb_id}", {"api_key": TMDB_API_KEY})
        kw_data = MovieDataFetcher._get(f"{TMDB_BASE}/movie/{tmdb_id}/keywords", {"api_key": TMDB_API_KEY})
        rd_data = MovieDataFetcher._get(f"{TMDB_BASE}/movie/{tmdb_id}/release_dates", {"api_key": TMDB_API_KEY})

        cert = None
        for c in rd_data.get("results", []):
            if c.get("iso_3166_1") == "US":
                for rd in c.get("release_dates", []):
                    cert = (rd.get("certification") or "").strip() or None
                    if cert: break

        overview = d.get("overview", "")
        if overview:
            overview = re.sub(r"\s+", " ", overview).strip()

        release = d.get("release_date", "")
        return {
            "tmdb_id": tmdb_id,
            "title": d.get("title"),
            "year": release[:4] if release else None,
            "runtime_min": d.get("runtime"),
            "overview": overview or None,
            "genres": [g["name"] for g in (d.get("genres") or [])],
            "keywords": [k["name"] for k in kw_data.get("keywords", []) if k.get("name")],
            "us_certification": cert,
            "poster_path": d.get("poster_path"),
        }


# ─── WARNING GENERATOR ───────────────────────────────────────
class ContentWarningGenerator:
    @staticmethod
    def generate_warnings(movie_data: Dict, prompt_template: str) -> Dict:
        prompt = prompt_template.format(
            title    = movie_data.get("title", "Unknown"),
            year     = movie_data.get("year", ""),
            rating   = movie_data.get("us_certification", "Unknown"),
            overview = (movie_data.get("overview") or "")[:500],
            keywords = ", ".join((movie_data.get("keywords") or [])[:30]),
            genres   = ", ".join((movie_data.get("genres") or []))
        )
        try:
            text = call_gemini(prompt).strip()
            if text.startswith("```"):
                text = text.split("```")[1].replace("json", "", 1).strip()
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0].strip()
            return json.loads(text)
        except Exception as e:
            print(f"Warning generation error: {e}")
            return {
                "disclaimer": "Fallback",
                "spoiler_free": {
                    cat: {"severity": 0, "confidence": 0.3, "notes": ""}
                    for cat in WARNING_CATEGORIES
                }
            }


db          = MovieDatabase()
fetcher     = MovieDataFetcher()
warning_gen = ContentWarningGenerator()


# ─── ROUTES ──────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/search", methods=["POST"])
def search():
    query = request.json.get("query", "")
    if not query:
        return jsonify([])
    results = fetcher.search_movie(query, max_results=5)
    return jsonify([{
        "tmdb_id": r["id"],
        "title": r.get("title"),
        "year": (r.get("release_date") or "")[:4] or None,
        "poster": f"https://image.tmdb.org/t/p/w200{r['poster_path']}" if r.get("poster_path") else None,
    } for r in results])

@app.route("/api/load_movie", methods=["POST"])
def load_movie():
    tmdb_id = request.json.get("tmdb_id")
    cached  = db.get_movie(tmdb_id)
    if cached:
        app_state["current_movie"] = cached
        warn = db.get_warnings(tmdb_id) or warning_gen.generate_warnings(cached, app_state["prompt_template"])
    else:
        movie_data = fetcher.build_bundle(tmdb_id)
        db.save_movie(tmdb_id, movie_data)
        app_state["current_movie"] = movie_data
        warn = warning_gen.generate_warnings(movie_data, app_state["prompt_template"])
        db.save_warnings(tmdb_id, warn)
    app_state["conversation_history"] = []
    return jsonify({"movie": app_state["current_movie"], "warnings": warn})

@app.route("/api/chat", methods=["POST"])
def chat():
    message = request.json.get("message", "")
    spoiler_mode = request.json.get("spoiler_mode", False)
    if not app_state["current_movie"]:
        return jsonify({"error": "No movie loaded"}), 400
    movie = app_state["current_movie"]
    mode_note = "Do NOT reveal plot twists or endings." if not spoiler_mode else "You may discuss all plot details."
    ctx = f'You are a helpful movie content advisor. Movie: {movie["title"]} ({movie.get("year","")})\n{mode_note}\nAnswer in 2-3 sentences.'
    hist = ctx + "\n\n"
    for m in app_state["conversation_history"][-6:]:
        hist += f"{m['role']}: {m['content']}\n\n"
    hist += f"User: {message}\n\nAssistant:"
    response = call_gemini(hist)
    app_state["conversation_history"] += [
        {"role": "User", "content": message},
        {"role": "Assistant", "content": response}
    ]
    return jsonify({"response": response})

@app.route("/api/prompt", methods=["GET"])
def get_prompt():
    return jsonify({"prompt": app_state["prompt_template"]})

@app.route("/api/prompt", methods=["POST"])
def set_prompt():
    new_prompt = request.json.get("prompt", "").strip()
    if not new_prompt:
        return jsonify({"error": "Prompt cannot be empty"}), 400
    # Validate that required placeholders are present
    required = ["{title}", "{year}", "{rating}", "{genres}", "{overview}", "{keywords}"]
    missing = [p for p in required if p not in new_prompt]
    if missing:
        return jsonify({"error": f"Prompt is missing required placeholders: {', '.join(missing)}"}), 400
    app_state["prompt_template"] = new_prompt
    return jsonify({"ok": True, "message": "Prompt updated. New searches will use this prompt."})

@app.route("/api/prompt/reset", methods=["POST"])
def reset_prompt():
    app_state["prompt_template"] = DEFAULT_PROMPT_TEMPLATE
    return jsonify({"ok": True, "prompt": DEFAULT_PROMPT_TEMPLATE})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

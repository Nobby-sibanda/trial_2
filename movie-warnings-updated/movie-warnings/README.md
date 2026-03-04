# 🎬 Movie Content Warnings

AI-powered content warnings for movies using Gemini + TMDB.

**Features:**
- Gemini-generated warnings based on actual film knowledge (not just guesses)
- Prominent flashing light / epilepsy banner
- 100% confidence display for confirmed warnings
- TMDB movie overview and metadata
- Spoiler mode toggle
- Editable AI prompt via UI
- WCAG 2.1 AA accessible

---

## 🚀 Quick Start with Docker (Recommended)

### 1. Install Docker Desktop
Download from https://www.docker.com/products/docker-desktop and install it.
Make sure it's running (you'll see the whale icon in your taskbar).

### 2. Get your API keys
- **TMDB:** https://www.themoviedb.org/settings/api (free)
- **Gemini:** https://aistudio.google.com/app/apikey (free tier available)

### 3. Set up your environment file
```bash
# Copy the example file
cp .env.example .env

# Open .env and replace the placeholder values with your real keys
```

Your `.env` file should look like:
```
TMDB_API_KEY=abc123yourrealkey
GEMINI_API_KEY=AIzaSyYourrealkey
```

### 4. Build and run
```bash
docker compose up --build
```

That's it. Open http://localhost:5000 in your browser.

To stop: press `Ctrl+C`, or run `docker compose down`.

---

## 🔄 Subsequent Runs

After the first build, you don't need `--build` again unless you change the code:
```bash
docker compose up
```

Run in the background (detached):
```bash
docker compose up -d
docker compose down   # to stop
```

---

## 📁 Project Structure

```
movie-warnings/
├── app.py              # Flask backend + all API logic
├── templates/
│   └── index.html      # Frontend UI
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container definition
├── docker-compose.yml  # One-command launch config
├── .env.example        # Template for API keys (safe to commit)
├── .env                # Your real API keys (NEVER commit this)
└── .gitignore          # Keeps .env out of git
```

---

## 🐙 Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/movie-warnings.git
git push -u origin main
```

The `.gitignore` ensures your `.env` (with real API keys) is never pushed.
Anyone who clones the repo copies their own `.env` from `.env.example`.

---

## ✏️ Editing the AI Prompt

Click the **⚙ AI Prompt** button in the top-right corner of the app to open
the prompt editor. You can change how Gemini generates warnings without
touching any code. Changes apply to the next movie you load.

Required placeholders that must stay in the prompt:
`{title}`, `{year}`, `{rating}`, `{genres}`, `{overview}`, `{keywords}`

---

## 🧪 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/search` | Search movies by title |
| POST | `/api/load_movie` | Load movie + generate warnings |
| POST | `/api/chat` | Chat about the current movie |
| GET | `/api/prompt` | Get current prompt template |
| POST | `/api/prompt` | Update prompt template |
| POST | `/api/prompt/reset` | Reset prompt to default |
| GET | `/health` | Health check |

# Work Journal — Setup Guide

This app has two parts:
- **backend/** — a small Python (FastAPI) server. It holds your API keys safely and is the only thing that talks to Anthropic and Notion.
- **frontend/** — the HTML page you actually use day to day. It only talks to your backend, never directly to Anthropic or Notion.

## 1. Push this to GitHub

Create a new **public or private** repo (private is fine now — your backend lives on Render, not GitHub Pages, so the repo itself can be private without costing anything).

```bash
cd work-journal
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/work-journal.git
git push -u origin main
```

## 2. Deploy the backend on Render

1. Go to [render.com](https://render.com) → sign up / log in (GitHub login works)
2. **New** → **Web Service** → connect your `work-journal` repo
3. Render should detect `render.yaml` automatically. If not, set manually:
   - **Root directory**: leave blank
   - **Build command**: `pip install -r backend/requirements.txt`
   - **Start command**: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
4. Under **Environment**, add these variables (these are YOUR secrets — never go in the code):

   | Key | Value |
   |---|---|
   | `APP_PASSWORD` | a password you choose, e.g. `correct-horse-battery` |
   | `SESSION_SECRET` | any long random string (e.g. generate one at [random.org](https://www.random.org/strings/)) |
   | `ANTHROPIC_API_KEY` | your `sk-ant-...` key from console.anthropic.com |
   | `NOTION_TOKEN` | your `secret_...` token from notion.so/my-integrations |
   | `NOTION_DB_ID` | `381ac3e5d5e980bc94def763cf591f30` |
   | `ALLOWED_ORIGINS` | the URL your frontend will be hosted at (set this after step 3 — for now you can use `*`, then tighten it later) |

5. Click **Create Web Service**. First deploy takes a few minutes.
6. Once live, you'll get a URL like `https://work-journal-api.onrender.com`. Test it by visiting `https://work-journal-api.onrender.com/health` — should show `{"status":"ok"}`.

**Note on free tier:** the service sleeps after 15 min of no traffic. The first request after sleeping takes ~30-50 seconds to wake up — that's normal, just wait.

## 3. Connect the frontend to your backend

Open `frontend/index.html`, find this line near the top of the `<script>`:

```js
const API_BASE = "__BACKEND_URL__";
```

Replace `__BACKEND_URL__` with your actual Render URL, e.g.:

```js
const API_BASE = "https://work-journal-api.onrender.com";
```

## 4. Host the frontend

Easiest: **GitHub Pages**, using the same repo.

1. In your repo, go to **Settings** → **Pages**
2. Under "Build and deployment" → **Source**: Deploy from a branch
3. **Branch**: `main`, folder: `/frontend` (if GitHub doesn't offer a folder picker, move `index.html` to the repo root instead)
4. Save — after ~1 minute you'll get a URL like `https://YOUR_USERNAME.github.io/work-journal/`

Then go back to Render → your backend's environment variables → update `ALLOWED_ORIGINS` to this exact URL (no trailing slash), so only your frontend can call your backend.

## 5. Use it

- Open your GitHub Pages URL on any device (phone, laptop, anywhere)
- Enter your password once — it's remembered on that device after that
- On iPhone: Safari → Share → "Add to Home Screen" to make it feel like an app

## Security notes

- Your Anthropic and Notion keys live only on Render as environment variables — never in your GitHub repo, never in the browser.
- The password gate means only someone who knows your password can use your backend (and therefore your API budget).
- `ALLOWED_ORIGINS` restricts which websites are allowed to call your backend at all — set it to your exact GitHub Pages URL once you have it.
- If you ever suspect your password or any key leaked, just change it in Render's environment variables and redeploy (takes ~1 min).

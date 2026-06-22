import os
import hmac
import hashlib
import time
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx

app = FastAPI(title="Work Journal API")

# ── CORS ──────────────────────────────────────────────────
# Allow your frontend origin(s). Add your GitHub Pages / Render static URL here.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config from environment ───────────────────────────────
APP_PASSWORD = os.environ["APP_PASSWORD"]            # the single password you log in with
SESSION_SECRET = os.environ["SESSION_SECRET"]        # random string used to sign tokens
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DB_ID = os.environ["NOTION_DB_ID"]

SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

# ── Login rate limiting (in-memory, per IP) ───────────────
MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_SECONDS = 60 * 60  # 1 hour

# { ip: {"attempts": int, "locked_until": float} }
_login_attempts: dict[str, dict] = {}


def get_client_ip(request: Request) -> str:
    # Render sits behind a proxy; prefer the forwarded header if present.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(ip: str):
    record = _login_attempts.get(ip)
    if record and record["locked_until"] > time.time():
        remaining_min = int((record["locked_until"] - time.time()) / 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in ~{remaining_min} min.",
        )


def record_failed_attempt(ip: str):
    record = _login_attempts.setdefault(ip, {"attempts": 0, "locked_until": 0})
    record["attempts"] += 1
    if record["attempts"] >= MAX_LOGIN_ATTEMPTS:
        record["locked_until"] = time.time() + LOCKOUT_SECONDS
        record["attempts"] = 0  # reset counter once locked


def record_successful_attempt(ip: str):
    _login_attempts.pop(ip, None)


# ── Simple signed-token session (no DB needed) ───────────
def make_token() -> str:
    expiry = int(time.time()) + SESSION_TTL_SECONDS
    payload = str(expiry)
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str) -> bool:
    try:
        payload, sig = token.split(".")
        expected_sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False
        return int(payload) > time.time()
    except Exception:
        return False


def require_auth(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return True


# ── Models ─────────────────────────────────────────────────
class LoginRequest(BaseModel):
    password: str


class FollowUpRequest(BaseModel):
    dump: str


class SummaryRequest(BaseModel):
    dump: str
    answers: dict  # {question: answer}


class NotionExportRequest(BaseModel):
    wins: list[str]
    plan: list[str]
    journal: str
    tags: list[str]
    date: str  # YYYY-MM-DD


# ── Auth endpoint ──────────────────────────────────────────
@app.post("/login")
def login(req: LoginRequest, request: Request):
    ip = get_client_ip(request)
    check_rate_limit(ip)

    if not hmac.compare_digest(req.password, APP_PASSWORD):
        record_failed_attempt(ip)
        raise HTTPException(status_code=401, detail="Wrong password")

    record_successful_attempt(ip)
    return {"token": make_token()}


# ── Claude helper ──────────────────────────────────────────
async def call_claude(messages: list, system: str = "") -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 1000,
                "system": system,
                "messages": messages,
            },
        )
        data = resp.json()
        if "error" in data:
            raise HTTPException(status_code=502, detail=data["error"].get("message", "Claude API error"))
        return "".join(block.get("text", "") for block in data.get("content", []))


def extract_json(raw: str):
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)


# ── Follow-up questions ────────────────────────────────────
@app.post("/followup")
async def followup(req: FollowUpRequest, _auth: bool = Depends(require_auth)):
    raw = await call_claude(
        messages=[{
            "role": "user",
            "content": (
                f'Work journal dump:\n\n"{req.dump}"\n\n'
                "Generate 3 specific follow-up questions to deepen this reflection. "
                "Focus on: accomplishments worth documenting for a promotion case, "
                "blockers or struggles, and intentions for tomorrow. "
                "Return ONLY a JSON array of strings, no markdown."
            ),
        }],
        system="You are a concise executive coach. Return only valid JSON arrays of strings.",
    )
    try:
        questions = extract_json(raw)
        assert isinstance(questions, list)
    except Exception:
        questions = [
            "What was your biggest accomplishment today and why did it matter?",
            "What slowed you down or created friction?",
            "What's the one thing you want to tackle first tomorrow?",
        ]
    return {"questions": questions}


# ── Summary generation ─────────────────────────────────────
@app.post("/summary")
async def summary(req: SummaryRequest, _auth: bool = Depends(require_auth)):
    answers_text = "\n\n".join(f"Q: {q}\nA: {a}" for q, a in req.answers.items()) or "(none)"
    from datetime import date as date_cls
    date_str = date_cls.today().isoformat()

    raw = await call_claude(
        messages=[{
            "role": "user",
            "content": (
                f"Create a professional work journal entry for {date_str}.\n\n"
                f"Daily dump:\n{req.dump}\n\n"
                f"Follow-up answers:\n{answers_text}\n\n"
                'Return ONLY a JSON object with:\n'
                '- "wins": array of 2-4 bullet strings, results-oriented, suitable for a promotion document\n'
                '- "plan": array of 1-3 bullet strings for next steps / tomorrow\n'
                '- "journal": 2-3 sentences of honest personal reflection\n'
                '- "tags": array of 2-4 short topic tags\n\n'
                "No markdown fences, valid JSON only."
            ),
        }],
        system="You help professionals document their work for career advancement. Be specific and results-oriented. Return only valid JSON.",
    )
    try:
        result = extract_json(raw)
    except Exception:
        result = {
            "wins": ["Completed daily work tasks"],
            "plan": ["Continue planned work"],
            "journal": req.dump[:200],
            "tags": ["work"],
        }
    return result


# ── Notion export ──────────────────────────────────────────
async def create_notion_page(title: str, entry_type: str, blocks: list, tags: list, date_str: str):
    payload = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]},
            "Date": {"date": {"start": date_str}},
            "Tags": {"multi_select": [{"name": t} for t in tags]},
            "Type": {"select": {"name": entry_type}},
        },
        "children": blocks,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Content-Type": "application/json",
                "Notion-Version": "2022-06-28",
            },
            json=payload,
        )
        data = resp.json()
        if "id" not in data:
            raise HTTPException(status_code=502, detail=f"Notion error: {data.get('message', data)}")
        return data


@app.post("/export-notion")
async def export_notion(req: NotionExportRequest, _auth: bool = Depends(require_auth)):
    bullet = lambda text: {"object": "block", "type": "bulleted_list_item",
                            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]}}
    paragraph = lambda text: {"object": "block", "type": "paragraph",
                               "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}

    await create_notion_page(f"Wins – {req.date}", "Win", [bullet(w) for w in req.wins], req.tags, req.date)
    await create_notion_page(f"Plan – {req.date}", "Plan", [bullet(p) for p in req.plan], req.tags, req.date)
    await create_notion_page(f"Journal – {req.date}", "Journal", [paragraph(req.journal)], req.tags, req.date)

    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}

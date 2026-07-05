import os, asyncio, base64, threading, httpx
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events

API_ID    = int(os.environ.get("API_ID", "12345"))
API_HASH  = os.environ.get("API_HASH", "placeholder")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GH_TOKEN  = os.environ.get("GH_TOKEN", "")
GH_REPO   = os.environ.get("GH_REPO", "tajhatAti/Bot")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")
OWNER_ID  = int(os.environ.get("OWNER_ID", 0))
GROK_KEY  = os.environ.get("GROK_API_KEY", "")

BASE   = "plugins"
GH_API = "https://api.github.com"
state  = {}

bot = TelegramClient("github_bot", API_ID, API_HASH)

class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

def run_server():
    HTTPServer(('0.0.0.0', int(os.environ.get("PORT", 8080))), _H).serve_forever()

def gh_headers():
    return {"Authorization": f"token {GH_TOKEN}", "Content-Type": "application/json"}

def full_path(name: str) -> str:
    name = name.strip().lstrip("/")
    if not name.startswith(BASE + "/"):
        name = f"{BASE}/{name}"
    return name

async def gh_get(path: str):
    url = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=gh_headers())
        return r.json() if r.status_code == 200 else None

async def gh_upload(path: str, content: bytes, msg: str) -> bool:
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    data = {
        "message": msg,
        "content": base64.b64encode(content).decode(),
        "branch":  GH_BRANCH
    }
    existing = await gh_get(path)
    if existing and "sha" in existing:
        data["sha"] = existing["sha"]
    async with httpx.AsyncClient() as c:
        r = await c.put(url, headers=gh_headers(), json=data)
        return r.status_code in (200, 201)

async def gh_delete(path: str) -> bool:
    existing = await gh_get(path)
    if not existing or "sha" not in existing:
        return False
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    data = {"message": f"Delete {path} via bot", "sha": existing["sha"], "branch": GH_BRANCH}
    async with httpx.AsyncClient() as c:
        r = await c.delete(url, headers=gh_headers(), json=data)
        return r.status_code == 200

async def ai_fix(code: str) -> str:
    if not GROK_KEY:
        raise Exception("GROK_API_KEY not set.")
    prompt = (
        "You are an expert Python/Telethon developer.\n"
        "Rewrite this Telethon plugin with these rules:\n"
        "1. Accept prefixes (., /, !) and optional bot username\n"
        "2. If sender is owner use edit(), else use reply()\n"
        "3. After replying wait 6 seconds then delete bot response\n"
        "4. Return ONLY raw Python code. No markdown. No explanation.\n\n"
        f"Original:\n{code}"
    )
    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [{"role": "user", "content": prompt}]
    }
    headers = {
        "Authorization": f"Bearer {GROK_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers
        )
    try:
        data = r.json()
    except Exception:
        raise Exception(f"Invalid response: {r.text[:200]}")
    if r.status_code != 200:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        msg = err.get("message", str(data)[:200]) if isinstance(err, dict) else str(data)[:200]
        raise Exception(msg)
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise Exception(f"Unexpected format: {str(data)[:200]}")
    return text.replace("```python", "").replace("```", "").strip()

def owner(e):
    return e.sender_id == OWNER_ID

@bot.on(events.NewMessage(pattern="^/start$"))
async def _(e):
    if not owner(e): return
    await e.reply(
        "**GitHub File Manager + AI Fixer**\n"
        f"Base: `{BASE}/`\n\n"
        "`/new` — নতুন file\n"
        "`/edit` — file edit\n"
        "`/auto_fix filename.py` — AI দিয়ে fix\n"
        "`/rem filename.py` — delete\n"
        "`/ls` — file list\n"
        "`/cat filename.py` — content দেখো\n"
        "`/cancel` — বাতিল"
    )

@bot.on(events.NewMessage(pattern="^/new$"))
async def _(e):
    if not owner(e): return
    state[e.sender_id] = {"step": "filename", "mode": "new"}
    await e.reply("📁 File name দাও:\n_(example: `ping.py`)_")

@bot.on(events.NewMessage(pattern="^/edit$"))
async def _(e):
    if not owner(e): return
    state[e.sender_id] = {"step": "filename", "mode": "edit"}
    await e.reply("✏️ File name দাও:\n_(example: `ping.py`)_")

@bot.on(events.NewMessage(pattern=r"^/auto_fix (.+)"))
async def _(e):
    if not owner(e): return
    path = full_path(e.pattern_match.group(1).strip())
    msg  = await e.reply(f"Fetching `{path}`...")
    existing = await gh_get(path)
    if not existing or "content" not in existing:
        await msg.edit(f"❌ Not found: `{path}`")
        return
    old_code = base64.b64decode(existing["content"]).decode(errors="replace")
    await msg.edit("🧠 AI analyzing...")
    try:
        new_code = await ai_fix(old_code)
        if not new_code or len(new_code) < 10:
            await msg.edit("❌ AI invalid response.")
            return
        await msg.edit("Uploading...")
        ok = await gh_upload(path=path, content=new_code.encode(), msg=f"Auto-fix {path} via AI")
        if ok:
            url = f"https://github.com/{GH_REPO}/blob/{GH_BRANCH}/{path}"
            await msg.edit(f"✅ AI Fixed: `{path}`\n\n[GitHub এ দেখো]({url})")
        else:
            await msg.edit("❌ Upload failed.")
    except Exception as ex:
        await msg.edit(f"❌ AI Error: `{ex}`")

@bot.on(events.NewMessage(pattern=r"^/rem (.+)"))
async def _(e):
    if not owner(e): return
    path = full_path(e.pattern_match.group(1).strip())
    msg  = await e.reply(f"Deleting `{path}`...")
    ok   = await gh_delete(path)
    await msg.edit(f"✅ Deleted: `{path}`" if ok else f"❌ Not found: `{path}`")

@bot.on(events.NewMessage(pattern=r"^/ls(.*)"))
async def _(e):
    if not owner(e): return
    arg  = e.pattern_match.group(1).strip()
    path = full_path(arg) if arg else BASE
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=gh_headers())
    if r.status_code != 200:
        await e.reply("❌ Path not found.")
        return
    items = r.json()
    if not isinstance(items, list):
        await e.reply("❌ Not a directory.")
        return
    lines = [("📁 " if i["type"] == "dir" else "📄 ") + f"`{i['name']}`" for i in items]
    await e.reply(f"**{path}/**\n\n" + "\n".join(lines) if lines else f"**{path}/** is empty.")

@bot.on(events.NewMessage(pattern=r"^/cat (.+)"))
async def _(e):
    if not owner(e): return
    path     = full_path(e.pattern_match.group(1).strip())
    existing = await gh_get(path)
    if not existing or "content" not in existing:
        await e.reply(f"❌ Not found: `{path}`")
        return
    content = base64.b64decode(existing["content"]).decode(errors="replace")
    if len(content) > 3800:
        content = content[:3800] + "\n...(truncated)"
    await e.reply(f"```\n{content}\n```")

@bot.on(events.NewMessage(pattern="^/cancel$"))
async def _(e):
    if not owner(e): return
    if e.sender_id in state:
        del state[e.sender_id]
        await e.reply("❌ Cancelled.")
    else:
        await e.reply("কোনো active action নেই।")

@bot.on(events.NewMessage(func=lambda e: e.sender_id == OWNER_ID and bool(e.text) and not e.text.startswith("/")))
async def _(e):
    uid  = e.sender_id
    text = e.text.strip()
    if uid not in state:
        await e.reply("কোনো action নেই।\n`/new` বা `/edit` দিয়ে শুরু করো।")
        return
    s    = state[uid]
    step = s["step"]
    mode = s["mode"]

    if step == "filename":
        path      = full_path(text)
        s["path"] = path
        if mode == "edit":
            msg      = await e.reply(f"Fetching `{path}`...")
            existing = await gh_get(path)
            if not existing or "content" not in existing:
                await msg.edit(f"❌ Not found: `{path}`\n/cancel দাও বা আবার চেষ্টা করো।")
                del state[uid]
                return
            content = base64.b64decode(existing["content"]).decode(errors="replace")
            preview = content[:3500] + "\n...(truncated)" if len(content) > 3500 else content
            s["step"] = "edit_content"
            await msg.edit(
                f"📄 `{path}`:\n\n```\n{preview}\n```\n\n"
                f"Edited code পাঠাও।\n/cancel বাতিল।"
            )
        else:
            s["step"] = "content"
            await e.reply(f"✅ File: `{path}`\n\nCode পাঠাও।\n/cancel বাতিল।")
        return

    if step in ("content", "edit_content"):
        path = s["path"]
        msg  = await e.reply(f"{'Creating' if mode == 'new' else 'Updating'} `{path}`...")
        ok   = await gh_upload(
            path    = path,
            content = text.encode(),
            msg     = f"{'Add' if mode == 'new' else 'Update'} {path} via bot"
        )
        if ok:
            url    = f"https://github.com/{GH_REPO}/blob/{GH_BRANCH}/{path}"
            action = "Created" if mode == "new" else "Updated"
            del state[uid]
            await msg.edit(
                f"✅ {action}: `{path}`\n\n"
                f"[GitHub এ দেখো]({url})\n\n"
                f"নতুন file: `/new` | Edit: `/edit`"
            )
        else:
            await msg.edit("❌ Failed. GH_TOKEN এর `repo` permission চেক করো।")

async def main():
    threading.Thread(target=run_server, daemon=True).start()
    await bot.start(bot_token=BOT_TOKEN)
    print("[+] GitHub Manager Bot online.")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())

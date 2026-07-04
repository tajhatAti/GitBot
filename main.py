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

BASE   = "plugins"
GH_API = "https://api.github.com"

# state[uid] = {"step": "filename"|"content"|"edit_content", "path": "..."}
state = {}

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

def owner(e):
    return e.sender_id == OWNER_ID

# ── /start ────────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern="^/start$"))
async def _(e):
    if not owner(e): return
    await e.reply(
        "**GitHub File Manager**\n"
        f"Base path: `{BASE}/`\n\n"
        "Commands:\n"
        "`/new` — নতুন file create\n"
        "`/edit` — existing file edit\n"
        "`/rem filename.py` — file delete\n"
        "`/ls` — সব file দেখো\n"
        "`/cat filename.py` — content দেখো\n"
        "`/cancel` — বাতিল"
    )

# ── /new ──────────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern="^/new$"))
async def _(e):
    if not owner(e): return
    state[e.sender_id] = {"step": "filename", "mode": "new"}
    await e.reply("📁 File name দাও:\n_(example: `ping.py`)_")

# ── /edit ─────────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern="^/edit$"))
async def _(e):
    if not owner(e): return
    state[e.sender_id] = {"step": "filename", "mode": "edit"}
    await e.reply("✏️ Edit করতে file name দাও:\n_(example: `ping.py`)_")

# ── /rem ──────────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r"^/rem (.+)"))
async def _(e):
    if not owner(e): return
    path = full_path(e.pattern_match.group(1).strip())
    msg  = await e.reply(f"Deleting `{path}`...")
    ok   = await gh_delete(path)
    await msg.edit(f"✅ Deleted: `{path}`" if ok else f"❌ Not found: `{path}`")

# ── /ls ───────────────────────────────────────────────────────────────────────
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

# ── /cat ──────────────────────────────────────────────────────────────────────
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

# ── /cancel ───────────────────────────────────────────────────────────────────
@bot.on(events.NewMessage(pattern="^/cancel$"))
async def _(e):
    if not owner(e): return
    if e.sender_id in state:
        del state[e.sender_id]
        await e.reply("❌ Cancelled.")
    else:
        await e.reply("কোনো active action নেই।")

# ── TEXT HANDLER ──────────────────────────────────────────────────────────────
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

    # ── Step 1: filename নেওয়া ──
    if step == "filename":
        path       = full_path(text)
        s["path"]  = path
        s["step"]  = "content"

        if mode == "edit":
            msg      = await e.reply(f"Fetching `{path}`...")
            existing = await gh_get(path)
            if not existing or "content" not in existing:
                await msg.edit(f"❌ Not found: `{path}`\n\nআবার চেষ্টা করো বা /cancel দাও।")
                del state[uid]
                return
            content = base64.b64decode(existing["content"]).decode(errors="replace")
            preview = content[:3500] + "\n...(truncated)" if len(content) > 3500 else content
            s["step"] = "edit_content"
            await msg.edit(
                f"📄 `{path}` current content:\n\n"
                f"```\n{preview}\n```\n\n"
                f"Edited code plain text এ পাঠাও।\n/cancel বাতিল।"
            )
        else:
            await e.reply(
                f"✅ File: `{path}`\n\n"
                f"Code plain text এ পাঠাও।\n/cancel বাতিল।"
            )
        return

    # ── Step 2: content নেওয়া ও upload ──
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
                f"নতুন file: /new\nEdit: `/edit`"
            )
        else:
            await msg.edit("❌ Failed. GH_TOKEN এর `repo` permission চেক করো।")
        return

# ── BOOTSTRAP ─────────────────────────────────────────────────────────────────
async def main():
    threading.Thread(target=run_server, daemon=True).start()
    await bot.start(bot_token=BOT_TOKEN)
    print("[+] GitHub Manager Bot online.")
    await bot.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())

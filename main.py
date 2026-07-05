import os
import asyncio
import base64
import threading
import httpx
import google.generativeai as genai
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events

# ── ENVIRONMENT VARIABLES ──
API_ID         = int(os.environ.get("API_ID", "12345"))
API_HASH       = os.environ.get("API_HASH", "placeholder")
BOT_TOKEN      = os.environ.get("BOT_TOKEN", "")
GH_TOKEN       = os.environ.get("GH_TOKEN", "")
GH_REPO        = os.environ.get("GH_REPO", "tajhatAti/Bot")
GH_BRANCH      = os.environ.get("GH_BRANCH", "main")
OWNER_ID       = int(os.environ.get("OWNER_ID", 0))
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # এখানে শুরুতে ডিফাইন করে দিলাম

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
        "**GitHub File Manager & AI Auto-Fixer**\n"
        f"Base path: `{BASE}/`\n\n"
        "Commands:\n"
        "`/new` — নতুন file create\n"
        "`/edit` — existing file edit\n"
        "`/auto_fix filename.py` — 🧠 AI দিয়ে কোড অটো-ফিক্স\n"
        "`/rem filename.py` — file delete\n"
        "`/ls` — সব file দেখো\n"
        "`/cat filename.py` — content দেখো\n"
        "`/cancel` — বাতিল"
    )

# ── /auto_fix (AI Powered Code Editor) ────────────────────────────────────────
@bot.on(events.NewMessage(pattern=r"^/auto_fix (.+)"))
async def auto_fix_cmd(e):
    if not owner(e): return
    
    if not GEMINI_API_KEY:
        return await e.reply("❌ `GEMINI_API_KEY` এনভায়রনমেন্ট ভেরিয়েবলে সেট করা নেই!")
        
    path = full_path(e.pattern_match.group(1).strip())
    msg = await e.reply(f"Fetching `{path}` for AI magic...")
    
    existing = await gh_get(path)
    if not existing or "content" not in existing:
        return await msg.edit(f"❌ Not found: `{path}`")
        
    old_code = base64.b64decode(existing["content"]).decode(errors="replace")
    await msg.edit("🧠 AI is analyzing and rewriting the code...")
    
    try:
        # জেমিনি কনফিগারেশন (ফাইলের শুরুর ভেরিয়েবলটি ব্যবহার করা হয়েছে)
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
    model_name="models/gemini-1.5-flash",
    generation_config={"api_version": "v1beta"}
        )
        
        prompt = f"""
        You are an expert Python developer working with Telethon.
        Rewrite the provided Telethon plugin code according to these exact rules:
        1. Change the event pattern to accept prefixes (., /, !) and optional bot tags: (?i)^[./!]<command_name>(?:@\\w+)?$
        2. Make it public. If sender is `uid`, use `edit()`. Otherwise, use `reply()`. Example: 
           if getattr(e, 'sender_id', None) == uid: m = await e.edit(...) else: m = await e.reply(...)
        3. Add asyncio.sleep(6) at the end, then delete the bot's response message safely. Make sure `import asyncio` is at the top.
        4. Return ONLY the raw valid Python code. Do not include markdown blocks like ```python. Do not explain anything.
        
        Original Code:
        {old_code}
        """
        
        res = model.generate_content(prompt)
        new_code = res.text.replace('```python', '').replace('```', '').strip()
        
        if not new_code or len(new_code) < 10:
            return await msg.edit("❌ AI failed to generate valid code.")
            
        await msg.edit("✅ AI rewrite complete. Uploading to GitHub...")
        
        ok = await gh_upload(
            path=path,
            content=new_code.encode(),
            msg=f"Auto-fixed {path} via Gemini AI"
        )
        
        if ok:
            url = f"[https://github.com/](https://github.com/){GH_REPO}/blob/{GH_BRANCH}/{path}"
            await msg.edit(f"🎉 **Successfully Auto-Fixed via AI:** `{path}`\n\n[GitHub এ দেখো]({url})")
        else:
            await msg.edit("❌ Failed to upload to GitHub.")
            
    except Exception as ex:
        await msg.edit(f"❌ AI Error: `{ex}`")

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

    if step in ("content", "edit_content"):
        path = s["path"]
        msg  = await e.reply(f"{'Creating' if mode == 'new' else 'Updating'} `{path}`...")
        ok   = await gh_upload(
            path    = path,
            content = text.encode(),
            msg     = f"{'Add' if mode == 'new' else 'Update'} {path} via bot"
        )
        if ok:
            url    = f"[https://github.com/](https://github.com/){GH_REPO}/blob/{GH_BRANCH}/{path}"
            action = "Created" if mode == "new" else "Updated"
            del state[uid]
            await msg.edit(
                f"✅ {action}: `{path}`\n\n"
                f"[GitHub এ দেখো]({url})\n\n"
                f"নতুন file: `/new`\nEdit: `/edit`"
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

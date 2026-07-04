import os
import asyncio
import base64
import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TOKEN     = os.environ.get("BOT_TOKEN", "")
GH_TOKEN  = os.environ.get("GH_TOKEN", "")
GH_REPO   = os.environ.get("GH_REPO", "tajhatAti/Bot")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")
OWNER_ID  = int(os.environ.get("OWNER_ID", 0))

BASE_PATH = "plugins"
GH_API    = "https://api.github.com"

# State per user
state = {}
# state[uid] = {
#   "mode": "new" | "edit",
#   "filename": "ping.py",
#   "original": "...existing code..." (only for edit)
# }

def gh_headers():
    return {
        "Authorization": f"token {GH_TOKEN}",
        "Content-Type": "application/json"
    }

def full_path(filename: str) -> str:
    filename = filename.strip().lstrip("/")
    if not filename.startswith(BASE_PATH + "/"):
        filename = f"{BASE_PATH}/{filename}"
    return filename

async def get_file(path: str):
    url = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=gh_headers())
        if r.status_code == 200:
            return r.json()
    return None

async def upload_file(path: str, content: bytes, msg: str) -> bool:
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    data = {
        "message": msg,
        "content": base64.b64encode(content).decode(),
        "branch":  GH_BRANCH
    }
    existing = await get_file(path)
    if existing:
        data["sha"] = existing["sha"]
    async with httpx.AsyncClient() as c:
        r = await c.put(url, headers=gh_headers(), json=data)
        return r.status_code in (200, 201)

async def delete_file(path: str) -> bool:
    existing = await get_file(path)
    if not existing:
        return False
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    data = {
        "message": f"Delete {path} via bot",
        "sha":     existing["sha"],
        "branch":  GH_BRANCH
    }
    async with httpx.AsyncClient() as c:
        r = await c.delete(url, headers=gh_headers(), json=data)
        return r.status_code == 200

def check(update: Update) -> bool:
    return update.effective_user.id == OWNER_ID

# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    await update.message.reply_text(
        "**GitHub File Manager**\n"
        f"Base path: `{BASE_PATH}/`\n\n"
        "Commands:\n"
        "`/new filename.py` — নতুন file তৈরি\n"
        "`/edit filename.py` — existing file edit\n"
        "`/rem filename.py` — file delete\n"
        "`/ls` — সব file দেখো\n"
        "`/cat filename.py` — file content দেখো\n"
        "`/cancel` — current action বাতিল\n\n"
        "Flow:\n"
        "1. `/new ping.py` দাও\n"
        "2. Code plain text এ পাঠাও\n"
        "3. Upload হবে `plugins/ping.py` তে\n"
        "4. আবার নতুন file এর জন্য `/new` দাও",
        parse_mode="Markdown"
    )

# ── /new ──────────────────────────────────────────────────────────────────────
async def new_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    uid = update.effective_user.id
    if not ctx.args:
        await update.message.reply_text("Usage: `/new filename.py`", parse_mode="Markdown")
        return
    filename = ctx.args[0].strip()
    path     = full_path(filename)
    state[uid] = {"mode": "new", "filename": filename, "path": path}
    await update.message.reply_text(
        f"✅ File: `{path}`\n\nএবার code plain text এ পাঠাও।\nবাতিল করতে /cancel",
        parse_mode="Markdown"
    )

# ── /edit ─────────────────────────────────────────────────────────────────────
async def edit_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    uid = update.effective_user.id
    if not ctx.args:
        await update.message.reply_text("Usage: `/edit filename.py`", parse_mode="Markdown")
        return
    filename = ctx.args[0].strip()
    path     = full_path(filename)
    msg      = await update.message.reply_text(f"Fetching `{path}`...", parse_mode="Markdown")
    existing = await get_file(path)
    if not existing:
        await msg.edit_text(f"❌ File not found: `{path}`", parse_mode="Markdown")
        return
    original = base64.b64decode(existing["content"]).decode(errors="replace")
    state[uid] = {"mode": "edit", "filename": filename, "path": path, "original": original}
    # File content পাঠাবো যাতে user copy করে edit করতে পারে
    if len(original) > 3800:
        preview = original[:3800] + "\n...(truncated)"
    else:
        preview = original
    await msg.edit_text(
        f"📄 Current content of `{path}`:\n\n"
        f"```\n{preview}\n```\n\n"
        f"Edited code plain text এ পাঠাও। /cancel বাতিল করতে।",
        parse_mode="Markdown"
    )

# ── /rem ──────────────────────────────────────────────────────────────────────
async def rem_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: `/rem filename.py`", parse_mode="Markdown")
        return
    filename = ctx.args[0].strip()
    path     = full_path(filename)
    msg      = await update.message.reply_text(f"Deleting `{path}`...", parse_mode="Markdown")
    success  = await delete_file(path)
    if success:
        await msg.edit_text(f"✅ Deleted: `{path}`", parse_mode="Markdown")
    else:
        await msg.edit_text(f"❌ Not found or failed: `{path}`", parse_mode="Markdown")

# ── /ls ───────────────────────────────────────────────────────────────────────
async def ls_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    path = BASE_PATH
    if ctx.args:
        path = full_path(ctx.args[0])
    url = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=gh_headers())
    if r.status_code != 200:
        await update.message.reply_text("❌ Path not found.")
        return
    items = r.json()
    if not isinstance(items, list):
        await update.message.reply_text("❌ Not a directory.")
        return
    lines = []
    for item in items:
        icon = "📁" if item["type"] == "dir" else "📄"
        lines.append(f"{icon} `{item['name']}`")
    text = f"**{path}/**\n\n" + "\n".join(lines) if lines else f"**{path}/** is empty."
    await update.message.reply_text(text, parse_mode="Markdown")

# ── /cat ──────────────────────────────────────────────────────────────────────
async def cat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    if not ctx.args:
        await update.message.reply_text("Usage: `/cat filename.py`", parse_mode="Markdown")
        return
    path     = full_path(ctx.args[0])
    existing = await get_file(path)
    if not existing:
        await update.message.reply_text(f"❌ Not found: `{path}`", parse_mode="Markdown")
        return
    content = base64.b64decode(existing["content"]).decode(errors="replace")
    if len(content) > 3800:
        content = content[:3800] + "\n...(truncated)"
    await update.message.reply_text(f"```\n{content}\n```", parse_mode="Markdown")

# ── /cancel ───────────────────────────────────────────────────────────────────
async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    uid = update.effective_user.id
    if uid in state:
        del state[uid]
        await update.message.reply_text("❌ Cancelled.")
    else:
        await update.message.reply_text("কোনো active action নেই।")

# ── TEXT HANDLER (code receive) ───────────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not check(update): return
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if uid not in state:
        await update.message.reply_text(
            "কোনো file select করা নেই।\n`/new filename.py` দিয়ে শুরু করো।",
            parse_mode="Markdown"
        )
        return

    s    = state[uid]
    path = s["path"]
    mode = s["mode"]

    msg = await update.message.reply_text(
        f"{'Creating' if mode == 'new' else 'Updating'} `{path}`...",
        parse_mode="Markdown"
    )

    success = await upload_file(
        path    = path,
        content = text.encode(),
        msg     = f"{'Add' if mode == 'new' else 'Update'} {path} via Telegram bot"
    )

    if success:
        url = f"https://github.com/{GH_REPO}/blob/{GH_BRANCH}/{path}"
        action = "Created" if mode == "new" else "Updated"
        await msg.edit_text(
            f"✅ {action}: `{path}`\n\n"
            f"[GitHub এ দেখো]({url})\n\n"
            f"নতুন file এর জন্য `/new filename.py` দাও।",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        del state[uid]
    else:
        await msg.edit_text(
            "❌ Failed. GH_TOKEN এর `repo` permission আছে কিনা চেক করো।"
        )

# ── BOOTSTRAP ─────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("new",     new_cmd))
    app.add_handler(CommandHandler("edit",    edit_cmd))
    app.add_handler(CommandHandler("rem",     rem_cmd))
    app.add_handler(CommandHandler("ls",      ls_cmd))
    app.add_handler(CommandHandler("cat",     cat_cmd))
    app.add_handler(CommandHandler("cancel",  cancel_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("[+] GitHub Manager Bot running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

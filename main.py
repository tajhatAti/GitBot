import os
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import base64
import httpx

TOKEN     = os.environ.get("BOT_TOKEN", "")
GH_TOKEN  = os.environ.get("GH_TOKEN", "")
GH_REPO   = os.environ.get("GH_REPO", "tajhatAti/Bot")
GH_BRANCH = os.environ.get("GH_BRANCH", "main")
OWNER_ID  = int(os.environ.get("OWNER_ID", 0))

GH_API = "https://api.github.com"

async def get_file_sha(path: str) -> str:
    url = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        if r.status_code == 200:
            return r.json().get("sha", "")
    return ""

async def upload_to_github(path: str, content: bytes, commit_msg: str) -> bool:
    url = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Content-Type": "application/json"
    }
    sha = await get_file_sha(path)
    data = {
        "message": commit_msg,
        "content": base64.b64encode(content).decode(),
        "branch": GH_BRANCH
    }
    if sha:
        data["sha"] = sha

    async with httpx.AsyncClient() as client:
        r = await client.put(url, headers=headers, json=data)
        return r.status_code in (200, 201)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    await update.message.reply_text(
        "**GitHub File Manager**\n\n"
        "File পাঠাও — filename হবে GitHub path\n"
        "যেমন: `plugins/ping.py` নামে file পাঠালে\n"
        "সেটা `plugins/ping.py` তে upload হবে\n\n"
        "Commands:\n"
        "/ls `<path>` — folder list\n"
        "/rm `<path>` — file delete\n"
        "/cat `<path>` — file content দেখো",
        parse_mode="Markdown"
    )

async def handle_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    doc = update.message.document
    if not doc:
        await update.message.reply_text("File পাঠাও।")
        return

    filename = doc.file_name
    caption  = update.message.caption or ""

    # Caption এ path দিলে সেটা use করবে
    # না দিলে filename ই path হবে
    gh_path = caption.strip() if caption.strip() else filename

    msg = await update.message.reply_text(f"Uploading `{gh_path}`...", parse_mode="Markdown")

    tg_file = await doc.get_file()
    content  = bytes(await tg_file.download_as_bytearray())

    success = await upload_to_github(
        path=gh_path,
        content=content,
        commit_msg=f"Update {gh_path} via Telegram bot"
    )

    if success:
        url = f"https://github.com/{GH_REPO}/blob/{GH_BRANCH}/{gh_path}"
        await msg.edit_text(
            f"✅ Uploaded!\n`{gh_path}`\n\n[View on GitHub]({url})",
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        await msg.edit_text("❌ Upload failed. Check GH_TOKEN permissions.")

async def ls_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    path = " ".join(ctx.args) if ctx.args else ""
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
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
    text = f"**/{path or 'root'}**\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown")

async def cat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not ctx.args:
        await update.message.reply_text("Usage: /cat `<path>`", parse_mode="Markdown")
        return
    path = " ".join(ctx.args)
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
    if r.status_code != 200:
        await update.message.reply_text("❌ File not found.")
        return
    content = base64.b64decode(r.json()["content"]).decode(errors="replace")
    if len(content) > 3500:
        content = content[:3500] + "\n...(truncated)"
    await update.message.reply_text(f"```\n{content}\n```", parse_mode="Markdown")

async def rm_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not ctx.args:
        await update.message.reply_text("Usage: /rm `<path>`", parse_mode="Markdown")
        return
    path = " ".join(ctx.args)
    sha  = await get_file_sha(path)
    if not sha:
        await update.message.reply_text("❌ File not found.")
        return
    url  = f"{GH_API}/repos/{GH_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "message": f"Delete {path} via Telegram bot",
        "sha": sha,
        "branch": GH_BRANCH
    }
    async with httpx.AsyncClient() as client:
        r = await client.delete(url, headers=headers, json=data)
    if r.status_code == 200:
        await update.message.reply_text(f"✅ Deleted: `{path}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Delete failed.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ls", ls_cmd))
    app.add_handler(CommandHandler("cat", cat_cmd))
    app.add_handler(CommandHandler("rm", rm_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    print("[+] Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()

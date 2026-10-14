"""Telegram AI study assistant using OpenRouter (polling mode)."""
import asyncio
import logging
import os
import sys
import time
from collections import defaultdict, deque

import httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          MessageHandler, filters)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # tránh log URL chứa token
log = logging.getLogger("bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free").strip()
ALLOW_PAID = os.getenv("ALLOW_PAID_MODEL", "false").lower() == "true"
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
USER_COOLDOWN = float(os.getenv("USER_COOLDOWN_SECONDS", "2"))
API_URL = "https://openrouter.ai/api/v1/chat/completions"
TG_LIMIT = 4000
MAX_INPUT = 8000

SYSTEM_PROMPT = (
    "Bạn là trợ lý AI thông minh, thân thiện, luôn trả lời bằng tiếng Việt tự nhiên "
    "(trừ khi người dùng yêu cầu ngôn ngữ khác, ví dụ học tiếng Anh). Bạn đặc biệt giỏi hỗ trợ "
    "học tập: Toán, Văn, Anh, Lý, Hóa, Sinh, Lịch sử, Địa lý, Tin học và các môn khác. "
    "Khi giải bài tập: trình bày từng bước rõ ràng, giải thích lý do, nêu đáp số cuối cùng. "
    "Có thể tạo đề ôn tập, câu hỏi trắc nghiệm (kèm đáp án và giải thích khi được yêu cầu). "
    "Bạn cũng trả lời kiến thức tổng quát, công nghệ, lập trình, đời sống. "
    "Viết công thức bằng ký tự thường dễ đọc trên Telegram (vd: x^2, sqrt(x)), tránh LaTeX phức tạp. "
    "Nếu không chắc chắn, hãy nói rõ thay vì bịa thông tin."
)

histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY))
last_call: dict[int, float] = {}
locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
http: httpx.AsyncClient | None = None


class AIError(Exception):
    def __init__(self, user_msg: str):
        super().__init__(user_msg)
        self.user_msg = user_msg


def is_free_model(model: str) -> bool:
    return model.endswith(":free") or model == "openrouter/free"


async def ask_ai(messages: list[dict]) -> str:
    assert http is not None
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/telegram-ai-bot",
        "X-Title": "Telegram AI Study Bot",
    }
    payload = {"model": OPENROUTER_MODEL, "messages": messages}
    if not ALLOW_PAID:
        payload["max_price"] = {"prompt": 0, "completion": 0}  # chặn phát sinh chi phí
    for attempt in range(2):
        try:
            r = await http.post(API_URL, json=payload, headers=headers)
        except httpx.TimeoutException:
            raise AIError("⏳ AI phản hồi quá lâu. Bạn thử lại sau ít phút nhé.")
        except httpx.HTTPError as e:
            log.warning("Lỗi mạng OpenRouter: %s", type(e).__name__)
            raise AIError("🌐 Không kết nối được tới dịch vụ AI. Vui lòng thử lại sau.")
        if r.status_code == 200:
            try:
                data = r.json()
                if "error" in data:
                    log.warning("OpenRouter error body: %s", str(data["error"])[:300])
                    raise AIError("⚠️ Mô hình AI gặp lỗi khi xử lý. Bạn thử lại nhé.")
                text = (data["choices"][0]["message"].get("content") or "").strip()
            except (ValueError, KeyError, IndexError):
                raise AIError("⚠️ Phản hồi từ AI không hợp lệ. Bạn thử lại nhé.")
            if not text:
                raise AIError("🤔 AI không trả lời được câu này. Bạn diễn đạt lại thử nhé.")
            return text
        detail = r.text[:300]
        log.warning("OpenRouter HTTP %s: %s", r.status_code, detail)
        if r.status_code == 429:
            if attempt == 0:
                await asyncio.sleep(3)
                continue
            raise AIError("🚦 Mô hình miễn phí đang quá tải hoặc bạn đã hết hạn mức hôm nay. "
                          "Vui lòng thử lại sau ít phút (hoặc ngày mai).")
        if r.status_code == 401:
            raise AIError("🔑 Bot chưa được cấu hình khóa AI hợp lệ. Hãy báo quản trị viên.")
        if r.status_code == 402:
            raise AIError("💳 Tài khoản AI đã hết hạn mức. Bot sẽ không tự phát sinh chi phí. "
                          "Vui lòng thử lại sau.")
        if r.status_code in (404, 400) and ("model" in detail.lower() or "endpoint" in detail.lower()):
            raise AIError("🚫 Mô hình AI hiện không khả dụng. Hãy báo quản trị viên đổi mô hình miễn phí khác.")
        if r.status_code == 400:
            raise AIError("📏 Nội dung quá dài hoặc không hợp lệ. Hãy /reset hoặc rút gọn câu hỏi.")
        if r.status_code >= 500 and attempt == 0:
            await asyncio.sleep(2)
            continue
        raise AIError("⚠️ Dịch vụ AI đang gặp sự cố. Bạn thử lại sau nhé.")
    raise AIError("⚠️ Dịch vụ AI đang bận. Bạn thử lại sau nhé.")


def split_text(text: str, limit: int = TG_LIMIT) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


async def send_long(update: Update, text: str) -> None:
    for chunk in split_text(text):
        for _ in range(3):
            try:
                await update.effective_message.reply_text(chunk)
                break
            except RetryAfter as e:
                await asyncio.sleep(float(e.retry_after) + 1)
        await asyncio.sleep(0.3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name if update.effective_user else "bạn"
    await update.effective_message.reply_text(
        f"👋 Xin chào {name}! Mình là trợ lý AI hỗ trợ học tập và giải đáp mọi chủ đề.\n\n"
        "📚 Mình giúp được: Toán, Văn, Anh, Lý, Hóa, Sinh, Sử, Địa, Tin học...\n"
        "✍️ Giải bài từng bước, tạo đề ôn tập, câu hỏi trắc nghiệm.\n"
        "💡 Kiến thức tổng quát, công nghệ, lập trình, đời sống.\n\n"
        "Cứ gửi câu hỏi nhé! Gõ /help để xem hướng dẫn.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "📖 Hướng dẫn sử dụng\n\n"
        "/start – Giới thiệu bot\n/help – Xem hướng dẫn\n/reset – Xóa lịch sử, bắt đầu chủ đề mới\n\n"
        "Ví dụ:\n• Giải phương trình x^2 - 5x + 6 = 0 từng bước\n"
        "• Tạo 10 câu trắc nghiệm Sinh học lớp 10 về tế bào\n"
        "• Phân tích bài thơ Tây Tiến\n• Giải thích vòng lặp for trong Python\n\n"
        "Mình nhớ ngữ cảnh cuộc trò chuyện. Đổi chủ đề thì gõ /reset nhé.")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    histories.pop(update.effective_chat.id, None)
    await update.effective_message.reply_text("🧹 Đã xóa lịch sử hội thoại. Bắt đầu chủ đề mới thôi!")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return
    chat_id = update.effective_chat.id
    text = msg.text.strip()
    if len(text) > MAX_INPUT:
        await msg.reply_text(f"📏 Tin nhắn quá dài (tối đa {MAX_INPUT} ký tự). Hãy chia nhỏ nhé.")
        return
    now = time.monotonic()
    if now - last_call.get(chat_id, 0) < USER_COOLDOWN or locks[chat_id].locked():
        await msg.reply_text("⏱ Bạn gửi hơi nhanh, đợi mình trả lời xong câu trước nhé.")
        return
    last_call[chat_id] = now
    async with locks[chat_id]:
        hist = histories[chat_id]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *hist, {"role": "user", "content": text}]
        try:
            await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        except TelegramError:
            pass
        try:
            answer = await ask_ai(messages)
        except AIError as e:
            await msg.reply_text(e.user_msg)
            return
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": answer})
        await send_long(update, answer)


async def on_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("📝 Hiện mình chỉ đọc được tin nhắn văn bản. Bạn gõ câu hỏi nhé!")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Lỗi không mong muốn: %s", type(context.error).__name__, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("😥 Có lỗi xảy ra. Bạn thử lại sau nhé.")
        except TelegramError:
            pass


async def post_init(app: Application) -> None:
    global http
    http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))
    log.info("Bot đã khởi động. Model: %s", OPENROUTER_MODEL)


async def post_shutdown(app: Application) -> None:
    if http:
        await http.aclose()


def main() -> None:
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
                              ("OPENROUTER_API_KEY", OPENROUTER_API_KEY)) if not v]
    if missing:
        log.error("Thiếu biến môi trường: %s", ", ".join(missing))
        sys.exit(1)
    if not ALLOW_PAID and not is_free_model(OPENROUTER_MODEL):
        log.error("OPENROUTER_MODEL '%s' không phải mô hình miễn phí (cần đuôi ':free'). "
                  "Dừng để tránh phát sinh chi phí.", OPENROUTER_MODEL)
        sys.exit(1)
    app = (Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, on_other))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()

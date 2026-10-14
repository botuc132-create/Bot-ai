# 🤖 Bot Telegram AI hỗ trợ học tập (OpenRouter + Railway)

Trợ lý AI tiếng Việt: giải bài từng bước, tạo đề ôn, trắc nghiệm, kiến thức tổng quát, lập trình. Nhớ ngữ cảnh riêng cho từng người dùng. Lệnh: `/start`, `/help`, `/reset`.

## Cấu trúc
| File | Mô tả |
|---|---|
| bot.py | Mã nguồn bot |
| requirements.txt | Thư viện (python-telegram-bot 21.6, httpx) |
| Procfile / railway.json | Cấu hình chạy worker trên Railway (1 bản duy nhất) |
| .python-version | Python 3.11 |
| .env.example | Mẫu biến môi trường (giá trị giả) |

## Bước 1 – Tạo bot Telegram
1. Mở Telegram, tìm **@BotFather** → gửi `/newbot`.
2. Đặt tên và username (kết thúc bằng `bot`).
3. BotFather trả về **token** – giữ bí mật, không gửi cho ai.
4. (Tùy chọn) `/setcommands` → dán:
```
start - Giới thiệu bot
help - Hướng dẫn sử dụng
reset - Xóa lịch sử hội thoại
```

## Bước 2 – Tạo OpenRouter API key
1. Đăng ký tại https://openrouter.ai → **Keys** → **Create Key**.
2. Khuyến nghị: đặt **Credit limit = 0** cho key để chắc chắn không phát sinh chi phí.
3. Chọn mô hình miễn phí tại https://openrouter.ai/models?max_price=0 (tên có đuôi `:free`), ví dụ `meta-llama/llama-3.3-70b-instruct:free`, `deepseek/deepseek-chat-v3-0324:free`, `google/gemma-3-27b-it:free`. Danh sách mô hình free thay đổi theo thời gian, hãy kiểm tra trước.

> Bot từ chối khởi động nếu mô hình không có đuôi `:free`, và gửi `max_price = 0` trong mỗi yêu cầu → không tự chuyển sang mô hình trả phí.

## Bước 3 – Đưa code lên GitHub
```bash
git init && git add . && git commit -m "Telegram AI bot"
git branch -M main
git remote add origin https://github.com/<ban>/<repo>.git
git push -u origin main
```
`.gitignore` đã loại `.env` – **không bao giờ commit khóa thật**.

## Bước 4 – Triển khai Railway
1. https://railway.app → **New Project** → **Deploy from GitHub repo** → chọn repo.
2. Vào service → tab **Variables** → thêm:
   - `TELEGRAM_BOT_TOKEN` = token từ BotFather
   - `OPENROUTER_API_KEY` = key OpenRouter
   - `OPENROUTER_MODEL` = tên mô hình `:free`
3. Tab **Settings**: đảm bảo **Start Command** là `python bot.py` (đã có trong railway.json), **Replicas = 1**. Không cần mở domain/port vì bot dùng polling.
4. Railway tự build và chạy lại khi bạn lưu Variables.

⚠️ **Chỉ chạy 1 bản bot với mỗi token.** Không chạy đồng thời trên máy tính và Railway, không tăng replicas – nếu không sẽ gặp lỗi `Conflict: terminated by other getUpdates request`.

## Bước 5 – Xem log & kiểm tra
- Tab **Deployments** → chọn bản mới nhất → **View Logs**.
- Thấy `Bot đã khởi động. Model: ...` là thành công. Mở bot trên Telegram, gửi `/start`.
- Log không bao giờ in token/API key.

## Chạy thử trên máy (tùy chọn)
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # điền khóa thật vào .env
set -a && source .env && set +a
python bot.py
```
Nhớ tắt bản chạy trên máy trước khi chạy trên Railway.

## Xử lý sự cố
| Thông báo | Nguyên nhân / cách xử lý |
|---|---|
| Thiếu biến môi trường | Chưa thêm Variables trên Railway |
| không phải mô hình miễn phí | Đổi `OPENROUTER_MODEL` sang tên có `:free` |
| 🚦 quá tải / hết hạn mức | Giới hạn của mô hình free (theo phút/ngày) – đợi rồi thử lại |
| 🚫 Mô hình không khả dụng | Mô hình free bị gỡ – chọn mô hình free khác |
| 🔑 khóa AI không hợp lệ | Kiểm tra lại `OPENROUTER_API_KEY` |
| Conflict getUpdates | Đang có 2 bản bot chạy cùng token |

## Ghi chú
- Lịch sử hội thoại lưu trong bộ nhớ (mất khi bot khởi động lại), giữ tối đa `MAX_HISTORY` tin gần nhất.
- Tin trả lời dài được tự động chia nhỏ theo giới hạn Telegram.

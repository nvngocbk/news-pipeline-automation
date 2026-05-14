# chiasegpu.vn — LLM Inference API

Tài liệu tham khảo các endpoint inference của chiasegpu.vn (tương thích OpenAI / Anthropic). Dùng làm gateway gọi LLM, TTS, embedding, image gen.

> **Lưu ý bảo mật**: token có dạng `sk-...` mang full quyền. KHÔNG paste token thật vào file này hoặc bất kỳ file nào trong repo. Đặt token vào biến môi trường `CHIASEGPU_API_KEY` và đọc từ code. Nếu lỡ commit token, **thu hồi ngay** ở console chiasegpu, tạo token mới, và rewrite git history.

## Base URLs

- `https://llm.chiasegpu.vn/v1` — chat, responses, messages, embeddings, audio, video, models
- `https://llm-2.chiasegpu.vn/v1` — images (gen + edit)

Header xác thực dùng chung cho mọi endpoint:

```
Authorization: Bearer $CHIASEGPU_API_KEY
Content-Type: application/json
```

## Endpoints

| Method | Endpoint | Tương thích | Mục đích |
|---|---|---|---|
| POST | `llm.chiasegpu.vn/v1/chat/completions` | OpenAI Chat | Chat / tóm tắt / dịch |
| POST | `llm.chiasegpu.vn/v1/responses` | OpenAI Responses | Chat (API mới hơn) |
| POST | `llm.chiasegpu.vn/v1/messages` | Anthropic | Cú pháp Claude |
| POST | `llm.chiasegpu.vn/v1/embeddings` | OpenAI | Embedding vector |
| POST | `llm-2.chiasegpu.vn/v1/images/generations` | OpenAI Images | Sinh ảnh |
| POST | `llm-2.chiasegpu.vn/v1/images/edits` | OpenAI Images | Chỉnh ảnh |
| POST | `llm.chiasegpu.vn/v1/audio/speech` | OpenAI TTS | Text → audio |
| POST | `llm.chiasegpu.vn/v1/audio/transcriptions` | OpenAI Whisper | Audio → text |
| POST | `llm.chiasegpu.vn/v1/audio/translations` | OpenAI Whisper | Audio → text (dịch) |
| GET  | `llm.chiasegpu.vn/v1/videos` | (custom) | List video |
| GET  | `llm.chiasegpu.vn/v1/videos/:id` | (custom) | Chi tiết video |
| GET  | `llm.chiasegpu.vn/v1/videos/:id/content` | (custom) | Tải nội dung video |
| GET  | `llm.chiasegpu.vn/v1/models` | OpenAI | Danh sách model có sẵn |

## Models

Đã xác nhận có:

- `gpt-5.5`
- `gpt-5.4`

Tên model cho TTS / embedding / image gen chưa rõ — gọi `GET /v1/models` để lấy danh sách đầy đủ.

## Ví dụ

### cURL — Chat completion (OpenAI)

```bash
curl https://llm.chiasegpu.vn/v1/chat/completions \
  -H "Authorization: Bearer $CHIASEGPU_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"Hello!"}]}'
```

### cURL — Responses API (OpenAI)

```bash
curl https://llm.chiasegpu.vn/v1/responses \
  -H "Authorization: Bearer $CHIASEGPU_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","input":"Hello!"}'
```

### Python — Chat (openai SDK)

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["CHIASEGPU_API_KEY"],
    base_url="https://llm.chiasegpu.vn/v1",
)

response = client.chat.completions.create(
    model="gpt-5.5",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

### Python — Responses (openai SDK)

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["CHIASEGPU_API_KEY"],
    base_url="https://llm.chiasegpu.vn/v1",
)

response = client.responses.create(
    model="gpt-5.5",
    input="Hello!",
)
print(response.output_text)
```

## Đã tích hợp vào pipeline

VN pipeline ([news/scripts/run_vn_news_dynamic.py](news/scripts/run_vn_news_dynamic.py)) đã dùng `chat/completions` để viết lại `summary_vi` trước khi đưa vào TTS:

- Sau `pick_stories()` chọn xong 8-10 tin, mỗi tin được fetch full HTML qua [news/core/article_extract.py](news/core/article_extract.py) (`trafilatura`).
- Body được gửi sang [news/core/ai_summarize.py](news/core/ai_summarize.py) → gọi `gpt-5.5` viết lại thành đoạn 3-5 câu kiểu bản tin.
- Nếu fetch fail hoặc AI fail → giữ nguyên RSS `description`, log `[ai] story-NN ...`, pipeline không gãy.
- `metadata.json` ghi `summary_source: "ai" | "rss"` cho từng tin để audit.
- Anti-repeat (`tokens`, `cluster_keys`, `category`) vẫn dùng RSS gốc, không bị ảnh hưởng bởi rewrite.

Cách 1 — file `.env` ở repo root (đã trong `.gitignore`):

```bash
cp .env.example .env
chmod 600 .env
# Mở .env, dán giá trị thật vào CHIASEGPU_API_KEY=sk-...
```

Cách 2 — export trực tiếp trong shell / cron (luôn ưu tiên hơn `.env`):

```bash
export CHIASEGPU_API_KEY="sk-..."   # KHÔNG commit vào repo
```

Tuỳ chọn override (cùng đặt được trong `.env` hoặc shell): `CHIASEGPU_MODEL` (default `gpt-5.5`), `AI_SUMMARY_ENABLED=0` để tắt, `AI_SUMMARY_TIMEOUT` (30s), `AI_SUMMARY_MAX_BODY_CHARS` (8000).

World pipeline ([news/scripts/run_world_news_dynamic.py](news/scripts/run_world_news_dynamic.py)) **chưa tích hợp** — sẽ làm sau khi VN chạy ổn định.

## Ý tưởng tích hợp khác (chưa làm)

| Endpoint | Có thể thay/bổ sung cho | Lợi ích |
|---|---|---|
| `chat/completions` | Google Translate REST trong World pipeline | Dịch EN→VI mượt hơn (thay vì 2 bước: dịch + AI rewrite) |
| `embeddings` | Anti-repeat token-overlap | Bắt được tin trùng dù khác headline / khác cluster_keys (đúng vụ Dân Trí kiểu "Thủ tướng: …") |
| `audio/speech` | Google Cloud TTS (`vi-VN-Neural2-A`) | Bỏ phụ thuộc service account Google — cần test chất lượng giọng Việt trước |
| `images/generations` | Khi RSS không có ảnh (hiện tại `continue` bỏ tin) | Tỉ lệ pick-up tin cao hơn |

Khi tích hợp, đọc token qua `os.environ["CHIASEGPU_API_KEY"]`, **không** hardcode trong code.

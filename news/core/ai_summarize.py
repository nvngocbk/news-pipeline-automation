"""Summarize a single news article into a bulletin-ready Vietnamese paragraph.

Calls chiasegpu's OpenAI-compatible chat completion endpoint. Designed to fail
silently: any error (missing key, network, API, parsing) returns None so the
caller falls back to the RSS description and the bulletin still ships.

Env vars (all optional; missing API key disables AI):
  CHIASEGPU_API_KEY        — required for AI to run
  CHIASEGPU_BASE_URL       — default https://llm.chiasegpu.vn/v1
  CHIASEGPU_MODEL          — default gpt-5.5
  AI_SUMMARY_ENABLED       — set to "0" to force-disable even with key present
  AI_SUMMARY_TIMEOUT       — request timeout in seconds, default 30
  AI_SUMMARY_MAX_BODY_CHARS — truncate article body before sending, default 8000
"""

from __future__ import annotations

import logging
import os

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

log = logging.getLogger(__name__)

_BASE_URL = os.environ.get('CHIASEGPU_BASE_URL', 'https://llm.chiasegpu.vn/v1')
_MODEL = os.environ.get('CHIASEGPU_MODEL', 'gpt-5.5')
_TIMEOUT = int(os.environ.get('AI_SUMMARY_TIMEOUT', '30'))
_MAX_BODY = int(os.environ.get('AI_SUMMARY_MAX_BODY_CHARS', '8000'))

_SYSTEM_PROMPT = (
    "Bạn là biên tập viên bản tin phát thanh tiếng Việt. "
    "Nhiệm vụ: viết lại bài báo dưới đây thành đoạn bản tin ngắn để phát thanh viên đọc trên truyền hình."
)

_USER_TEMPLATE = (
    "Yêu cầu bắt buộc:\n"
    "- CHỈ dùng thông tin có trong bài. Không bịa số liệu, tên người, địa danh, ngày tháng.\n"
    "- Viết 3 đến 5 câu hoàn chỉnh, tổng khoảng 60-110 từ.\n"
    "- Văn phong bản tin: trang trọng, trung tính, ngôi thứ ba. Không xưng 'tôi/chúng tôi/mình'.\n"
    "- Không đặt câu hỏi tu từ. Không có lời chào, lời dẫn ('Kính thưa…'), hoặc đoạn kết ('Trên đây là…').\n"
    "- Không nhắc tên báo nguồn trong nội dung.\n"
    "- Ngày, giờ, thời điểm phải viết ra chữ để TTS đọc đúng:\n"
    "    '12 giờ 30 phút ngày 12 tháng 12 năm 2025' — KHÔNG dùng dạng '12-12-2025', '12/12/2025', '2025-12-12', '06/05', '06:05'.\n"
    "    Số thứ tự / số đo lớn cũng nên đọc tự nhiên (vd '5 nghìn tỷ đồng' thay vì '5.000.000.000.000đ').\n"
    "- Trả về thuần đoạn văn, không bullet, không tiêu đề, không markdown.\n\n"
    "Tiêu đề bài báo:\n{title}\n\n"
    "Nội dung bài báo:\n{body}\n\n"
    "Đoạn bản tin:"
)


def _enabled() -> bool:
    if OpenAI is None:
        return False
    if os.environ.get('AI_SUMMARY_ENABLED', '1') == '0':
        return False
    return bool(os.environ.get('CHIASEGPU_API_KEY'))


_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not _enabled():
        return None
    try:
        _client = OpenAI(
            api_key=os.environ['CHIASEGPU_API_KEY'],
            base_url=_BASE_URL,
            timeout=_TIMEOUT,
        )
    except Exception as exc:
        log.warning('OpenAI client init failed: %s', exc)
        _client = None
    return _client


def summarize_for_bulletin(title: str, body: str) -> str | None:
    """Return an AI-written bulletin paragraph, or None on any failure."""
    client = _get_client()
    if client is None:
        return None
    if not title and not body:
        return None
    truncated = (body or '')[:_MAX_BODY]
    user_msg = _USER_TEMPLATE.format(title=title or '', body=truncated)
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': user_msg},
            ],
            temperature=0.3,
        )
    except Exception as exc:
        log.warning('chat.completions failed: %s', exc)
        return None
    try:
        text = resp.choices[0].message.content
    except Exception:
        return None
    if not text:
        return None
    text = text.strip()
    if not text.endswith(('.', '!', '?')):
        text += '.'
    return text

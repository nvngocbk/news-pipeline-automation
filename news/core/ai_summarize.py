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

import json as _json
import logging
import os
import re as _re
from datetime import datetime, timezone

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


_HOT_SIGNALS = frozenset({
    # pháp lý / hình sự
    'bắt', 'khởi tố', 'điều tra', 'xét xử', 'tuyên án', 'truy nã', 'triệt phá',
    'lừa đảo', 'tham nhũng', 'đấu thầu', 'đề nghị truy tố',
    # sự cố khẩn cấp
    'cháy', 'nổ', 'tai nạn', 'thương vong', 'thiên tai', 'bão', 'áp thấp',
    'ngập', 'dịch', 'ngộ độc', 'cảnh báo', 'khẩn cấp', 'sập', 'chìm',
    # chính sách tác động ngay
    'tăng giá', 'thu hồi', 'đình chỉ', 'xử phạt', 'cấm', 'siết', 'dừng',
    # chính trị / nhân sự
    'bổ nhiệm', 'phê chuẩn', 'quốc hội', 'bầu', 'miễn nhiệm',
})


def _hot_count(text: str) -> int:
    low = text.lower()
    return sum(1 for kw in _HOT_SIGNALS if kw in low)


def select_stories(candidates: list, target_n: int, focus_keywords: list | None = None) -> list[int] | None:
    """Ask AI to pick the best target_n stories from a candidate pool.

    candidates: list of story dicts with 'headline_vi', 'summary_vi', 'category',
    optionally 'pub_dt' (datetime) and 'source_name' (str).
    Returns a list of target_n 0-based indices into candidates, ordered by broadcast
    priority, or None on any failure (caller should fall back to algorithmic pick).
    """
    client = _get_client()
    if client is None:
        return None
    if not candidates:
        return None

    now_utc = datetime.now(timezone.utc)
    lines = []
    for i, c in enumerate(candidates):
        headline = (c.get('headline_vi') or '').strip()
        desc = (c.get('summary_vi') or '').strip()[:220]
        category = c.get('category', 'khác')
        source = (c.get('source_name') or '').strip()
        hot = _hot_count(headline + ' ' + desc)

        pub_dt = c.get('pub_dt')
        age_str = ''
        if pub_dt:
            try:
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                age_h = (now_utc - pub_dt.astimezone(timezone.utc)).total_seconds() / 3600
                age_str = f"{age_h:.0f}h ago"
            except Exception:
                pass

        meta = ' | '.join(filter(None, [category, source, age_str, f"hot={hot}" if hot else None]))
        lines.append(f"[{i}] ({meta}) {headline} — {desc}")

    candidates_text = '\n'.join(lines)
    focus_note = ''
    if focus_keywords:
        focus_note = (
            f"\n⚡ YÊU CẦU ĐẶC BIỆT: Ưu tiên tối đa tin liên quan đến "
            f"'{', '.join(focus_keywords)}'. Nếu đủ tin khớp, lineup phải phản ánh chủ đề này.\n"
        )

    prompt = (
        "Bạn là tổng biên tập bản tin thời sự truyền hình Việt Nam.\n"
        f"Từ {len(candidates)} tin ứng viên dưới đây, chọn đúng {target_n} tin để phát sóng tối nay.{focus_note}\n\n"

        "=== THANG ƯU TIÊN (xét theo thứ tự, tier trên luôn thắng tier dưới) ===\n\n"

        "TIER 1 — SỰ CỐ KHẨN CẤP (ưu tiên tuyệt đối nếu có):\n"
        "  Cháy, nổ lớn; tai nạn giao thông/lao động nhiều thương vong; thiên tai (bão, lũ, sạt lở);\n"
        "  dịch bệnh bùng phát; ngộ độc thực phẩm hàng loạt; sập công trình.\n\n"

        "TIER 2 — SỰ KIỆN PHÁP LÝ / HÌNH SỰ:\n"
        "  Bắt giữ, khởi tố, xét xử, tuyên án — đặc biệt cán bộ cấp cao hoặc vụ án quy mô lớn;\n"
        "  triệt phá đường dây tội phạm, ma túy, lừa đảo; điều tra tham nhũng.\n\n"

        "TIER 3 — CHÍNH SÁCH TÁC ĐỘNG TRỰC TIẾP ĐỜI SỐNG:\n"
        "  Tăng/giảm giá đột ngột (điện, xăng, viện phí, học phí); thu hồi sản phẩm;\n"
        "  lệnh cấm hoặc đình chỉ vừa ban hành; siết quy định mới có hiệu lực sớm.\n\n"

        "TIER 4 — CHÍNH TRỊ / NHÂN SỰ / ĐỐI NGOẠI CẤP CAO:\n"
        "  Quốc hội phê chuẩn; bổ nhiệm/miễn nhiệm lãnh đạo cấp bộ trở lên;\n"
        "  quyết định quan trọng của Chính phủ/Đảng; hội đàm, hiệp ước quốc tế.\n\n"

        "=== QUY TẮC PHỤ ===\n"
        "• Tiebreaker trong cùng tier: ưu tiên hot=N cao hơn → age nhỏ hơn → nguồn thời sự chuyên biệt.\n"
        "• Đa dạng: không chọn quá 2 tin cùng category.\n"
        "• Loại bỏ dứt khoát: bình luận/phân tích không có sự kiện mới hôm nay; evergreen;\n"
        "  tin lifestyle, giải trí, mẹo vặt, quảng cáo ẩn; hội thảo/triển lãm thông thường.\n"
        "• Sắp xếp lineup: tin nóng nhất / quan trọng nhất phát đầu tiên.\n\n"

        f"=== DANH SÁCH ỨNG VIÊN ===\n{candidates_text}\n\n"

        f"Trả về JSON array gồm đúng {target_n} số nguyên là chỉ số (0-based) của các tin được chọn,\n"
        "sắp xếp theo thứ tự phát sóng (quan trọng nhất trước).\n"
        "Ví dụ: [3, 0, 12, 7, 5]\n"
        "Chỉ trả về JSON array, không kèm giải thích."
    )

    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
        )
        text = resp.choices[0].message.content.strip()
    except Exception as exc:
        log.warning('[ai-select] chat.completions failed: %s', exc)
        return None

    try:
        m = _re.search(r'\[[\d,\s]+\]', text)
        if not m:
            log.warning('[ai-select] no JSON array in response: %.200s', text)
            return None
        indices = _json.loads(m.group(0))
        if not isinstance(indices, list):
            return None
        valid = [i for i in indices if isinstance(i, int) and 0 <= i < len(candidates)]
        seen: set[int] = set()
        deduped: list[int] = []
        for i in valid:
            if i not in seen:
                seen.add(i)
                deduped.append(i)
        if len(deduped) < target_n:
            log.warning('[ai-select] only %d valid unique indices, need %d', len(deduped), target_n)
            return None
        return deduped[:target_n]
    except Exception as exc:
        log.warning('[ai-select] parse failed: %s', exc)
        return None


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

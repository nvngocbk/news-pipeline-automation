import html
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from google.cloud import texttospeech

from news.core.dotenv import load_dotenv

# Load .env from repo root BEFORE importing modules that read env at import
# time (ai_summarize captures CHIASEGPU_* at import). Existing shell/cron env
# always wins — see news/core/dotenv.py.
load_dotenv(Path(__file__).resolve().parents[2] / '.env')

from news.core.runtime import get_runtime, vn_video_filename
from news.core.article_extract import fetch_article_body
from news.core.ai_summarize import summarize_for_bulletin, select_stories

runtime = get_runtime()
RUN_DATE = runtime.run_date
RUN_HHMM = runtime.run_hhmm
RUN_HOUR = runtime.spoken_hour_vi
CURRENT_RUN_KEY = f"{RUN_DATE}-{RUN_HHMM}"
BASE = Path('/home/nv-ngoc/.openclaw/workspace/news-videos-vn')
RUN_DIR = BASE / RUN_DATE / RUN_HHMM
PREP_DIR = RUN_DIR / 'prepared'
SRC_DIR = RUN_DIR / 'source-images'
TMP_DIR = RUN_DIR / 'tmp'
RUN_DIR.mkdir(parents=True, exist_ok=True)
PREP_DIR.mkdir(exist_ok=True)
SRC_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)
HISTORY_PATH = Path('/home/nv-ngoc/.openclaw/workspace/news-vn-history.jsonl')
HISTORY_MAX_LINES = int(os.environ.get('HISTORY_MAX_LINES', '1500'))

FEEDS = [
    # Desk-specific hard-news feeds first so dedup-by-link prefers them
    ('VnExpress-Thời sự', 'https://vnexpress.net/rss/thoi-su.rss'),
    ('Tuổi Trẻ-Thời sự', 'https://tuoitre.vn/rss/thoi-su.rss'),
    ('VietnamNet-Thời sự', 'https://vietnamnet.vn/rss/thoi-su.rss'),
    ('VietnamPlus', 'https://www.vietnamplus.vn/rss/tin-moi-nhat.rss'),
    # Firehose feeds last — used only to fill gaps after desk feeds
    ('VnExpress', 'https://vnexpress.net/rss/tin-moi-nhat.rss'),
    ('Tuổi Trẻ', 'https://tuoitre.vn/rss/tin-moi-nhat.rss'),
    ('VietnamNet', 'https://vietnamnet.vn/rss/home.rss'),
]

FOCUS_FEEDS = [
    ('VnExpress-Thời sự', 'https://vnexpress.net/rss/thoi-su.rss'),
    ('Tuổi Trẻ-Thời sự', 'https://tuoitre.vn/rss/thoi-su.rss'),
    ('VietnamNet-Thời sự', 'https://vietnamnet.vn/rss/thoi-su.rss'),
]

STOPWORDS = {
    'và', 'của', 'với', 'các', 'những', 'được', 'theo', 'khi', 'trong', 'trên', 'sau', 'đến', 'cho', 'tại',
    'người', 'việt', 'nam', 'hà', 'nội', 'tp', 'hcm', 'tphcm', 'việc', 'một', 'nhiều', 'đã', 'sẽ', 'đang',
    'vừa', 'lại', 'hơn', 'từ', 'này', 'kia', 'đó', 'ra', 'bị', 'về', 'có', 'không', 'lúc', 'giờ', 'ngày',
    'tháng', 'năm', 'ở', 'do', 'số', 'sau', 'trước', 'giữa', 'mới', 'rất', 'cần', 'thêm'
}

BLACKLIST_KEYWORDS = {
    'onsen', 'ecopark', 'khuyến mãi', 'ưu đãi', 'mỹ phẩm', 'làm đẹp', 'da lão hóa', 'quán cà phê',
    'bắt cá', 'hái nho', 'forest onsen', 'du lịch', 'giải trí', 'showbiz', 'hoa hậu', 'bóng đá',
    'xe máy điện', 'cà phê vườn', 'ung thư vú', 'bác sĩ chỉ ra', 'thói quen', 'sản phẩm',
    'mẹo vặt', 'bí quyết', 'cách làm', 'review', 'trải nghiệm', 'giảm cân', 'detox', 'làm giàu nhanh',
    'tử vi', 'cung hoàng đạo', 'phong thủy', 'bói', 'soi kèo', 'nhận định bóng đá',
    'esports', 'livestream', 'fan hâm mộ', 'xe sang', 'siêu xe', 'concept car',
    'top 5', 'top 10', 'top 3', 'điểm danh', 'gợi ý món', 'thực đơn',
    'công thức', 'chăm sóc da', 'giữ dáng', 'drama', 'tin đồn', 'lộ ảnh',
    'street style', 'diện đồ', 'mặc gì', 'trailer', 'teaser', 'mv mới',
    'khai trương', 'ra mắt dự án', 'ra mắt sản phẩm',
}

VIRAL_KEYWORDS = {
    'bắt', 'khởi tố', 'xét xử', 'tuyên án', 'điều tra', 'lừa đảo', 'tham nhũng', 'đấu thầu',
    'tăng giá', 'giảm giá', 'căng thẳng', 'tranh cãi', 'phản ứng', 'biểu tình', 'tai nạn',
    'cháy', 'nổ', 'đâm', 'vụ án', 'clip', 'rò rỉ', 'cảnh báo', 'dừng', 'cấm', 'đình chỉ',
    'truy nã', 'triệt phá', 'đột kích', 'phong tỏa', 'khẩn cấp', 'đề nghị truy tố', 'thu hồi', 'xử phạt'
}

CONTROVERSY_KEYWORDS = {
    'tranh cãi', 'phản ứng', 'bức xúc', 'phẫn nộ', 'làn sóng', 'gây sốc', 'chỉ trích', 'phản đối',
    'cấm', 'đình chỉ', 'đề xuất', 'tăng giá', 'giảm giá', 'khẩn cấp', 'nghi vấn', 'bất thường'
}

SOFT_NEWS_KEYWORDS = {
    'mẹo', 'bí quyết', 'ăn gì', 'mặc gì', 'check-in', 'đi đâu', 'du lịch', 'review', 'trải nghiệm',
    'showbiz', 'hoa hậu', 'lifestyle', 'ẩm thực', 'giải trí', 'tử vi', 'cung hoàng đạo', 'mua sắm'
}

HOT_NEWS_KEYWORDS = {
    'bắt', 'khởi tố', 'điều tra', 'xét xử', 'tuyên án', 'lừa đảo', 'tham nhũng', 'truy nã', 'triệt phá',
    'tai nạn', 'cháy', 'nổ', 'thương vong', 'cảnh báo', 'khẩn cấp', 'dịch', 'ngộ độc', 'thu hồi',
    'tăng giá', 'siết', 'đình chỉ', 'xử phạt', 'cấm', 'đấu thầu', 'bất thường', 'áp thấp', 'bão', 'ngập'
}

URL_PATH_BLOCKLIST = (
    '/giai-tri/', '/the-thao/', '/bong-da/', '/doi-song/', '/am-thuc/',
    '/du-lich/', '/lam-dep/', '/thoi-trang/', '/xe/', '/oto-xemay/', '/oto/',
    '/showbiz/', '/nhip-song-tre/', '/song-khoe/', '/tinh-yeu-gioi-tinh/',
    '/goc-nhin/', '/tam-su/', '/cuoi/', '/esport/', '/game/', '/tu-van/',
    '/phim/', '/nhac/', '/sao/', '/ngoi-sao/', '/blog/', '/cam-nang/',
    '/the-gioi/',  # VN pipeline excludes world — covered by world pipeline
)

# Event tokens for cluster-signature anti-repeat. Ordered roughly by specificity
# (concrete incidents first, broader policy/economic signals last) so
# extract_event_keyword returns the most incident-like token when multiple match.
CLUSTER_EVENT_KEYWORDS = (
    'cháy', 'nổ', 'tai nạn', 'thương vong', 'chìm', 'sập', 'đâm',
    'bắt', 'khởi tố', 'truy nã', 'triệt phá', 'điều tra', 'xét xử', 'tuyên án',
    'lừa đảo', 'tham nhũng', 'đấu thầu', 'đình chỉ', 'xử phạt',
    'ngộ độc', 'thu hồi', 'dịch', 'áp thấp', 'bão', 'ngập',
    'cảnh báo', 'khẩn cấp', 'tăng giá', 'giảm giá', 'siết', 'cấm',
)

# Place/entity names too broad to serve as a cluster key on their own. If a
# headline mentions only these, we fall back to token-overlap instead of
# cluster-signature (avoids blocking unrelated events in the same major city).
CLUSTER_NOUN_BLOCKLIST = {
    'hà nội', 'tp hcm', 'tp hồ chí minh', 'hồ chí minh', 'tp. hcm',
    'việt nam', 'đà nẵng', 'hải phòng', 'cần thơ',
    'miền bắc', 'miền trung', 'miền nam',
}

_VN_CAP_CHARS = (
    'A-Z'
    'ÀÁẠẢÃĂẮẰẲẴẶÂẤẦẨẪẬ'
    'Đ'
    'ÈÉẸẺẼÊẾỀỂỄỆ'
    'ÌÍỊỈĨ'
    'ÒÓỌỎÕÔỐỒỔỖỘƠỚỜỞỠỢ'
    'ÙÚỤỦŨƯỨỪỬỮỰ'
    'ỲÝỴỶỸ'
)
_PROPER_NOUN_RE = re.compile(
    rf'[{_VN_CAP_CHARS}]\w*(?:\s+[{_VN_CAP_CHARS}]\w*)+',
    re.UNICODE,
)


def is_quality_url(url: str) -> bool:
    if not url:
        return True
    lowered = url.lower()
    return not any(bad in lowered for bad in URL_PATH_BLOCKLIST)


CATEGORY_PRIORITY = {
    'chính trị': 3,
    'kinh tế': 3,
    'xã hội': 2,
    'giao thông': 2,
    'y tế': 2,
    'giáo dục': 1,
    'thời tiết': 1,
    'công nghệ': 1,
    'khác': 0,
}

PRIOR_FILES_TO_SCAN = int(os.environ.get('PRIOR_FILES_TO_SCAN', '50'))
ROLLING_HOURS = int(os.environ.get('ROLLING_HOURS', '24'))
TARGET_STORIES = int(os.environ.get('TARGET_STORIES', '5'))
TARGET_STORIES = max(5, min(10, TARGET_STORIES))
FOCUS_KEYWORDS = [x.strip().lower() for x in os.environ.get('FOCUS_KEYWORDS', '').split('|') if x.strip()]
INCLUDE_YESTERDAY = os.environ.get('INCLUDE_YESTERDAY', '0') == '1'
MIN_FOCUS_MATCHES = int(os.environ.get('MIN_FOCUS_MATCHES', str(max(3, TARGET_STORIES // 2))))
# Hard absolute age cap: an incident's pub_date older than this is never picked
# regardless of hot_score, unless it matches FOCUS_KEYWORDS.
MAX_STORY_AGE_HOURS = int(os.environ.get('MAX_STORY_AGE_HOURS', '36'))
# Cluster-signature window runs wider than the token window so follow-up
# coverage of a weeks-old incident can still be caught.
CLUSTER_ROLLING_HOURS = int(os.environ.get('CLUSTER_ROLLING_HOURS', '168'))
# Headline-set Jaccard threshold: candidate's headline tokens vs each prior
# headline (within cluster window). Catches institutional/policy headlines
# (e.g. "Thủ tướng: …") that yield no cluster_keys but still re-air with
# essentially the same wording across days. 1.0 disables the check.
HEADLINE_JACCARD_BLOCK = float(os.environ.get('HEADLINE_JACCARD_BLOCK', '0.6'))


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'ignore')


def strip_html(text: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def clean_headline_text(text: str) -> str:
    text = strip_html(text)
    text = text.replace('“', '"').replace('”', '"').replace('’', "'").replace('‘', "'")
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def parse_image_from_html(text: str):
    if not text:
        return None
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', text, re.I)
    return html.unescape(m.group(1)) if m else None


def fetch_page(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'ignore')


def find_meta_content(html_text: str, attr_name: str, attr_value: str):
    pats = [
        rf'<meta[^>]+{attr_name}=["\']{re.escape(attr_value)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr_name}=["\']{re.escape(attr_value)}["\']',
    ]
    for pat in pats:
        m = re.search(pat, html_text, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def fetch_og_image(url: str):
    try:
        html_text = fetch_page(url)
    except Exception:
        return None
    for attr_name, attr_value in [('property', 'og:image'), ('name', 'twitter:image')]:
        content = find_meta_content(html_text, attr_name, attr_value)
        if content:
            return content
    return None


def probe_image_dims(path: str):
    try:
        out = subprocess.check_output([
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x', path
        ], text=True).strip()
        w, h = out.split('x', 1)
        return int(w), int(h)
    except Exception:
        return (0, 0)


def download_image(url: str, out_path: Path) -> bool:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=40) as r, open(out_path, 'wb') as f:
        f.write(r.read())
    return out_path.exists() and out_path.stat().st_size > 0


def tokenize(text: str):
    text = strip_html(text).lower()
    text = re.sub(r'[^\w\sàáạảãăắằẳẵặâấầẩẫậđèéẹẻẽêếềểễệìíịỉĩòóọỏõôốồổỗộơớờởỡợùúụủũưứừửữựỳýỵỷỹ]', ' ', text)
    tokens = [t for t in text.split() if len(t) >= 3 and t not in STOPWORDS and not t.isdigit()]
    return tokens


def parse_pub_date(pub_date_raw: str):
    if not pub_date_raw:
        return None
    try:
        dt = parsedate_to_datetime(pub_date_raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=runtime.now.tzinfo)
    else:
        dt = dt.astimezone(runtime.now.tzinfo)
    return dt


def viral_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for kw in VIRAL_KEYWORDS if kw in lowered)


def is_controversial(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in CONTROVERSY_KEYWORDS)


def hot_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for kw in HOT_NEWS_KEYWORDS if kw in lowered)


def is_soft_news(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in SOFT_NEWS_KEYWORDS)


def category_from_text(title: str, desc: str):
    text = (title + ' ' + desc).lower()
    rules = [
        ('chính trị', ['quốc hội', 'chính phủ', 'chủ tịch', 'thủ tướng', 'bộ trưởng', 'đảng', 'quốc gia']),
        ('kinh tế', ['giá', 'xuất khẩu', 'doanh nghiệp', 'đầu tư', 'thị trường', 'kinh tế', 'ngân hàng', 'tỷ giá']),
        ('giao thông', ['cao tốc', 'đường', 'giao thông', 'xe buýt', 'tai nạn', 'metro', 'sân bay']),
        ('giáo dục', ['trường', 'học sinh', 'giáo dục', 'thi', 'đại học', 'giáo viên']),
        ('y tế', ['bệnh', 'bệnh viện', 'y tế', 'dịch', 'sức khỏe', 'thực phẩm', 'thuốc']),
        ('thời tiết', ['nắng nóng', 'mưa', 'áp thấp', 'bão', 'cháy rừng', 'thời tiết', 'ngập', 'lũ']),
        ('công nghệ', ['ai', 'trung tâm dữ liệu', 'công nghệ', 'chip', 'internet', 'điện thoại', 'ứng dụng']),
        ('xã hội', ['công an', 'điều tra', 'cháy', 'ma túy', 'an toàn', 'lao động', 'đời sống']),
    ]
    for name, keys in rules:
        if any(k in text for k in keys):
            return name
    return 'khác'


def extract_proper_nouns(text: str):
    # Matches sequences of 2+ consecutive capitalized words (Vietnamese or
    # ASCII caps). Single sentence-start capitals are intentionally skipped
    # — they carry too little signal and create false cluster matches.
    #
    # Splitting on sentence-ending punctuation first prevents a run-on like
    # "…Vĩnh Tuy. Vụ cháy…" from being matched as the 3-word phrase
    # "Vĩnh Tuy Vụ". After the match, any embedded broad-location noun
    # ("hà nội", "tp hcm", …) is stripped so "TP Đà Nẵng" collapses to "tp"
    # (then rejected as too short to serve as a cluster signal).
    if not text:
        return []
    results = []
    for sentence in re.split(r'[.!?;:]+|\n+', text):
        for m in _PROPER_NOUN_RE.finditer(sentence):
            raw = re.sub(r'\s+', ' ', m.group(0).replace('.', ' ').strip()).lower()
            if not raw:
                continue
            cleaned = raw
            for bad in CLUSTER_NOUN_BLOCKLIST:
                cleaned = re.sub(rf'(?:^|\s){re.escape(bad)}(?=\s|$)', ' ', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            words = cleaned.split()
            if len(words) < 2 or not any(len(w) >= 3 for w in words):
                continue
            if cleaned not in results:
                results.append(cleaned)
    return results


def extract_event_keyword(text: str) -> str:
    lowered = (text or '').lower()
    for kw in CLUSTER_EVENT_KEYWORDS:
        if kw in lowered:
            return kw
    return ''


def headline_token_set(headline: str) -> frozenset:
    return frozenset(tokenize(headline or ''))


def compute_cluster_keys(title: str, summary: str):
    # A cluster key is `<proper-noun>@<event>`. We require BOTH parts: a bare
    # proper noun (e.g. "Vĩnh Tuy") without an event word matches too much;
    # a bare event ("cháy") matches unrelated incidents. Multiple keys per
    # story let a follow-up article with an extra noun still match the
    # original ({vĩnh tuy@cháy} ⊂ {hà nội@cháy, vĩnh tuy@cháy}).
    # The explicit ". " between title and summary ensures extract_proper_nouns
    # treats them as separate sentences instead of a run-on phrase.
    t = (title or '').strip()
    s = (summary or '').strip()
    if t and t[-1] not in '.!?':
        t = t + '.'
    text = f'{t} {s}'.strip()
    nouns = extract_proper_nouns(text)
    event = extract_event_keyword(text)
    if not nouns or not event:
        return []
    return [f'{n}@{event}' for n in nouns[:3]]


def normalize_story(entry, idx):
    link = entry.get('link', '') or ''
    if not is_quality_url(link):
        return None
    title_vi = clean_headline_text(entry['title'])
    desc_vi = strip_html(entry.get('description', ''))
    if not desc_vi:
        desc_vi = title_vi
    desc_vi = re.sub(r'^\([^)]{2,40}\)\s*[-–:]\s*', '', desc_vi).strip()
    desc_vi = re.sub(r'^[\-–•\s]+', '', desc_vi)
    desc_vi = re.sub(r'\s*\[.*?\]\s*$', '', desc_vi).strip()
    lowered = (title_vi + ' ' + desc_vi).lower()
    if any(bad in lowered for bad in BLACKLIST_KEYWORDS):
        return None
    if desc_vi == title_vi:
        summary_vi = title_vi
    else:
        summary_vi = desc_vi
        if not summary_vi.endswith(('.', '!', '?')):
            summary_vi += '.'
    title_en = title_vi
    summary_en = summary_vi
    pub_dt = parse_pub_date(entry.get('pub_date', ''))
    return {
        'id': f'story-{idx:02d}',
        'headline_vi': title_vi,
        'headline_en': title_en,
        'summary_vi': summary_vi,
        'summary_en': summary_en,
        'sources': [{'name': entry['source_name'], 'url': entry['link']}],
        'source_name': entry['source_name'],
        'source_url': entry['link'],
        'image_url': entry.get('image_url'),
        'pub_date': entry.get('pub_date', ''),
        'pub_dt': pub_dt,
        'category': category_from_text(title_vi, summary_vi),
        'tokens': tokenize(title_vi + ' ' + summary_vi),
        'cluster_keys': compute_cluster_keys(title_vi, summary_vi),
    }


def parse_rss(feed_name: str, url: str):
    xml_text = fetch(url)
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall('.//item')[:20]:
        title = item.findtext('title') or ''
        link = item.findtext('link') or ''
        description_raw = item.findtext('description') or ''
        pub_date = item.findtext('pubDate') or ''
        image_url = None

        enclosure = item.find('enclosure')
        if enclosure is not None:
            image_url = enclosure.attrib.get('url')

        for child in item:
            tag = child.tag.lower()
            if tag.endswith('content') or tag.endswith('thumbnail'):
                image_url = image_url or child.attrib.get('url')

        image_url = image_url or parse_image_from_html(description_raw)
        items.append({
            'source_name': feed_name,
            'title': title,
            'link': link,
            'description': description_raw,
            'pub_date': pub_date,
            'image_url': image_url,
        })
    return items


def parse_history_runs():
    if not HISTORY_PATH.exists():
        return []
    run_entries = {}
    run_times = {}
    with HISTORY_PATH.open('r', encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            run_key = data.get('run_key') or f"{data.get('run_date', '')}-{data.get('run_hhmm', '')}"
            if not run_key or run_key == CURRENT_RUN_KEY:
                continue
            ts_str = data.get('run_timestamp') or data.get('timestamp') or ''
            ts_dt = None
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str)
                except Exception:
                    ts_dt = None
            # Fallback: reconstruct from run_date + run_hhmm (handles legacy
            # "2026-04-22 14:30 Asia/Bangkok" timestamps that fromisoformat
            # cannot parse because of the trailing IANA tz name).
            if ts_dt is None:
                rd = data.get('run_date') or ''
                rh = data.get('run_hhmm') or ''
                if rd and len(rh) == 4:
                    try:
                        ts_dt = datetime.strptime(f'{rd} {rh}', '%Y-%m-%d %H%M').replace(
                            tzinfo=runtime.now.tzinfo
                        )
                    except Exception:
                        ts_dt = None
            if ts_dt is None:
                continue
            run_entries.setdefault(run_key, []).append(data)
            run_times.setdefault(run_key, ts_dt)
    runs = []
    for run_key, entries in run_entries.items():
        ts = run_times.get(run_key)
        if ts is None:
            continue
        runs.append((ts, run_key, entries))
    runs.sort(key=lambda x: x[0], reverse=True)
    return runs


def load_prior_headlines():
    # Two windows share one scan of the history file:
    #   - token window (ROLLING_HOURS, default 24h): for overlap-count anti-repeat
    #   - cluster window (CLUSTER_ROLLING_HOURS, default 168h): catches follow-up
    #     coverage of incidents that broke days earlier.
    # PRIOR_FILES_TO_SCAN caps the outer walk so we don't read the whole
    # rolling JSONL when the cluster window runs wide.
    prior_tokens = set()
    prior_categories = set()
    prior_links = set()
    prior_clusters = set()
    prior_headline_sets = []  # list[frozenset] within cluster window
    scanned_tokens = 0
    scanned_clusters = 0
    token_cutoff = runtime.now - timedelta(hours=ROLLING_HOURS)
    cluster_cutoff = runtime.now - timedelta(hours=CLUSTER_ROLLING_HOURS)

    for ts, run_key, entries in parse_history_runs():
        if ts >= runtime.now:
            continue
        if ts < cluster_cutoff:
            break
        within_tokens = ts >= token_cutoff
        within_clusters = ts >= cluster_cutoff
        if within_tokens:
            scanned_tokens += 1
        if within_clusters:
            scanned_clusters += 1
        for story in entries:
            title = story.get('headline_vi') or ''
            summary = story.get('summary_vi') or ''
            if within_clusters:
                # Legacy history rows (pre-cluster) won't have cluster_keys —
                # derive on the fly so the week-old backfill still filters.
                keys = story.get('cluster_keys')
                if not keys:
                    keys = compute_cluster_keys(title, summary)
                for key in keys:
                    if key:
                        prior_clusters.add(key)
                # Headline-set signature: catches re-airings of institutional
                # speeches/policy stories that don't yield cluster_keys.
                hs_set = headline_token_set(title)
                if hs_set:
                    prior_headline_sets.append(hs_set)
            if within_tokens:
                prior_tokens.update(tokenize(title + ' ' + summary))
                category = story.get('category') or category_from_text(title, summary)
                prior_categories.add(category)
                source_url = story.get('source_url') or ''
                if source_url:
                    prior_links.add(source_url)
        if scanned_clusters >= PRIOR_FILES_TO_SCAN:
            break
    return (
        prior_tokens, prior_categories, prior_links, prior_clusters,
        prior_headline_sets, scanned_tokens, scanned_clusters,
    )


def pick_stories(pool):
    (
        prior_tokens, prior_categories, prior_links, prior_clusters,
        prior_headline_sets, scanned_tokens, scanned_clusters,
    ) = load_prior_headlines()

    def focus_score(candidate):
        if not FOCUS_KEYWORDS:
            return 0
        hay = (candidate['headline_vi'] + ' ' + candidate['summary_vi']).lower()
        return sum(1 for kw in FOCUS_KEYWORDS if kw in hay)

    def priority_score(candidate):
        return CATEGORY_PRIORITY.get(candidate.get('category', 'khác'), 0)

    def recency_score(candidate):
        pub_dt = candidate.get('pub_dt')
        if not pub_dt:
            return 0
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=runtime.now.tzinfo)
        else:
            pub_dt = pub_dt.astimezone(runtime.now.tzinfo)
        delta = (runtime.now - pub_dt).total_seconds() / 3600
        return max(0, 24 - delta)

    def story_age_hours(candidate):
        pub_dt = candidate.get('pub_dt')
        if not pub_dt:
            return None
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=runtime.now.tzinfo)
        else:
            pub_dt = pub_dt.astimezone(runtime.now.tzinfo)
        return (runtime.now - pub_dt).total_seconds() / 3600

    def final_rank(candidate):
        text = candidate['headline_vi'] + ' ' + candidate['summary_vi']
        return (
            focus_score(candidate),
            priority_score(candidate),    # chính trị/kinh tế/xã hội ưu tiên trước
            hot_score(text),              # rồi tới độ nóng
            recency_score(candidate),     # rồi độ mới
            viral_score(text),
            len(candidate['tokens']),
        )

    def in_prior_cluster(candidate):
        keys = candidate.get('cluster_keys') or []
        return any(k in prior_clusters for k in keys)

    def headline_clashes_prior(candidate):
        # Headline-set Jaccard against each prior headline (within 168h).
        # Catches institutional/speech stories like "Thủ tướng: Tiếng nói
        # Việt Nam vang xa…" that yield no cluster_keys (no 2-word proper
        # noun + no event word) but re-air with the same wording across
        # multiple days. Exact-set match always blocks; otherwise apply
        # the configured Jaccard threshold.
        if HEADLINE_JACCARD_BLOCK >= 1.0 or not prior_headline_sets:
            return False
        cand = headline_token_set(candidate.get('headline_vi', ''))
        if not cand:
            return False
        for prior in prior_headline_sets:
            if cand == prior:
                return True
            inter = len(cand & prior)
            if not inter:
                continue
            union = len(cand | prior)
            if union and inter / union >= HEADLINE_JACCARD_BLOCK:
                return True
        return False

    ranked_pool = sorted(pool, key=final_rank, reverse=True)

    # Hard absolute age cap for main pass. Hot stories (cháy, nổ, bắt…) used to
    # ride through recency_score because the old gate only dropped when hs==0;
    # that let week-old incidents resurface via follow-up coverage. Bypass only
    # for explicit FOCUS_KEYWORDS matches.
    fresh_pool = []
    for c in ranked_pool:
        age_h = story_age_hours(c)
        if age_h is not None and age_h > MAX_STORY_AGE_HOURS and focus_score(c) == 0:
            continue
        fresh_pool.append(c)

    selected = []
    used_links = set()
    used_categories = set()
    used_token_sets = []  # per-selected token sets, for intra-run cluster dedup
    duplicate_pool = []

    CLUSTER_OVERLAP = 4  # tokens shared ⇒ same topic cluster

    def clashes_with_selected(candidate):
        cand_set = set(candidate['tokens'])
        return any(len(cand_set & used) >= CLUSTER_OVERLAP for used in used_token_sets)

    for candidate in fresh_pool:
        if candidate['source_url'] in used_links:
            continue
        if not candidate.get('image_url'):
            continue

        text = candidate['headline_vi'] + ' ' + candidate['summary_vi']
        if is_soft_news(text):
            continue

        # Cluster-signature anti-repeat — hard drop, no hot bypass. Catches
        # follow-up articles about an incident that broke days earlier.
        if in_prior_cluster(candidate):
            continue

        # Headline-set Jaccard anti-repeat — catches institutional/policy
        # re-airings (no cluster_keys → cluster check is silent for them).
        # Focus_keywords override: explicit user request beats anti-repeat.
        if headline_clashes_prior(candidate) and focus_score(candidate) == 0:
            continue

        if candidate['source_url'] in prior_links:
            duplicate_pool.append(candidate)
            continue

        overlap = len(set(candidate['tokens']) & prior_tokens)
        fs = focus_score(candidate)
        hs = hot_score(text)

        # No hot-story bypass here: a high hot_score must not override the
        # topic-repeat signal (that was the original fire-story regression).
        if overlap >= 5 and fs == 0:
            continue

        # Same topic cluster as an already-selected story in this run.
        if clashes_with_selected(candidate):
            continue

        # Allow repeated category when story is hot enough.
        already_used_category = candidate['category'] in used_categories
        if already_used_category and hs < 2 and len(selected) < TARGET_STORIES - 1:
            continue

        # Strongly prefer very recent hot stories.
        if recency_score(candidate) < 12 and hs == 0 and fs == 0:
            continue

        selected.append(candidate)
        used_links.add(candidate['source_url'])
        used_categories.add(candidate['category'])
        used_token_sets.append(set(candidate['tokens']))
        if len(selected) == TARGET_STORIES:
            break

    # Fallback 1: relax age + token-overlap rules, but cluster check, headline
    # check and soft-news filter still apply. A week-old incident cluster
    # never sneaks in even when the main pass undersubscribed.
    if len(selected) < TARGET_STORIES:
        for candidate in ranked_pool:
            if candidate['source_url'] in used_links:
                continue
            if not candidate.get('image_url'):
                continue
            text = candidate['headline_vi'] + ' ' + candidate['summary_vi']
            if is_soft_news(text):
                continue
            if in_prior_cluster(candidate):
                continue
            if headline_clashes_prior(candidate):
                continue
            if clashes_with_selected(candidate):
                continue
            selected.append(candidate)
            used_links.add(candidate['source_url'])
            used_token_sets.append(set(candidate['tokens']))
            if len(selected) == TARGET_STORIES:
                break

    # Fallback 2: URLs we saw in prior runs (within token window). Still gated
    # by cluster + headline checks so we don't literally re-air the same story.
    if len(selected) < TARGET_STORIES:
        for candidate in duplicate_pool:
            if candidate['source_url'] in used_links:
                continue
            if not candidate.get('image_url'):
                continue
            if in_prior_cluster(candidate):
                continue
            if headline_clashes_prior(candidate):
                continue
            if clashes_with_selected(candidate):
                continue
            selected.append(candidate)
            used_links.add(candidate['source_url'])
            used_token_sets.append(set(candidate['tokens']))
            if len(selected) == TARGET_STORIES:
                break

    # Enforce at least 3 hot headlines when possible, without violating
    # intra-run cluster dedup (rule 4 in NEWS_PIPELINE_RULES.md). Rescue pool
    # draws from fresh_pool (age-capped) and excludes prior-cluster topics so
    # a stale hot incident can't be rescued back in.
    hot_count = sum(1 for s in selected if hot_score(s['headline_vi'] + ' ' + s['summary_vi']) > 0)
    if hot_count < 3:
        hot_pool = [
            c for c in fresh_pool
            if c['source_url'] not in used_links
            and c.get('image_url')
            and hot_score(c['headline_vi'] + ' ' + c['summary_vi']) > 0
            and not is_soft_news(c['headline_vi'] + ' ' + c['summary_vi'])
            and not in_prior_cluster(c)
            and not headline_clashes_prior(c)
            and len(set(c['tokens']) & prior_tokens) < 5
        ]
        for hot_candidate in hot_pool:
            # replace the least-hot selected item
            selected_sorted = sorted(
                selected,
                key=lambda s: hot_score(s['headline_vi'] + ' ' + s['summary_vi'])
            )
            if not selected_sorted:
                break
            victim = selected_sorted[0]
            victim_hot = hot_score(victim['headline_vi'] + ' ' + victim['summary_vi'])
            if victim_hot >= hot_score(hot_candidate['headline_vi'] + ' ' + hot_candidate['summary_vi']):
                continue
            # Check cluster dedup against the non-victim selected stories.
            remaining_tokens = [set(s['tokens']) for s in selected if s is not victim]
            cand_set = set(hot_candidate['tokens'])
            if any(len(cand_set & t) >= CLUSTER_OVERLAP for t in remaining_tokens):
                continue
            selected.remove(victim)
            used_links.discard(victim['source_url'])
            selected.append(hot_candidate)
            used_links.add(hot_candidate['source_url'])
            used_token_sets[:] = [set(s['tokens']) for s in selected]
            hot_count = sum(1 for s in selected if hot_score(s['headline_vi'] + ' ' + s['summary_vi']) > 0)
            if hot_count >= 3:
                break

    selected = sorted(selected, key=final_rank, reverse=True)[:TARGET_STORIES]
    return (
        selected, prior_categories, prior_links, prior_clusters,
        prior_headline_sets, scanned_tokens, scanned_clusters,
    )


def filter_candidates_for_ai(pool):
    """Apply anti-repeat hard drops and editorial filters to pool.

    Returns (filtered_pool, anti_repeat_state) where anti_repeat_state is the same
    7-tuple returned by pick_stories() so the history-update path is unchanged.
    Algorithmic ranking/selection is intentionally skipped — that is left to AI.
    """
    (
        prior_tokens, prior_categories, prior_links, prior_clusters,
        prior_headline_sets, scanned_tokens, scanned_clusters,
    ) = load_prior_headlines()

    def _focus_score(candidate):
        if not FOCUS_KEYWORDS:
            return 0
        hay = (candidate['headline_vi'] + ' ' + candidate['summary_vi']).lower()
        return sum(1 for kw in FOCUS_KEYWORDS if kw in hay)

    def _age_hours(candidate):
        pub_dt = candidate.get('pub_dt')
        if not pub_dt:
            return None
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=runtime.now.tzinfo)
        else:
            pub_dt = pub_dt.astimezone(runtime.now.tzinfo)
        return (runtime.now - pub_dt).total_seconds() / 3600

    def _in_prior_cluster(candidate):
        return any(k in prior_clusters for k in (candidate.get('cluster_keys') or []))

    def _headline_clashes(candidate):
        if HEADLINE_JACCARD_BLOCK >= 1.0 or not prior_headline_sets:
            return False
        cand = headline_token_set(candidate.get('headline_vi', ''))
        if not cand:
            return False
        for prior in prior_headline_sets:
            if cand == prior:
                return True
            inter = len(cand & prior)
            if not inter:
                continue
            if inter / len(cand | prior) >= HEADLINE_JACCARD_BLOCK:
                return True
        return False

    filtered = []
    for candidate in pool:
        if not candidate.get('image_url'):
            continue
        text = candidate['headline_vi'] + ' ' + candidate['summary_vi']
        if is_soft_news(text):
            continue
        age_h = _age_hours(candidate)
        if age_h is not None and age_h > MAX_STORY_AGE_HOURS and _focus_score(candidate) == 0:
            continue
        if _in_prior_cluster(candidate):
            continue
        if _headline_clashes(candidate) and _focus_score(candidate) == 0:
            continue
        overlap = len(set(candidate['tokens']) & prior_tokens)
        if overlap >= 5 and _focus_score(candidate) == 0:
            continue
        filtered.append(candidate)

    anti_repeat_state = (
        prior_tokens, prior_categories, prior_links, prior_clusters,
        prior_headline_sets, scanned_tokens, scanned_clusters,
    )
    return filtered, anti_repeat_state


raw_entries = []
feed_errors = []
feed_plan = FOCUS_FEEDS + [f for f in FEEDS if f not in FOCUS_FEEDS] if FOCUS_KEYWORDS else FEEDS
for name, url in feed_plan:
    try:
        raw_entries.extend(parse_rss(name, url))
    except Exception as e:
        feed_errors.append(f'{name}: {e}')

seen_links = set()
pool = []
for idx, entry in enumerate(raw_entries, start=1):
    link = entry.get('link')
    if not link or link in seen_links:
        continue
    seen_links.add(link)
    story = normalize_story(entry, idx)
    if story is None:
        continue
    pool.append(story)

# --- Story selection: AI-first, algorithmic fallback ---
# Phase 1: filter pool by anti-repeat hard drops + editorial hard filters.
# Ranking / final selection is left to AI so it can weigh editorial importance
# holistically rather than via heuristic scores.
filtered_pool, _ar_state = filter_candidates_for_ai(pool)
(
    _prior_tokens_ar, prior_categories, prior_links, prior_clusters,
    prior_headline_sets, prior_scanned_tokens, prior_scanned_clusters,
) = _ar_state
print(f"[ai-select] pool={len(pool)} → filtered={len(filtered_pool)}", flush=True)

# Phase 2: ask AI to pick the best TARGET_STORIES from filtered_pool.
used_ai_selection = False
ai_indices = select_stories(filtered_pool, TARGET_STORIES, FOCUS_KEYWORDS or None)
if ai_indices is not None:
    stories = [filtered_pool[i] for i in ai_indices]
    used_ai_selection = True
    print(
        f"[ai-select] AI selected {len(stories)}: "
        + '; '.join(s['headline_vi'] for s in stories),
        flush=True,
    )
else:
    # Fallback: algorithmic pick_stories when AI is unavailable or fails.
    print("[ai-select] AI selection unavailable; falling back to algorithmic pick_stories", flush=True)
    (
        stories, prior_categories, prior_links, prior_clusters,
        prior_headline_sets, prior_scanned_tokens, prior_scanned_clusters,
    ) = pick_stories(pool)

if len(stories) < TARGET_STORIES:
    raise RuntimeError(
        f'Not enough stories. selected={len(stories)} filtered={len(filtered_pool)} '
        f'pool={len(pool)} feeds={len(raw_entries)} errors={feed_errors}'
    )

stories = stories[:TARGET_STORIES]
for idx, story in enumerate(stories, start=1):
    story['id'] = f'story-{idx:02d}'

# Phase 3: AI rewrites summary_vi from full article body (TTS quality layer).
# anti-repeat tokens / cluster_keys keep RSS-based values so editorial decisions
# stay deterministic. Any failure leaves RSS summary_vi in place.
for story in stories:
    story['summary_source'] = 'rss'
    body = fetch_article_body(story.get('source_url', ''))
    if not body:
        print(f"[ai] {story['id']} body fetch failed; keeping RSS summary", flush=True)
        continue
    ai_text = summarize_for_bulletin(story.get('headline_vi', ''), body)
    if not ai_text:
        print(f"[ai] {story['id']} AI summarize failed; keeping RSS summary", flush=True)
        continue
    story['summary_vi'] = ai_text
    story['summary_en'] = ai_text
    story['summary_source'] = 'ai'
    print(f"[ai] {story['id']} rewrote summary ({len(ai_text)} chars)", flush=True)

all_sources = []
for s in stories:
    src = s['sources'][0]
    all_sources.append(f"{s['headline_vi']} | {src['name']} | {src['url']}")


def normalize_sentences(text: str):
    text = strip_html(text)
    chunks = [c.strip(' \n\t-•') for c in re.split(r'(?<=[.!?])\s+', text) if c.strip()]
    if not chunks and text:
        chunks = [text]
    return chunks


def build_journalist_voice(stories_local):
    lead = f"Xin kính chào quý vị. Đây là bản tin Việt Nam cập nhật lúc {RUN_HOUR}."
    lines = [lead]
    story_units = []
    for s in stories_local:
        headline = (s.get('headline_vi') or '').strip()
        summary = (s.get('summary_vi') or '').strip()
        headline_norm = headline.rstrip('.!?').strip().lower()
        summary_norm = summary.rstrip('.!?').strip().lower()
        if not summary or summary_norm == headline_norm:
            # No extra detail beyond headline — speak it once.
            block = headline if headline.endswith(('.', '!', '?')) else headline + '.'
            sentence_count = 1
        else:
            sentences = normalize_sentences(summary)
            # Skip a leading sentence that duplicates the headline.
            if sentences and sentences[0].rstrip('.!?').strip().lower() == headline_norm:
                sentences = sentences[1:]
            # AI rewrites are already shaped as a 3-5 sentence broadcast paragraph
            # — read in full. RSS descriptions are unbounded boilerplate, so cap
            # them at 2 sentences as before.
            if s.get('summary_source') == 'ai':
                detail_sentences = sentences
            else:
                detail_sentences = sentences[:2]
            detail = ' '.join(detail_sentences).strip()
            if detail and not detail.endswith(('.', '!', '?')):
                detail += '.'
            if detail:
                block = f"{headline}. {detail}".strip()
                sentence_count = len(detail_sentences) + 1
            else:
                block = headline if headline.endswith(('.', '!', '?')) else headline + '.'
                sentence_count = 1
        block = re.sub(r'\s+', ' ', block)
        lines.append(block)
        story_units.append({
            'story_id': s['id'],
            'headline_vi': headline,
            'voice_block_vi': block,
            'sentence_count': sentence_count,
        })
    outro = "Bản tin tạm dừng tại đây. Chúng tôi sẽ tiếp tục cập nhật trong các bản tin tiếp theo."
    lines.append(outro)
    full_script = '\n\n'.join(lines).strip() + '\n'
    return full_script, lead, story_units, outro

for story in stories:
    src_img_path = ''
    if story.get('image_url'):
        ext = '.png' if '.png' in story['image_url'].lower() else '.jpg'
        src_img = SRC_DIR / f"{story['id']}{ext}"
        try:
            download_image(story['image_url'], src_img)
            w, h = probe_image_dims(str(src_img))
            # RSS thumbnails are often tiny; try OG image as fallback.
            if min(w, h) < 720 and story.get('source_url'):
                og_image = fetch_og_image(story['source_url'])
                if og_image and og_image != story['image_url']:
                    alt_ext = '.png' if '.png' in og_image.lower() else '.jpg'
                    alt_img = SRC_DIR / f"{story['id']}-og{alt_ext}"
                    try:
                        download_image(og_image, alt_img)
                        w2, h2 = probe_image_dims(str(alt_img))
                        if (w2 * h2) > (w * h):
                            src_img = alt_img
                            story['image_url'] = og_image
                            w, h = w2, h2
                    except Exception:
                        pass
            src_img_path = str(src_img)
            story['source_image_size'] = {'width': w, 'height': h}
        except Exception:
            src_img_path = ''
    story['source_image_path'] = src_img_path
    out_img = str(PREP_DIR / f"{story['id']}-vertical.jpg")
    if src_img_path and os.path.exists(src_img_path):
        cmd = [
            'ffmpeg', '-y', '-i', src_img_path, '-filter_complex',
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,unsharp=5:5:0.8:5:5:0.0[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuvj420p",
            '-frames:v', '1', '-q:v', '2', out_img
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        story['prepared_image'] = out_img
        story['image_processing'] = 'fit+blur'
    else:
        story['prepared_image'] = ''
        story['image_processing'] = 'image-missing'

voice_vi, intro_vi, story_units, outro_vi = build_journalist_voice(stories)
voice_en = '\n'.join(s['summary_en'] for s in stories).strip() + '\n'

headline_list_vi = '\n'.join(s['headline_vi'] for s in stories) + '\n'
headline_list_en = '\n'.join(s['headline_en'] for s in stories) + '\n'
caption_vi = (
    f"Bản tin Việt Nam {RUN_HOUR}: " + '; '.join(s['headline_vi'] for s in stories[:3]) + '. '
    "Theo bạn, đâu là diễn biến đáng chú ý nhất?"
)
hashtags = '#tinvietnam #tinnong #tinthoisu #vietnamnews #capnhat #tiktoknews #xuhuong'

files = {
    'voice_vi.txt': voice_vi,
    'voice_en.txt': voice_en,
    'headline-list_vi.txt': headline_list_vi,
    'headline-list_en.txt': headline_list_en,
    'tiktok-caption_vi.txt': caption_vi + '\n',
    'tiktok-hashtags.txt': hashtags + '\n',
    'sources.txt': '\n'.join(all_sources + ([f'Feed errors: {" | ".join(feed_errors)}'] if feed_errors else [])) + '\n',
}
for name, content in files.items():
    (RUN_DIR / name).write_text(content, encoding='utf-8')

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/home/nv-ngoc/keys/tts-sa.json'
client = texttospeech.TextToSpeechClient()
voice = texttospeech.VoiceSelectionParams(language_code='vi-VN', name='vi-VN-Neural2-A')
audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16, speaking_rate=1.15)

probe = lambda p: float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(p)]).decode().strip())

all_audio_paths = []

intro_input = texttospeech.SynthesisInput(text=intro_vi)
intro_resp = client.synthesize_speech(input=intro_input, voice=voice, audio_config=audio_config)
intro_path = TMP_DIR / 'voice_intro.wav'
intro_path.write_bytes(intro_resp.audio_content)
intro_duration = probe(intro_path)
all_audio_paths.append(intro_path)

story_durations = []
for idx, unit in enumerate(story_units, start=1):
    block_input = texttospeech.SynthesisInput(text=unit['voice_block_vi'])
    block_resp = client.synthesize_speech(input=block_input, voice=voice, audio_config=audio_config)
    block_path = TMP_DIR / f"voice_story_{idx:02d}.wav"
    block_path.write_bytes(block_resp.audio_content)
    dur = probe(block_path)
    all_audio_paths.append(block_path)
    story_durations.append(dur)
    unit['audio_duration_seconds'] = dur

outro_input = texttospeech.SynthesisInput(text=outro_vi)
outro_resp = client.synthesize_speech(input=outro_input, voice=voice, audio_config=audio_config)
outro_path = TMP_DIR / 'voice_outro.wav'
outro_path.write_bytes(outro_resp.audio_content)
outro_duration = probe(outro_path)
all_audio_paths.append(outro_path)

audio_concat = TMP_DIR / 'audio_concat.txt'
with open(audio_concat, 'w', encoding='utf-8') as f:
    for p in all_audio_paths:
        f.write(f"file '{p}'\n")
audio_path = RUN_DIR / 'voice_vi.wav'
subprocess.run([
    'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(audio_concat),
    '-c', 'copy', str(audio_path)
], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

audio_duration = probe(audio_path)
seg_durs = [round(d, 3) for d in story_durations]
if seg_durs:
    seg_durs[0] = round(seg_durs[0] + intro_duration, 3)
    seg_durs[-1] = round(seg_durs[-1] + outro_duration, 3)

segment_paths = []
for i, (story, dur) in enumerate(zip(stories, seg_durs), start=1):
    seg = TMP_DIR / f'seg{i:02d}.mp4'
    img = story['prepared_image']
    subprocess.run([
        'ffmpeg', '-y', '-loop', '1', '-t', str(dur), '-i', img,
        '-vf', 'scale=1080:1920,format=yuv420p',
        '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', str(seg)
    ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    segment_paths.append(seg)

concat_list = TMP_DIR / 'concat.txt'
with open(concat_list, 'w', encoding='utf-8') as f:
    for p in segment_paths:
        f.write(f"file '{p}'\n")
slideshow = TMP_DIR / 'slideshow.mp4'
subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_list), '-c', 'copy', str(slideshow)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
video_path = RUN_DIR / vn_video_filename(RUN_DATE, RUN_HHMM)
subprocess.run([
    'ffmpeg', '-y', '-i', str(slideshow), '-i', str(audio_path),
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-b:a', '192k', '-shortest', str(video_path)
], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
video_duration = probe(video_path)

_selection_mode = 'AI selection + ' if used_ai_selection else 'Algorithmic selection + '
anti_repeat_note = (
    f'Dynamic RSS sourcing enabled. {_selection_mode}'
    + (
        f"Prior categories avoided where possible: {', '.join(sorted(prior_categories))}. "
        f"Token window: last {ROLLING_HOURS}h, runs scanned: {prior_scanned_tokens}, prior links: {len(prior_links)}. "
        f"Cluster window: last {CLUSTER_ROLLING_HOURS}h, runs scanned: {prior_scanned_clusters}, prior clusters: {len(prior_clusters)}. "
        f"Headline-Jaccard window: last {CLUSTER_ROLLING_HOURS}h, prior headlines: {len(prior_headline_sets)}, threshold: {HEADLINE_JACCARD_BLOCK}. "
        f"Max story age: {MAX_STORY_AGE_HOURS}h."
        if prior_scanned_clusters else
        f"No recent metadata found in last {CLUSTER_ROLLING_HOURS}h for anti-repeat comparison."
    )
)

metadata = {
    'run_datetime_local': runtime.iso_local,
    'timestamp_overlay_applied': False,
    'timestamp_overlay_text': '',
    'tts': {
        'provider': 'Google Cloud Text-to-Speech',
        'language': 'vi-VN',
        'voice_name': 'vi-VN-Neural2-A',
        'speaking_rate': 1.2,
        'output_file': 'voice_vi.wav'
    },
    'audio_duration_seconds': audio_duration,
    'video_duration_seconds': video_duration,
    'story_count': len(stories),
    'stories': [],
    'anti_repeat_note': anti_repeat_note,
    'feed_sources': [{'name': n, 'url': u} for n, u in FEEDS],
    'feed_errors': feed_errors,
}
manifest_lines = [
    f'Run directory: news-videos-vn/{RUN_DATE}/{RUN_HHMM}',
    f'Final video: {video_path.name}',
    'Timestamp overlay applied: no',
    'TTS speaking rate: 1.2',
    f'Audio duration seconds: {audio_duration:.3f}',
    f'Video duration seconds: {video_duration:.3f}',
    'Prepared images: 1080x1920 fit+blur for all stories',
    f'Anti-repeat note: {anti_repeat_note}',
]
for s in stories:
    metadata['stories'].append({
        'id': s['id'],
        'headline_vi': s['headline_vi'],
        'headline_en': s['headline_en'],
        'summary_vi': s['summary_vi'],
        'summary_en': s['summary_en'],
        'summary_source': s.get('summary_source', 'rss'),
        'source_name': s['source_name'],
        'source_url': s['source_url'],
        'secondary_sources': s['sources'][1:],
        'image_url': s['image_url'],
        'pub_date': s['pub_date'],
        'image_processing': s['image_processing'],
        'prepared_image': s['prepared_image'],
        'category': s['category'],
    })
    manifest_lines.append(f"{s['id']}: {s['headline_vi']} | category={s['category']} | image={Path(s['prepared_image']).name} | mode={s['image_processing']}")
(RUN_DIR / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
(RUN_DIR / 'manifest.txt').write_text('\n'.join(manifest_lines) + '\n', encoding='utf-8')


def update_history(stories):
    entries = []
    if HISTORY_PATH.exists():
        with HISTORY_PATH.open('r', encoding='utf-8') as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                run_key = data.get('run_key') or f"{data.get('run_date', '')}-{data.get('run_hhmm', '')}"
                if run_key == CURRENT_RUN_KEY:
                    continue
                entries.append(json.dumps(data, ensure_ascii=False))
    for story in stories:
        entry = {
            'timestamp': runtime.iso_local,
            'run_timestamp': runtime.now.isoformat(),
            'run_date': RUN_DATE,
            'run_hhmm': RUN_HHMM,
            'run_key': CURRENT_RUN_KEY,
            'headline_vi': story['headline_vi'],
            'summary_vi': story['summary_vi'],
            'source_url': story.get('source_url', ''),
            'category': story.get('category', ''),
            'cluster_keys': story.get('cluster_keys', []),
            'tokens': story.get('tokens', []),
        }
        entries.append(json.dumps(entry, ensure_ascii=False))
    if len(entries) > HISTORY_MAX_LINES:
        entries = entries[-HISTORY_MAX_LINES:]
    HISTORY_PATH.write_text('\n'.join(entries) + '\n', encoding='utf-8')


update_history(stories)

remote = f'gdrive:news-videos-vn/{RUN_DATE}/{RUN_HHMM}/'
copy_cmd = ['rclone', 'copy', str(RUN_DIR), remote, '--create-empty-src-dirs', '--checksum']
copy_proc = subprocess.run(copy_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
list_proc = subprocess.run(['rclone', 'lsf', remote, '-R'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
local_files = []
for root, dirs, files2 in os.walk(RUN_DIR):
    for fn in files2:
        rel = os.path.relpath(os.path.join(root, fn), RUN_DIR)
        local_files.append(rel)
local_set = set(sorted(local_files))
remote_set = set(sorted([line.strip() for line in list_proc.stdout.decode().splitlines() if line.strip()]))
missing = sorted(local_set - remote_set)
extra = sorted(remote_set - local_set)
upload_ok = (copy_proc.returncode == 0 and list_proc.returncode == 0 and not missing)
summary = {
    'run_dir': str(RUN_DIR),
    'remote': remote,
    'upload_copy_returncode': copy_proc.returncode,
    'upload_copy_stderr': copy_proc.stderr.decode('utf-8', 'ignore')[-4000:],
    'upload_list_returncode': list_proc.returncode,
    'upload_list_stderr': list_proc.stderr.decode('utf-8', 'ignore')[-4000:],
    'missing_remote_files': missing,
    'extra_remote_files': extra,
    'audio_duration_seconds': audio_duration,
    'video_duration_seconds': video_duration,
    'video_file': video_path.name,
    'headlines': [s['headline_vi'] for s in stories],
    'anti_repeat_note': anti_repeat_note,
    'local_deleted': False,
}
if upload_ok:
    shutil.rmtree(RUN_DIR)
    summary['local_deleted'] = True
Path('/home/nv-ngoc/.openclaw/workspace/news-video-last-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))

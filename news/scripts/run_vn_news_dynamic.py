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

from news.core.runtime import get_runtime, vn_video_filename

runtime = get_runtime()
RUN_DATE = runtime.run_date
RUN_HHMM = runtime.run_hhmm
RUN_HOUR = runtime.spoken_hour_vi
CURRENT_RUN_KEY = f"{RUN_DATE}-{RUN_HHMM}"
BASE = Path('/home/minipc/.openclaw/workspace/news-videos-vn')
RUN_DIR = BASE / RUN_DATE / RUN_HHMM
PREP_DIR = RUN_DIR / 'prepared'
SRC_DIR = RUN_DIR / 'source-images'
TMP_DIR = RUN_DIR / 'tmp'
RUN_DIR.mkdir(parents=True, exist_ok=True)
PREP_DIR.mkdir(exist_ok=True)
SRC_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)
HISTORY_PATH = Path('/home/minipc/.openclaw/workspace/news-vn-history.jsonl')
HISTORY_MAX_LINES = int(os.environ.get('HISTORY_MAX_LINES', '1500'))

FEEDS = [
    ('VnExpress', 'https://vnexpress.net/rss/tin-moi-nhat.rss'),
    ('VnExpress-Thời sự', 'https://vnexpress.net/rss/thoi-su.rss'),
    ('Tuổi Trẻ', 'https://tuoitre.vn/rss/tin-moi-nhat.rss'),
    ('Tuổi Trẻ-Thời sự', 'https://tuoitre.vn/rss/thoi-su.rss'),
    ('VietnamNet', 'https://vietnamnet.vn/rss/home.rss'),
    ('VietnamNet-Thời sự', 'https://vietnamnet.vn/rss/thoi-su.rss'),
    ('Dân Trí', 'https://dantri.com.vn/rss/home.rss'),
    ('Dân Trí-Xã hội', 'https://dantri.com.vn/rss/xa-hoi.rss'),
    ('VietnamPlus', 'https://www.vietnamplus.vn/rss/tin-moi-nhat.rss'),
]

FOCUS_FEEDS = [
    ('VnExpress-Thời sự', 'https://vnexpress.net/rss/thoi-su.rss'),
    ('Tuổi Trẻ-Thời sự', 'https://tuoitre.vn/rss/thoi-su.rss'),
    ('VietnamNet-Thời sự', 'https://vietnamnet.vn/rss/thoi-su.rss'),
    ('Dân Trí-Xã hội', 'https://dantri.com.vn/rss/xa-hoi.rss'),
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
    'mẹo vặt', 'bí quyết', 'cách làm', 'review', 'trải nghiệm', 'giảm cân', 'detox', 'làm giàu nhanh'
}

VIRAL_KEYWORDS = {
    'bắt', 'khởi tố', 'xét xử', 'tuyên án', 'điều tra', 'lừa đảo', 'tham nhũng', 'đấu thầu',
    'tăng giá', 'giảm giá', 'căng thẳng', 'tranh cãi', 'phản ứng', 'biểu tình', 'tai nạn',
    'cháy', 'nổ', 'đâm', 'vụ án', 'clip', 'rò rỉ', 'cảnh báo', 'dừng', 'cấm', 'đình chỉ'
}

CONTROVERSY_KEYWORDS = {
    'tranh cãi', 'phản ứng', 'bức xúc', 'phẫn nộ', 'làn sóng', 'gây sốc', 'chỉ trích', 'phản đối',
    'cấm', 'đình chỉ', 'đề xuất', 'tăng giá', 'giảm giá'
}

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

PRIOR_FILES_TO_SCAN = int(os.environ.get('PRIOR_FILES_TO_SCAN', '3'))
ROLLING_HOURS = int(os.environ.get('ROLLING_HOURS', '24'))
TARGET_STORIES = 5
FOCUS_KEYWORDS = [x.strip().lower() for x in os.environ.get('FOCUS_KEYWORDS', '').split('|') if x.strip()]
INCLUDE_YESTERDAY = os.environ.get('INCLUDE_YESTERDAY', '0') == '1'
MIN_FOCUS_MATCHES = int(os.environ.get('MIN_FOCUS_MATCHES', '3'))


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'ignore')


def strip_html(text: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


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


def normalize_story(entry, idx):
    title_vi = strip_html(entry['title'])
    desc_vi = strip_html(entry.get('description', ''))
    if not desc_vi:
        desc_vi = title_vi
    desc_vi = re.sub(r'^[\-–•\s]+', '', desc_vi)
    desc_vi = re.sub(r'\s*\[.*?\]\s*$', '', desc_vi).strip()
    lowered = (title_vi + ' ' + desc_vi).lower()
    if any(bad in lowered for bad in BLACKLIST_KEYWORDS):
        return None
    if desc_vi == title_vi:
        summary_vi = f"{title_vi}. Đây là diễn biến đáng chú ý cần tiếp tục theo dõi trong bản tin cập nhật lúc {RUN_HOUR}."
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
            ts_str = data.get('timestamp') or data.get('run_timestamp')
            ts_dt = None
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str)
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
    prior_tokens = set()
    prior_categories = set()
    prior_links = set()
    scanned = 0
    cutoff = runtime.now - timedelta(hours=ROLLING_HOURS)

    for ts, run_key, entries in parse_history_runs():
        if ts >= runtime.now:
            continue
        if ts < cutoff:
            break
        scanned += 1
        for story in entries:
            title = story.get('headline_vi') or ''
            summary = story.get('summary_vi') or ''
            prior_tokens.update(tokenize(title + ' ' + summary))
            category = story.get('category') or category_from_text(title, summary)
            prior_categories.add(category)
            source_url = story.get('source_url') or ''
            if source_url:
                prior_links.add(source_url)
        if scanned >= PRIOR_FILES_TO_SCAN:
            break
    return prior_tokens, prior_categories, prior_links, scanned


def pick_stories(pool):
    prior_tokens, prior_categories, prior_links, scanned = load_prior_headlines()

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

    ranked_pool = sorted(
        pool,
        key=lambda c: (
            focus_score(c),
            priority_score(c),
            viral_score(c['headline_vi'] + ' ' + c['summary_vi']),
            recency_score(c),
            len(c['tokens'])
        ),
        reverse=True,
    )

    focus_candidates = [c for c in ranked_pool if focus_score(c) > 0 and c.get('image_url')]
    selected = []
    used_links = set()
    used_categories = set()
    duplicate_pool = []

    for candidate in focus_candidates:
        if candidate['source_url'] in prior_links:
            duplicate_pool.append(candidate)
            continue
        overlap = len(set(candidate['tokens']) & prior_tokens)
        if overlap >= 6:
            continue
        selected.append(candidate)
        used_links.add(candidate['source_url'])
        used_categories.add(candidate['category'])
        if len(selected) >= min(MIN_FOCUS_MATCHES, TARGET_STORIES):
            break

    if not any(is_controversial(s['headline_vi'] + ' ' + s['summary_vi']) for s in selected):
        for candidate in ranked_pool:
            if candidate['source_url'] in used_links:
                continue
            if not candidate.get('image_url'):
                continue
            if is_controversial(candidate['headline_vi'] + ' ' + candidate['summary_vi']):
                selected.append(candidate)
                used_links.add(candidate['source_url'])
                used_categories.add(candidate['category'])
                break

    for candidate in ranked_pool:
        if candidate['source_url'] in used_links:
            continue
        if candidate['source_url'] in prior_links:
            duplicate_pool.append(candidate)
            continue
        overlap = len(set(candidate['tokens']) & prior_tokens)
        same_category_penalty = candidate['category'] in prior_categories and focus_score(candidate) == 0
        already_used_category = candidate['category'] in used_categories and focus_score(candidate) == 0
        if overlap >= 4 and focus_score(candidate) == 0:
            continue
        if already_used_category and len(selected) < TARGET_STORIES - 1:
            continue
        if same_category_penalty and len(selected) < TARGET_STORIES - 2:
            continue
        if not candidate.get('image_url'):
            continue
        selected.append(candidate)
        used_links.add(candidate['source_url'])
        used_categories.add(candidate['category'])
        if len(selected) == TARGET_STORIES:
            break

    if len(selected) < TARGET_STORIES:
        for candidate in ranked_pool:
            if candidate['source_url'] in used_links:
                continue
            if not candidate.get('image_url'):
                continue
            selected.append(candidate)
            used_links.add(candidate['source_url'])
            if len(selected) == TARGET_STORIES:
                break

    if len(selected) < TARGET_STORIES:
        for candidate in duplicate_pool:
            if candidate['source_url'] in used_links:
                continue
            if not candidate.get('image_url'):
                continue
            selected.append(candidate)
            used_links.add(candidate['source_url'])
            if len(selected) == TARGET_STORIES:
                break

    return selected, prior_categories, prior_links, scanned




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

stories, prior_categories, prior_links, prior_scanned = pick_stories(pool)
if len(stories) < TARGET_STORIES:
    raise RuntimeError(f'Not enough dynamic VN stories gathered. feeds={len(raw_entries)} selected={len(stories)} errors={feed_errors}')

stories = stories[:TARGET_STORIES]
for idx, story in enumerate(stories, start=1):
    story['id'] = f'story-{idx:02d}'

all_sources = []
for s in stories:
    src = s['sources'][0]
    all_sources.append(f"{s['headline_vi']} | {src['name']} | {src['url']}")

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
            'ffmpeg', '-y', '-i', src_img_path, '-vf',
            'scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,'
            'crop=1080:1920,eq=contrast=1.03:saturation=1.03,unsharp=7:7:1.2:7:7:0.0,format=yuvj420p',
            '-frames:v', '1', '-q:v', '1', out_img
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        story['prepared_image'] = out_img
        story['image_processing'] = 'center-crop+lanczos+unsharp'
    else:
        story['prepared_image'] = ''
        story['image_processing'] = 'image-missing'

intro_templates = [
    f"3 diễn biến đang khiến dư luận chú ý lúc {RUN_HOUR}.",
    f"Điểm nóng trong nước lúc {RUN_HOUR}: đây là 5 tin đáng bàn nhất.",
    f"Lúc {RUN_HOUR}, có những chuyển động đang tạo nhiều tranh luận.",
]
intro_vi = intro_templates[int(RUN_HHMM) % len(intro_templates)]
body_vi = '\n\n'.join(s['summary_vi'] for s in stories)
outro_vi = f"Bạn nghĩ tin nào tác động mạnh nhất? Đó là những điểm tin đáng chú ý lúc {RUN_HOUR}."
voice_vi = (intro_vi + "\n\n" + body_vi + "\n\n" + outro_vi).strip() + "\n"

intro_en = f"Here are the notable domestic developments updated at {RUN_HHMM[:2]}:00."
body_en = '\n\n'.join(s['summary_en'] for s in stories)
outro_en = f"Those are the key domestic headlines at {RUN_HHMM[:2]}:00."
voice_en = (intro_en + "\n\n" + body_en + "\n\n" + outro_en).strip() + "\n"

headline_list_vi = '\n'.join(s['headline_vi'] for s in stories) + '\n'
headline_list_en = '\n'.join(s['headline_en'] for s in stories) + '\n'
caption_vi = (
    f"Bản tin Việt Nam {RUN_HOUR}: " + '; '.join(s['headline_vi'] for s in stories[:3]) + '. '
    "Theo bạn, diễn biến nào đáng lo nhất hôm nay?"
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

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = '/home/minipc/keys/tts-sa.json'
client = texttospeech.TextToSpeechClient()
input_text = texttospeech.SynthesisInput(text=voice_vi)
voice = texttospeech.VoiceSelectionParams(language_code='vi-VN', name='vi-VN-Neural2-A')
audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16, speaking_rate=1.2)
response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
audio_path = RUN_DIR / 'voice_vi.wav'
audio_path.write_bytes(response.audio_content)

probe = lambda p: float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(p)]).decode().strip())
audio_duration = probe(audio_path)
seg_base = audio_duration / len(stories)
seg_durs = [round(seg_base, 3)] * len(stories)
seg_durs[-1] = round(audio_duration - sum(seg_durs[:-1]), 3)

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

anti_repeat_note = (
    'Dynamic RSS sourcing enabled. '
    + (
        f"Prior categories avoided where possible: {', '.join(sorted(prior_categories))}. "
        f"Rolling window: last {ROLLING_HOURS}h, runs scanned: {prior_scanned}, prior links: {len(prior_links)}."
        if prior_scanned else
        f"No recent metadata found in last {ROLLING_HOURS}h for anti-repeat comparison."
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
    'Prepared images: 1080x1920 center-crop + lanczos + unsharp',
    f'Anti-repeat note: {anti_repeat_note}',
]
for s in stories:
    metadata['stories'].append({
        'id': s['id'],
        'headline_vi': s['headline_vi'],
        'headline_en': s['headline_en'],
        'summary_vi': s['summary_vi'],
        'summary_en': s['summary_en'],
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
            'run_date': RUN_DATE,
            'run_hhmm': RUN_HHMM,
            'run_key': CURRENT_RUN_KEY,
            'headline_vi': story['headline_vi'],
            'summary_vi': story['summary_vi'],
            'source_url': story.get('source_url', ''),
            'category': story.get('category', ''),
        }
        entries.append(json.dumps(entry, ensure_ascii=False))
    if len(entries) > HISTORY_MAX_LINES:
        entries = entries[-HISTORY_MAX_LINES:]
    HISTORY_PATH.write_text('\n'.join(entries) + '\n', encoding='utf-8')


update_history(stories)

remote = f'gdrive:OpenClaw Database/news-videos-vn/{RUN_DATE}/{RUN_HHMM}/'
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
Path('/home/minipc/.openclaw/workspace/news-video-last-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))

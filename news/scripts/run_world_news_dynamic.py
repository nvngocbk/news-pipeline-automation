import html
import json
import os
import re
import shutil
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from news.core.runtime import get_runtime, world_video_filename

runtime = get_runtime()
RUN_DATE = runtime.run_date
RUN_HHMM = runtime.run_hhmm
RUN_HOUR = runtime.spoken_hour_vi
BASE = Path('/home/minipc/.openclaw/workspace/news-videos')
RUN_DIR = BASE / RUN_DATE / RUN_HHMM
SRC_DIR = RUN_DIR / 'source-images'
PREP_DIR = RUN_DIR / 'prepared'
TMP_DIR = RUN_DIR / 'tmp'
for path in [RUN_DIR, SRC_DIR, PREP_DIR, TMP_DIR]:
    path.mkdir(parents=True, exist_ok=True)

KEY = '/home/minipc/keys/tts-sa.json'
TRANSLATE_URL = 'https://translate.googleapis.com/translate_a/single'
TRANSLATE_CACHE = {}

FEEDS = [
    ('Reuters World News', 'https://www.reuters.com/world/rss'),
    ('Reuters Top News', 'https://www.reuters.com/rssFeed/topNews'),
    ('AP International', 'https://apnews.com/rss/apf-intlnews'),
    ('BBC World', 'http://feeds.bbci.co.uk/news/world/rss.xml'),
    ('The Guardian World', 'https://www.theguardian.com/world/rss'),
    ('DW Top Stories', 'https://rss.dw.com/xml/rss-en-top'),
    ('Al Jazeera', 'https://www.aljazeera.com/xml/rss/all.xml'),
    ('NYTimes World', 'https://rss.nytimes.com/services/xml/rss/nyt/World.xml'),
]

STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'have', 'from', 'this', 'will', 'says', 'said', 'after', 'over',
    'into', 'about', 'their', 'been', 'were', 'also', 'under', 'more', 'than', 'amid', 'between', 'near',
    'its', 'are', 'but', 'not', 'has', 'had', 'who', 'his', 'her', 'new', 'year', 'news', 'world', 'into',
    'during', 'when', 'into', 'make', 'made', 'could', 'should', 'would', 'amid', 'among', 'per', 'cent',
    'was', 'out', 'one', 'two', 'three', 'four', 'five', 'month', 'week', 'today', 'yesterday', 'monday',
    'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'government', 'people'
}

BLACKLIST_KEYWORDS = {
    'celebrity', 'actor', 'actress', 'singer', 'band', 'music', 'film', 'movie', 'hollywood', 'showbiz',
    'entertainment', 'sports', 'football', 'soccer', 'nba', 'tennis', 'golf', 'tiger woods', 'matthew perry',
    'grammy', 'oscars', 'fashion', 'runway', 'beauty', 'court', 'trial', 'lawsuit', 'sentenced', 'sentence',
    'drug', 'prescription', 'pop star', 'transfer window', 'champions league', 'premier league', 'box office'
}

VIRAL_KEYWORDS = {
    'war', 'attack', 'strike', 'missile', 'drone', 'ceasefire', 'sanction', 'tariff', 'election', 'protest',
    'crisis', 'escalation', 'warning', 'emergency', 'dead', 'killed', 'hostage', 'explosion', 'clash', 'threat'
}

CONTROVERSY_KEYWORDS = {
    'controversy', 'backlash', 'criticized', 'condemn', 'debate', 'disputed', 'allegation', 'accused',
    'protest', 'boycott', 'ceasefire', 'sanction', 'tariff'
}

CATEGORY_PRIORITY = {
    'xung đột': 3,
    'chính trị quốc tế': 3,
    'ngoại giao': 2,
    'kinh tế thế giới': 2,
    'quyền con người': 2,
    'biến đổi khí hậu': 1,
    'khoa học - công nghệ': 1,
    'thời sự quốc tế': 1,
}

META_DESCRIPTION_KEYS = [
    ('property', 'og:description'),
    ('name', 'description'),
    ('name', 'twitter:description')
]

PRIOR_FILES_TO_SCAN = 3
TARGET_STORIES = 5
FOCUS_KEYWORDS = [x.strip().lower() for x in os.environ.get('FOCUS_KEYWORDS', '').split('|') if x.strip()]
INCLUDE_YESTERDAY = os.environ.get('INCLUDE_YESTERDAY', '0') == '1'
MIN_FOCUS_MATCHES = int(os.environ.get('MIN_FOCUS_MATCHES', '3'))

headers = {'User-Agent': 'Mozilla/5.0'}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode('utf-8', 'ignore')


def strip_html(text: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', text or ''))).strip()


def trim_summary(text: str, max_chars: int = 320) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    idx = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
    if idx > 60:
        return cut[:idx + 1]
    return cut


def parse_image_from_html(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', text, re.I)
    return html.unescape(m.group(1)) if m else None


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def find_meta_content(html_text: str, attr_name: str, attr_value: str) -> str | None:
    pats = [
        rf'<meta[^>]+{attr_name}=["\']{re.escape(attr_value)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr_name}=["\']{re.escape(attr_value)}["\']',
    ]
    for pat in pats:
        m = re.search(pat, html_text, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def fetch_og_image(url: str) -> str | None:
    try:
        html_text = fetch_page(url)
    except Exception:
        return None
    for attr_name, attr_value in [('property', 'og:image'), ('name', 'twitter:image')]:
        content = find_meta_content(html_text, attr_name, attr_value)
        if content:
            return content
    return None


def fetch_better_summary(url: str) -> str:
    html_text = fetch_page(url)
    for attr_name, attr_value in META_DESCRIPTION_KEYS:
        content = find_meta_content(html_text, attr_name, attr_value)
        if content and len(content) >= 60:
            return trim_summary(strip_html(content))
    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html_text, re.I | re.S)
    for para in paragraphs:
        cleaned = trim_summary(strip_html(para))
        if len(cleaned) >= 80:
            return cleaned
    return ''


def is_truncated_summary(text: str) -> bool:
    text = strip_html(text or '')
    if not text:
        return True
    lowered = text.lower()
    if lowered.endswith('...') or lowered.endswith('…'):
        return True
    if len(text) < 70:
        return True
    if re.search(r'\b[a-z]{1,4}$', lowered) and text[-1].isalpha() and not text.endswith(('.', '!', '?', '”', '"')):
        return True
    bad_endings = ('for', 'with', 'into', 'from', 'after', 'before', 'during', 'about', 'through', 'because', 'residents searched for', 'searched for sur')
    return any(lowered.endswith(x) for x in bad_endings)


def is_bad_vi_summary(text: str) -> bool:
    text = strip_html(text or '')
    if not text:
        return True
    lowered = text.lower()
    if len(text) < 45:
        return True
    bad_endings = ('đã', 'đang', 'sẽ', 'để', 'và', 'nhưng', 'của', 'với', 'sau khi', 'người dân đã tìm kiếm')
    if any(lowered.endswith(x) for x in bad_endings):
        return True
    return not text.endswith(('.', '!', '?', '”', '"'))


def translate_to_vi(text: str) -> str:
    text = strip_html(text or '')
    if not text:
        return ''
    cached = TRANSLATE_CACHE.get(text)
    if cached:
        return cached
    resp = requests.get(
        TRANSLATE_URL,
        params={'client': 'gtx', 'sl': 'en', 'tl': 'vi', 'dt': 't', 'q': text},
        headers=headers,
        timeout=40,
    )
    if not resp.ok:
        raise RuntimeError(f'Translate failed: {resp.status_code} {resp.text[:200]}')
    data = resp.json()
    if not data or not data[0]:
        raise RuntimeError('Translate failed: empty response')
    translated = ''.join(seg[0] for seg in data[0] if seg and seg[0]).strip()
    if not translated:
        raise RuntimeError('Translate failed: empty translation')
    TRANSLATE_CACHE[text] = translated
    return translated


def tokenize(text: str):
    text = strip_html(text).lower()
    text = re.sub(r'["\'`.,?!:;()\[\]\-]', ' ', text)
    tokens = [t for t in text.split() if len(t) >= 4 and t not in STOPWORDS and not t.isdigit()]
    return tokens


def viral_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for kw in VIRAL_KEYWORDS if kw in lowered)


def is_controversial(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in CONTROVERSY_KEYWORDS)


def parse_pub_date(pub_date_raw: str):
    if not pub_date_raw:
        return None
    try:
        dt = parsedate_to_datetime(pub_date_raw)
        return dt
    except Exception:
        return None


def category_from_text(title: str, summary: str):
    text = (title + ' ' + summary).lower()
    rules = [
        ('chính trị quốc tế', ['president', 'prime minister', 'parliament', 'election', 'government', 'minister', 'senate', 'white house', 'congress', 'coup']),
        ('xung đột', ['war', 'conflict', 'attack', 'strike', 'missile', 'battle', 'frontline', 'troop', 'military', 'army', 'drone']),
        ('ngoại giao', ['talks', 'summit', 'meeting', 'negotiation', 'peace', 'deal', 'agreement', 'accord']),
        ('kinh tế thế giới', ['economy', 'market', 'oil', 'trade', 'export', 'import', 'inflation', 'currency', 'stock']),
        ('biến đổi khí hậu', ['climate', 'heat', 'storm', 'hurricane', 'flood', 'drought', 'wildfire', 'weather', 'temperature']),
        ('khoa học - công nghệ', ['tech', 'technology', 'ai', 'space', 'satellite', 'rocket', 'nasa', 'moon', 'launch', 'science']),
        ('quyền con người', ['human rights', 'refugee', 'asylum', 'rights', 'court', 'protest', 'activist']),
    ]
    for name, keys in rules:
        if any(k in text for k in keys):
            return name
    return 'thời sự quốc tế'


def is_blacklisted(text: str) -> bool:
    lowered = text.lower()
    return any(k in lowered for k in BLACKLIST_KEYWORDS)


def parse_rss(feed_name: str, url: str):
    xml_text = fetch(url)
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall('.//item')[:15]:
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
            if tag.endswith('content') and 'url' in child.attrib:
                image_url = image_url or child.attrib.get('url')
            if tag.endswith('thumbnail') and 'url' in child.attrib:
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


def normalize_story(entry, idx):
    headline_en = strip_html(entry['title'])
    summary_en = trim_summary(strip_html(entry.get('description', '')))
    if not summary_en:
        summary_en = headline_en
    if is_blacklisted(f'{headline_en} {summary_en}'):
        return None
    if entry.get('link') and is_truncated_summary(summary_en):
        try:
            better = fetch_better_summary(entry['link'])
            if better:
                summary_en = better
        except Exception:
            pass
    if is_truncated_summary(summary_en):
        return None
    pub_dt = parse_pub_date(entry.get('pub_date', ''))
    if entry.get('image_url') is None and entry.get('link'):
        entry['image_url'] = fetch_og_image(entry['link'])
    headline_vi = translate_to_vi(headline_en)
    summary_vi_core = translate_to_vi(summary_en or headline_en)
    if summary_vi_core and not summary_vi_core.endswith(('.', '!', '?')):
        summary_vi_core += '.'
    summary_vi = f"Theo {entry['source_name']}, {summary_vi_core}".strip()
    if is_bad_vi_summary(summary_vi):
        return None
    tokens = tokenize(headline_en + ' ' + summary_en)
    category = category_from_text(headline_en, summary_en)
    return {
        'id': f'story-{idx:02d}',
        'headline_en': headline_en,
        'headline_vi': headline_vi,
        'summary_en': summary_en,
        'summary_vi': summary_vi,
        'source_name': entry['source_name'],
        'source_url': entry['link'],
        'image_url': entry.get('image_url'),
        'pub_date': entry.get('pub_date', ''),
        'pub_dt': pub_dt,
        'tokens': tokens,
        'category': category,
    }


def load_prior_tokens():
    prior_tokens = set()
    prior_categories = set()
    date_dirs = [BASE / RUN_DATE]
    if INCLUDE_YESTERDAY:
        y = (runtime.now - timedelta(days=1)).strftime('%Y-%m-%d')
        date_dirs.append(BASE / y)
    for date_dir in date_dirs:
        if not date_dir.exists():
            continue
        runs = sorted([p for p in date_dir.iterdir() if p.is_dir()], reverse=True)
        filtered = []
        for run in runs:
            if date_dir.name == RUN_DATE and run.name >= RUN_HHMM:
                continue
            filtered.append(run)
        for run in filtered[:PRIOR_FILES_TO_SCAN]:
            meta = run / 'metadata.json'
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text(encoding='utf-8'))
            except Exception:
                continue
            for story in data.get('stories', []):
                text = (story.get('headline_en') or '') + ' ' + (story.get('summary_en') or '')
                prior_tokens.update(tokenize(text))
                if story.get('category'):
                    prior_categories.add(story['category'])
    return prior_tokens, prior_categories


def focus_score(candidate):
    if not FOCUS_KEYWORDS:
        return 0
    text = (candidate['headline_en'] + ' ' + candidate['summary_en']).lower()
    return sum(1 for kw in FOCUS_KEYWORDS if kw in text)


def pick_stories(pool):
    prior_tokens, prior_categories = load_prior_tokens()

    def priority_score(candidate):
        return CATEGORY_PRIORITY.get(candidate.get('category', 'thời sự quốc tế'), 0)

    def recency_score(candidate):
        if not candidate.get('pub_dt'):
            return 0
        delta = (runtime.now - candidate['pub_dt']).total_seconds() / 3600
        return max(0, 24 - delta)

    ranked_pool = sorted(
        pool,
        key=lambda c: (
            focus_score(c),
            priority_score(c),
            viral_score(c['headline_en'] + ' ' + c['summary_en']),
            recency_score(c),
            len(c['tokens'])
        ),
        reverse=True,
    )
    selected = []
    used_links = set()
    used_categories = set()

    focus_candidates = [c for c in ranked_pool if focus_score(c) > 0 and c.get('image_url')]
    for candidate in focus_candidates:
        overlap = len(set(candidate['tokens']) & prior_tokens)
        if overlap >= 6:
            continue
        if candidate['source_url'] in used_links:
            continue
        selected.append(candidate)
        used_links.add(candidate['source_url'])
        used_categories.add(candidate['category'])
        if len(selected) >= min(MIN_FOCUS_MATCHES, TARGET_STORIES):
            break

    if not any(is_controversial(s['headline_en'] + ' ' + s['summary_en']) for s in selected):
        for candidate in ranked_pool:
            if candidate['source_url'] in used_links:
                continue
            if not candidate.get('image_url'):
                continue
            if is_controversial(candidate['headline_en'] + ' ' + candidate['summary_en']):
                selected.append(candidate)
                used_links.add(candidate['source_url'])
                used_categories.add(candidate['category'])
                break

    for candidate in ranked_pool:
        if candidate['source_url'] in used_links:
            continue
        if not candidate.get('image_url'):
            continue
        overlap = len(set(candidate['tokens']) & prior_tokens)
        fs = focus_score(candidate)
        if overlap >= 5 and fs == 0:
            continue
        if candidate['category'] in used_categories and fs == 0 and len(selected) < TARGET_STORIES - 1:
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
    return selected, prior_categories


raw_entries = []
feed_errors = []
for name, url in FEEDS:
    try:
        raw_entries.extend(parse_rss(name, url))
    except Exception as e:
        feed_errors.append(f'{name}: {e}')

seen = set()
pool = []
for idx, entry in enumerate(raw_entries, start=1):
    link = entry.get('link')
    if not link or link in seen:
        continue
    seen.add(link)
    story = normalize_story(entry, idx)
    if not story or not story.get('headline_en'):
        continue
    pool.append(story)

stories, prior_categories = pick_stories(pool)
if len(stories) < TARGET_STORIES:
    raise RuntimeError(f'Not enough world stories gathered. feeds={len(raw_entries)} selected={len(stories)} errors={feed_errors}')

stories = stories[:TARGET_STORIES]
for idx, story in enumerate(stories, start=1):
    story['id'] = f'story-{idx:02d}'

all_sources = []
for s in stories:
    all_sources.append(f"{s['headline_en']} | {s['source_name']} | {s['source_url']}")

for story in stories:
    img_url = story.get('image_url')
    ext = '.jpg'
    if img_url and '.png' in img_url.lower():
        ext = '.png'
    src_path = SRC_DIR / f"{story['id']}{ext}"
    if img_url:
        try:
            req = urllib.request.Request(img_url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as r, open(src_path, 'wb') as f:
                f.write(r.read())
        except Exception:
            img_url = None
    if not img_url or not src_path.exists():
        raise RuntimeError(f"Story {story['id']} missing usable image: {story['headline_en']}")
    story['source_image_path'] = str(src_path)
    out_path = PREP_DIR / f"{story['id']}-vertical.jpg"
    cmd = [
        'ffmpeg', '-y', '-i', str(src_path), '-filter_complex',
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:10[bg];"
        "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,unsharp=5:5:0.8:5:5:0.0[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuvj420p",
        '-frames:v', '1', '-q:v', '2', str(out_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    story['prepared_image'] = str(out_path)
    story['image_processing'] = 'fit+blur'

intro_templates = [
    f"Tin số 1 sáng nay có thể làm cục diện quốc tế thay đổi lúc {RUN_HOUR}.",
    f"5 diễn biến thế giới đang gây tranh luận mạnh lúc {RUN_HOUR}.",
    f"Nếu chỉ xem 1 bản tin lúc {RUN_HOUR}, đây là 5 điểm nóng đáng theo dõi.",
]
intro_vi = intro_templates[int(RUN_HHMM) % len(intro_templates)]
body_vi = '\n'.join(s['summary_vi'] for s in stories)
outro_vi = f"Theo bạn, diễn biến nào có thể leo thang tiếp? Đó là những điểm nóng quốc tế lúc {RUN_HOUR}."
voice_vi = (intro_vi + '\n' + body_vi + '\n' + outro_vi).strip() + '\n'

headline_list_vi = '\n'.join(s['headline_vi'] for s in stories) + '\n'
headline_list_en = '\n'.join(s['headline_en'] for s in stories) + '\n'
caption_vi = (
    f"Bản tin thế giới {RUN_HOUR}: " + '; '.join(s['headline_vi'] for s in stories[:3]) + '. '
    "Bạn nghĩ điểm nóng nào có thể tác động lớn nhất 24h tới?"
)
hashtags = '#tinthegioi #worldnews #quocte #internationalnews #OpenClaw'

files = {
    'voice_vi.txt': voice_vi,
    'headline-list_vi.txt': headline_list_vi,
    'headline-list_en.txt': headline_list_en,
    'tiktok-caption_vi.txt': caption_vi + '\n',
    'tiktok-hashtags.txt': hashtags + '\n',
    'sources.txt': '\n'.join(all_sources + ([f"Feed errors: {' | '.join(feed_errors)}"] if feed_errors else [])) + '\n',
}
for name, content in files.items():
    (RUN_DIR / name).write_text(content, encoding='utf-8')

creds = service_account.Credentials.from_service_account_file(KEY, scopes=['https://www.googleapis.com/auth/cloud-platform'])
creds.refresh(Request())
voice_candidates = ['vi-VN-Chirp3-HD-Aoede', 'vi-VN-Neural2-A', 'vi-VN-Wavenet-A']
audio_path = RUN_DIR / 'voice_vi.mp3'
chosen_voice = None
last_err = None
for voice_name in voice_candidates:
    resp = requests.post(
        'https://texttospeech.googleapis.com/v1/text:synthesize',
        headers={'Authorization': f'Bearer {creds.token}', 'Content-Type': 'application/json'},
        json={
            'input': {'text': voice_vi},
            'voice': {'languageCode': 'vi-VN', 'name': voice_name},
            'audioConfig': {'audioEncoding': 'MP3', 'speakingRate': 1.15},
        },
        timeout=120,
    )
    if resp.ok and 'audioContent' in resp.json():
        import base64
        audio_path.write_bytes(base64.b64decode(resp.json()['audioContent']))
        chosen_voice = voice_name
        break
    last_err = f'{voice_name}: {resp.status_code} {resp.text[:300]}'
if not chosen_voice:
    raise RuntimeError(f'TTS failed: {last_err}')

probe = lambda p: float(subprocess.check_output([
    'ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(p)
], text=True).strip())
audio_duration = probe(audio_path)
per = audio_duration / len(stories)
concat_list = RUN_DIR / 'images.txt'
lines = []
for story in stories:
    lines.append(f"file '{story['prepared_image']}'")
    lines.append(f'duration {per:.6f}')
lines.append(f"file '{stories[-1]['prepared_image']}'")
concat_list.write_text('\n'.join(lines) + '\n', encoding='utf-8')

video_name = world_video_filename(RUN_DATE, RUN_HHMM)
video_path = RUN_DIR / video_name
subprocess.run([
    'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_list), '-i', str(audio_path),
    '-vf', 'scale=1080:1920,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', '-shortest', str(video_path)
], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
video_duration = probe(video_path)

anti_repeat_note = (
    'Dynamic RSS sourcing enabled. '
    + (
        f"Prior categories avoided where possible: {', '.join(sorted(prior_categories))}."
        if prior_categories else
        'No earlier same-day metadata available for anti-repeat comparison.'
    )
)

metadata = {
    'run_date': RUN_DATE,
    'run_hhmm': RUN_HHMM,
    'run_datetime_local': runtime.iso_local,
    'timezone': runtime.tz_name,
    'edition_label': f'world-headlines-{RUN_HHMM}',
    'tts': {
        'provider': 'Google Cloud Text-to-Speech',
        'voice': chosen_voice,
        'languageCode': 'vi-VN',
        'speakingRate': 1.15,
        'audio_file': audio_path.name,
        'audio_duration_seconds': audio_duration,
    },
    'video': {
        'file': video_name,
        'duration_seconds': video_duration,
        'resolution': '1080x1920',
    },
    'stories': [],
    'anti_repeat_note': anti_repeat_note,
    'feed_errors': feed_errors,
}
for story in stories:
    metadata['stories'].append({
        'id': story['id'],
        'headline_en': story['headline_en'],
        'headline_vi': story['headline_vi'],
        'summary_en': story['summary_en'],
        'summary_vi': story['summary_vi'],
        'source_name': story['source_name'],
        'source_url': story['source_url'],
        'image_url': story['image_url'],
        'prepared_image': Path(story['prepared_image']).name,
        'category': story['category'],
        'pub_date': story['pub_date'],
    })
(RUN_DIR / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

manifest_lines = [
    f'Run directory: news-videos/{RUN_DATE}/{RUN_HHMM}',
    f'Final video: {video_name}',
    f'Audio duration: {audio_duration:.3f}s',
    f'Video duration: {video_duration:.3f}s',
    f'TTS voice: {chosen_voice}',
    f'Anti-repeat note: {anti_repeat_note}',
]
for story in stories:
    manifest_lines.append(
        f"{story['id']}: {story['headline_vi']} | category={story['category']} | image={Path(story['prepared_image']).name}"
    )
(RUN_DIR / 'manifest.txt').write_text('\n'.join(manifest_lines) + '\n', encoding='utf-8')

remote = f'gdrive:OpenClaw Database/news-videos/{RUN_DATE}/{RUN_HHMM}/'
copy_proc = subprocess.run(['rclone', 'copy', str(RUN_DIR), remote, '--create-empty-src-dirs', '--checksum'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
list_proc = subprocess.run(['rclone', 'lsf', remote, '-R'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
local_files = []
for root, dirs, files in os.walk(RUN_DIR):
    for fn in files:
        local_files.append(os.path.relpath(os.path.join(root, fn), RUN_DIR))
local_set = set(sorted(local_files))
remote_set = set(sorted([line.strip() for line in list_proc.stdout.decode().splitlines() if line.strip()]))
missing = sorted(local_set - remote_set)
extra = sorted(remote_set - local_set)
upload_ok = copy_proc.returncode == 0 and list_proc.returncode == 0 and not missing
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
    'video_file': video_name,
    'headlines': [s['headline_vi'] for s in stories],
    'anti_repeat_note': anti_repeat_note,
    'local_deleted': False,
}
if upload_ok:
    shutil.rmtree(RUN_DIR)
    summary['local_deleted'] = True
(BASE / 'news-video-last-summary-world.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))

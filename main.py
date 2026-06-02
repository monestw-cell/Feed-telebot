import os
import re
import html
import logging
import sqlite3
import hashlib
import time
import threading
import asyncio
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
import feedparser
from deep_translator import GoogleTranslator
from PIL import Image, ImageDraw
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from apscheduler.schedulers.background import BackgroundScheduler

# --- 1. إعداد سجل التشغيل والأخطاء ---
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
YOUR_CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")
PORT = int(os.environ.get("PORT", 8080))

# المصادر الشاملة (تقنية + ذكاء اصطناعي + سياسة + كرة قدم عالمية)
RSS_FEEDS = [
    # مصادر تقنية وهواتف و AI
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.artificialintelligence-news.com/feed/",
    "https://www.androidcentral.com/feed",
    "https://www.macrumors.com/macrumors.xml",
    "https://www.gsmarena.com/rss-news-reviews.php3",
    "https://www.tomshardware.com/feeds/all",
    "https://9to5google.com/feed/",
    "https://9to5mac.com/feed/",
    "https://www.engadget.com/rss.xml",
    "https://arstechnica.com/feed/",
    "https://www.wired.com/feed/rss",
    "https://aitnews.com/feed/",
    "https://tech-wd.com/feed/",
    "https://www.unlimit-tech.com/feed/",
    "https://openai.com/blog/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://blogs.nvidia.com/feed/",
    # مصادر سياسية وأخبار عالمية
    "https://www.aljazeera.net/aljazeerarss.xml",
    "https://arabic.rt.com/rss/",
    "https://www.bbc.com/arabic/index.xml",
    # مصادر كرة القدم العالمية والانتقالات والنتائج
    "https://www.goal.com/ar/feeds/news",
    "https://www.filgoal.com/news.xml",
    "https://www.skysports.com/football/rss"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# قاموس المصطلحات لمنع الترجمات الحرفية الخاطئة
TECH_GLOSSARY = {
    "ذكاء التفاحة": "ذكاء آبل (Apple Intelligence)",
    "الذكاء الاصطناعي الإنجابي": "الذكاء الاصطناعي التوليدي",
    "ذكاء اصطناعي إنجابي": "ذكاء اصطناعي توليدي",
    "رقاقة": "معالج رقمي",
    "دردشة جي بي تي": "تطبيق ChatGPT",
    "أندرويد سنترال": "Android Central",
    "ماك رومرز": "MacRumors"
}

# الهاشتاجات التلقائية الذكية لكل تصنيف لدعم نمو القناة
AUTO_HASHTAGS = {
    "🤖 AI": "\n\n#ذكاء_اصطناعي #ذكاء_توليدي #تقنية #تكنولوجيا",
    "🍎 Apple": "\n\n#آبل #آيفون #تكنولوجيا_آبل #iOS",
    "📱 Mobile": "\n\n#هواتف #أندرويد #سامسونج #جوالات",
    "🚀 Startups": "\n\n#شركات_ناشئة #استثمارات #ريادة_أعمال",
    "📢 سياسة": "\n\n#أخبار_السياسة #عاجل #أخبار_العالم #سياسة",
    "⚽ كرة القدم": "\n\n#كرة_القدم #انتقالات_اللاعبين #الدوري_الانجليزي #ريال_مدريد #برشلونة #مباراة",
    "💻 Tech": "\n\n#أخبار_التقنية #تكنولوجيا #علوم #عالم_التقنية",
    "🚨 عاجل | Breaking": "\n\n#عاجل #أخبار_عاجلة #BreakingNews"
}

# --- 2. خادم الحفاظ على النشاط (Render Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Premium News Bot Automation Engine is Live!")
    def log_message(self, format, *args):
        return

def run_health_server():
    logging.info(f"🚀 تشغيل خادم الويب الداخلي على المنفذ: {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    server.serve_forever()

# --- 3. إدارة قاعدة البيانات وتنظيف الروابط ---
DB_FILE = "bulletproof_news.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed_news (
                        url TEXT PRIMARY KEY,
                        title_hash TEXT)''')
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_title_hash ON processed_news (title_hash)")
    conn.commit()
    conn.close()

def clean_url(url):
    try:
        parsed = urlparse(url)
        query = parsed.query
        if 'utm_' in query or 'ref=' in query:
            queries = [q for q in query.split('&') if not q.startswith('utm_') and not q.startswith('ref=')]
            query = '&'.join(queries)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment)).rstrip('/')
    except Exception:
        return url

def generate_clean_hash(title):
    clean_title = re.sub(r'\W+', '', title.lower().strip())
    return hashlib.md5(clean_title.encode('utf-8')).hexdigest()

def is_duplicate(title, url):
    title_hash = generate_clean_hash(title)
    cleaned_url = clean_url(url)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_news WHERE url = ? OR title_hash = ?", (cleaned_url, title_hash))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_news(title, url):
    title_hash = generate_clean_hash(title)
    cleaned_url = clean_url(url)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO processed_news (url, title_hash) VALUES (?, ?)", (cleaned_url, title_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

# --- 4. نظام التصنيف التلقائي الذكي ومحرك الأخبار العاجلة ---
def classify_article(title, content):
    text_to_scan = f"{title} {content}".lower()
    
    if any(x in text_to_scan for x in ["breaking", "urgent", "عاجل", "🚨"]):
        return "🚨 عاجل | Breaking"
        
    categories = {
        "🤖 AI": ["ai", "artificial intelligence", "openai", "chatgpt", "llm", "claude", "anthropic", "deepmind", "gemini", "copilot", "machine learning", "meta ai", "xai", "mistral", "ذكاء اصطناعي", "توليدي"],
        "⚽ كرة القدم": ["انتقالات", "مباراة", "هدف", "برشلونة", "مدريد", "دوري", "لاعب", "اهداف", "كورة", "ملاعب", "تعاقد", "football", "transfer", "match", "goal", "ucl", "chelsea", "liverpool", "bayern", "la liga", "premier league", "الدوري"],
        "📢 سياسة": ["انتخابات", "رئيس", "وزير", "غارة", "معاهدة", "قمة", "تصويت", "البرلمان", "سياسة", "مفاوضات", "هدنة", "جيش", "قوات", "government", "president", "minister", "election", "parliament", "military", "treaty", "summit", "politics"],
        "🍎 Apple": ["apple", "iphone", "ipad", "mac", "macbook", "ios", "macrumors", "9to5mac", "apple watch", "آبل", "آيفون"],
        "📱 Mobile": ["mobile", "android", "smartphone", "gsmarena", "samsung", "galaxy", "pixel", "snapdragon", "xiaomi", "9to5google", "androidcentral", "هاتف", "أندرويد", "جوال"],
        "🚀 Startups": ["startup", "funding", "venture", "techcrunch", "acquisition", "ipo", "raised", "تمويل", "شركة ناشئة", "استحواذ"]
    }
    for category, keywords in categories.items():
        if any(keyword in text_to_scan for keyword in keywords):
            return category
    return "💻 Tech"

# --- 5. محرك الختم المائي التلقائي الذكي للصور (Auto-Watermark) ---
def apply_watermark(image_content):
    """تحميل الصورة برمجياً وطباعة يوزر القناة في الزاوية لحفظ الحقوق والحصريّة"""
    try:
        img = Image.open(io.BytesIO(image_content)).convert("RGB")
        draw = ImageDraw.Draw(img)
        text = "@FeedTelebot"
        
        # حساب مكان الطباعة تلقائياً (الزاوية اليمنى السفلى) بناءً على أبعاد الصورة
        width, height = img.size
        x = width - 140
        y = height - 40
        
        # رسم ظل أسود خلف النص لضمان وضوحه التام مهما كانت خلفية الصورة بيضاء أو سوداء
        draw.text((x + 1, y + 1), text, fill=(0, 0, 0))
        draw.text((x, y), text, fill=(255, 255, 255))
        
        # حفظ النتيجة في بافر الذاكرة دون الكتابة على القرص لتوفير موارد السيرفر
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85)
        output.seek(0)
        return output
    except Exception as e:
        logging.error(f"⚠️ فشل تطبيق العلامة المائية: {e}")
        return io.BytesIO(image_content) # في حال الفشل نمرر الصورة الأصلية دون كراش

# --- 6. كشط محلي آمن والتحقق من الصور عبر Stream ---
def get_image_bytes(url):
    if not url:
        return None
    try:
        response = requests.get(url, headers=HEADERS, timeout=5, stream=True)
        if response.status_code == 200 and "image" in response.headers.get("Content-Type", "").lower():
            content = response.content
            response.close()
            return content
        response.close()
    except Exception:
        pass
    return None

def scrape_and_extract(url, entry):
    img_url = None
    extracted_paragraphs = []
    
    if 'media_content' in entry and len(entry.media_content) > 0:
        img_url = entry.media_content[0].get('url')
    elif 'links' in entry:
        for link in entry.links:
            if 'image' in link.get('type', ''):
                img_url = link.get('href')
                break

    try:
        response = requests.get(url, headers=HEADERS, timeout=8)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            
            if not img_url:
                og_img = soup.find('meta', property='og:image') or soup.find('meta', attrs={'name': 'og:image'})
                if og_img and og_img.get('content'):
                    img_url = og_img['content']
            
            for p in soup.find_all('p'):
                p_text = p.get_text().strip()
                if len(p_text) > 60 and not any(x in p_text.lower() for x in ["cookie", "subscribe", "sign up", "privacy policy", "terms of service", "all rights reserved"]):
                    
                    if any(x in p_text.lower() for x in ["by ", "author", "director", "manager", "researcher", "analyst", "بواسطة", "الكاتب", "باحث", "مدير"]):
                        if p_text.count('|') >= 1 or p_text.count(',') > 3:
                            continue
                            
                    extracted_paragraphs.append(p_text)
                if len(extracted_paragraphs) == 2:
                    break
    except Exception as e:
        logging.error(f"⚠️ خطأ أثناء كشط الرابط {url}: {e}")
        
    if not extracted_paragraphs:
        rss_summary = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
        extracted_paragraphs = [rss_summary] if rss_summary else []
        
    # جلب داتا الصورة المباشرة لغرض معالجة الختم
    image_bytes = get_image_bytes(img_url)
        
    return image_bytes, " ".join(extracted_paragraphs)

# --- 7. محرك الترجمة الفورية والتهذيب وتطبيق القاموس اللغوي ---
def translate_and_refine(text, max_chars=300):
    if not text.strip():
        return ""
    if bool(re.search(r'[\u0600-\u06FF]', text)): 
        return text[:max_chars]
        
    try:
        translator = GoogleTranslator(source='auto', target='ar')
        clean_input = text[:max_chars].strip()
        translated = translator.translate(clean_input)
        
        for wrong_term, correct_term in TECH_GLOSSARY.items():
            translated = translated.replace(wrong_term, correct_term)
            
        sentences = translated.split('.')
        refined_sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        
        final_text = " | ".join(refined_sentences[:3])
        return final_text[:max_chars]
    except Exception as e:
        logging.error(f"⚠️ فشل محرك الترجمة: {e}")
        return ""

# --- 8. المنظومة الأساسية لمعالجة وبث الأخبار لتيليجرام ---
async def process_news_pipeline(is_first_run=False):
    if is_first_run:
        logging.info("🛡️ [التشغيل الأول الصامت] جاري أرشفة القائمة الأساسية لحماية القناة...")
    else:
        logging.info("⏳ بدء دورة فحص الأخبار الجديدة (كل 15 دقيقة)...")
        
    bot = Bot(token=TELEGRAM_TOKEN)
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            
            if hasattr(feed, 'bozo') and feed.bozo:
                logging.warning(f"❌ خطأ في صياغة الـ RSS للمصدر: {feed_url}")
            
            if not feed.entries:
                logging.warning(f"⚠️ المصدر فارغ أو غير نشط حالياً: {feed_url}")
                continue
            
            entries = feed.entries[:3]
            
            for entry in entries:
                news_url = entry.link
                original_title = entry.title
                
                if is_duplicate(original_title, news_url):
                    continue
                
                if is_first_run:
                    save_news(original_title, news_url)
                    continue
                
                logging.info(f"📰 خبر جديد مكتشف: {original_title}")
                
                image_bytes, summary_text = scrape_and_extract(news_url, entry)
                category = classify_article(original_title, summary_text)
                
                translated_title = translate_and_refine(original_title, max_chars=120)
                translated_summary = translate_and_refine(summary_text, max_chars=350)
                
                if not translated_title:
                    translated_title = original_title
                
                # جلب الهاشتاج التلقائي المدمج للفئة المكتشفة
                hashtags = AUTO_HASHTAGS.get(category, "\n\n#تقنية #أخبار")
                
                safe_category = html.escape(category)
                safe_title = html.escape(translated_title)
                safe_summary = html.escape(translated_summary)
                
                message_template = (
                    f"{safe_category}\n"
                    f"<b>{safe_title}</b>\n\n"
                    f"🔹 {safe_summary}"
                    f"{hashtags}"
                )
                
                reply_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton(text="🌐 اقرأ الخبر من المصدر الأصلي", url=news_url)]
                ])
                
                try:
                    if image_bytes:
                        # تطبيق الختم المائي التلقائي الذكي على الصورة الحية قبل إرسالها
                        watermarked_image = apply_watermark(image_bytes)
                        
                        await bot.send_photo(
                            chat_id=YOUR_CHAT_ID,
                            photo=watermarked_image,
                            caption=message_template,
                            parse_mode=ParseMode.HTML,
                            reply_markup=reply_markup
                        )
                    else:
                        await bot.send_message(
                            chat_id=YOUR_CHAT_ID,
                            text=message_template,
                            parse_mode=ParseMode.HTML,
                            reply_markup=reply_markup,
                            disable_web_page_preview=True
                        )
                    
                    save_news(original_title, news_url)
                    logging.info(f"✅ تم بث الخبر بنجاح: {original_title}")
                    await asyncio.sleep(4) 
                    
                except Exception as tx:
                    logging.error(f"❌ خطأ أثناء النشر في تيليجرام: {tx}")
                    
        except Exception as feed_parse_error:
            logging.error(f"❌ فشل فحص أو معالجة رابط الـ RSS بالكامل للمصدر {feed_url}: {feed_parse_error}")

def trigger_async_pipeline(is_first_run=False):
    asyncio.run(process_news_pipeline(is_first_run=is_first_run))

# --- 9. تشغيل المحرك والمجدول الدوري الحامي من التداخل ---
if __name__ == '__main__':
    init_db()
    
    threading.Thread(target=run_health_server, daemon=True).start()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        trigger_async_pipeline, 
        'interval', 
        minutes=15,
        max_instances=1,  
        coalesce=True     
    )
    scheduler.start()
    logging.info("⏰ تم تفعيل مجدول الأخبار الاحترافي المطور (كل 15 دقيقة).")
    
    trigger_async_pipeline(is_first_run=True)
    logging.info("✅ تم إكمال الأرشفة الافتتاحية بنجاح واصطياد الهوية الذكية!")
    
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logging.info("🛑 تم إيقاف النظام.")

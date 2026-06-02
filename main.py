import os
import re
import html
import logging
import sqlite3
import hashlib
import time
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from bs4 import BeautifulSoup
import feedparser
from deep_translator import GoogleTranslator
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.background import BackgroundScheduler

# --- 1. إعداد سجل الأخطاء الاحترافي ---
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
YOUR_CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")
PORT = int(os.environ.get("PORT", 8080))

RSS_FEEDS = [
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
    "https://about.fb.com/news/category/artificial-intelligence/feed/",
    "https://deepmind.google/blog/rss.xml",
    "https://microsoft.com/en-us/research/feed/",
    "https://blogs.nvidia.com/feed/"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# --- 2. خادم الحفاظ على النشاط (Render Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot Engine is stable and running.")
    def log_message(self, format, *args):
        return

def run_health_server():
    logging.info(f"🚀 تشغيل خادم الويب الداخلي على المنفذ: {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    server.serve_forever()

# --- 3. إدارة قاعدة البيانات (آمنة مضاف لها الفهرس للبحث السريع) ---
DB_FILE = "bulletproof_news.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed_news (
                        url TEXT PRIMARY KEY,
                        title_hash TEXT)''')
    # تحسين الأداء: إضافة الفهرس لمنع الـ Full Table Scan مع نمو البيانات وضمان سرعة O(1)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_title_hash ON processed_news (title_hash)")
    conn.commit()
    conn.close()

def generate_clean_hash(title):
    clean_title = re.sub(r'\W+', '', title.lower().strip())
    return hashlib.md5(clean_title.encode('utf-8')).hexdigest()

def is_duplicate(title, url):
    title_hash = generate_clean_hash(title)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_news WHERE url = ? OR title_hash = ?", (url, title_hash))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_news(title, url):
    title_hash = generate_clean_hash(title)
    conn = sqlite3.connect(DB_FILE, timeout=30)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO processed_news (url, title_hash) VALUES (?, ?)", (url, title_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

# --- 4. نظام التصنيف التلقائي الذكي ---
def classify_article(title, content):
    text_to_scan = f"{title} {content}".lower()
    categories = {
        "🤖 AI": ["ai", "artificial intelligence", "openai", "chatgpt", "llm", "claude", "anthropic", "deepmind", "gemini", "copilot", "machine learning", "meta ai", "xai", "mistral", "ذكاء اصطناعي", "توليدي"],
        "🍎 Apple": ["apple", "iphone", "ipad", "mac", "macbook", "ios", "macrumors", "9to5mac", "apple watch", "آبل", "آيفون"],
        "📱 Mobile": ["mobile", "android", "smartphone", "gsmarena", "samsung", "galaxy", "pixel", "snapdragon", "xiaomi", "9to5google", "androidcentral", "هاتف", "أندرويد", "جوال"],
        "🚀 Startups": ["startup", "funding", "venture", "techcrunch", "acquisition", "ipo", "raised", "تمويل", "شركة ناشئة", "استحواذ"]
    }
    for category, keywords in categories.items():
        if any(keyword in text_to_scan for keyword in keywords):
            return category
    return "💻 Tech"

# --- 5. كشط محلي آمن ومحقق للصور عبر Stream (معالجة خطأ 405) ---
def is_image_valid(url):
    if not url:
        return False
    try:
        response = requests.get(url, headers=HEADERS, timeout=5, stream=True)
        is_ok = response.status_code == 200 and "image" in response.headers.get("Content-Type", "").lower()
        response.close()
        return is_ok
    except Exception:
        return False

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
                    extracted_paragraphs.append(p_text)
                if len(extracted_paragraphs) == 2:
                    break
    except Exception as e:
        logging.error(f"⚠️ خطأ أثناء كشط الرابط {url}: {e}")
        
    if not extracted_paragraphs:
        rss_summary = BeautifulSoup(entry.get('summary', ''), 'html.parser').get_text()
        extracted_paragraphs = [rss_summary] if rss_summary else []
        
    if img_url and not is_image_valid(img_url):
        img_url = None
        
    return img_url, " ".join(extracted_paragraphs)

# --- 6. محرك الترجمة الفورية والتهذيب السريع للنص ---
def translate_and_refine(text, max_chars=300):
    if not text.strip():
        return ""
    if bool(re.search(r'[\u0600-\u06FF]', text)): 
        return text[:max_chars]
        
    try:
        translator = GoogleTranslator(source='auto', target='ar')
        clean_input = text[:max_chars].strip()
        translated = translator.translate(clean_input)
        
        sentences = translated.split('.')
        refined_sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        
        final_text = " | ".join(refined_sentences[:3])
        return final_text[:max_chars]
    except Exception as e:
        logging.error(f"⚠️ فشل محرك الترجمة: {e}")
        return ""

# --- 7. المنظومة الأساسية لمعالجة وبث الأخبار لتيليجرام ---
async def process_news_pipeline():
    logging.info("⏳ بدء دورة الفحص المحدثة والآمنة تماماً...")
    bot = Bot(token=TELEGRAM_TOKEN)
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            
            if hasattr(feed, 'bozo') and feed.bozo:
                logging.warning(f"❌ خطأ في هيكلية الـ RSS للمصدر: {feed_url}")
            
            if not feed.entries:
                logging.warning(f"⚠️ المصدر لا يحتوي على أي مدخلات نشطة حالياً: {feed_url}")
                continue
            
            entries = feed.entries[:3]
            
            for entry in entries:
                news_url = entry.link
                original_title = entry.title
                
                if is_duplicate(original_title, news_url):
                    continue
                
                logging.info(f"📰 خبر جديد مكتشف: {original_title}")
                
                img_url, summary_text = scrape_and_extract(news_url, entry)
                category = classify_article(original_title, summary_text)
                
                translated_title = translate_and_refine(original_title, max_chars=150)
                translated_summary = translate_and_refine(summary_text, max_chars=350)
                
                if not translated_title:
                    translated_title = original_title
                
                safe_category = html.escape(category)
                safe_title = html.escape(translated_title)
                safe_summary = html.escape(translated_summary)
                safe_url = news_url.replace("'", "%27")
                
                message_template = (
                    f"{safe_category}\n"
                    f"<b>{safe_title}</b>\n\n"
                    f"🔹 {safe_summary}\n\n"
                    f"🔗 <a href='{safe_url}'>المصدر الأصلي</a>"
                )
                
                try:
                    if img_url:
                        await bot.send_photo(
                            chat_id=YOUR_CHAT_ID,
                            photo=img_url,
                            caption=message_template,
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        await bot.send_message(
                            chat_id=YOUR_CHAT_ID,
                            text=message_template,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False
                        )
                    
                    save_news(original_title, news_url)
                    logging.info(f"✅ تم نشر الخبر بنجاح: {original_title}")
                    await asyncio.sleep(4)
                    
                except Exception as tx:
                    logging.error(f"❌ خطأ في إرسال الخبر عبر تيليجرام: {tx}")
                    
        except Exception as feed_parse_error:
            logging.error(f"❌ فشل فحص أو معالجة رابط الـ RSS بالكامل للمصدر {feed_url}: {feed_parse_error}")

def trigger_async_pipeline():
    asyncio.run(process_news_pipeline())

# --- 8. تشغيل المحرك والمجدول الدوري الحامي من التداخل ---
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
    logging.info("⏰ تم تفعيل مجدول الأخبار المطور بحماية التداخل (كل 15 دقيقة).")
    
    trigger_async_pipeline()
    
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logging.info("🛑 تم إيقاف النظام.")
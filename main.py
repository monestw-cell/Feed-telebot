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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
import feedparser
from deep_translator import GoogleTranslator
from PIL import Image, ImageDraw
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# --- 1. إعداد سجل التشغيل والأخطاء الاحترافي ---
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
YOUR_CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")
PORT = int(os.environ.get("PORT", 8080))

# الحالات التشغيلية والإحصائيات (لوحة التحكم للمشرف)
IS_PAUSED = False
STATS = {
    "boot_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "published_total": 0,
    "skipped_duplicates": 0,
    "skipped_old": 0,
    "skipped_clickbait": 0,
    "scraped_total": 0  
}


# قائمة المصادر الشاملة (تقنية + ذكاء اصطناعي + سياسة + رياضة)
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
    "https://deepmind.google/blog/rss.xml",
    "https://blogs.nvidia.com/feed/",
    "https://www.aljazeera.net/aljazeerarss.xml",
    "https://arabic.rt.com/rss/",
    "https://www.bbc.com/arabic/index.xml",
    "https://www.goal.com/ar/feeds/news",
    "https://www.filgoal.com/news.xml",
    "https://www.skysports.com/football/rss"
    "https://hih2.com/feed",           "https://www.caughtoffside.com/tags/fabrizio-romano/feed/"
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# مصفاة الكلمات المحظورة والحشو المضلل (Clickbait Filter)
CLICKBAIT_BLACKLIST = [
    "sponsored", "giveaway", "deal of the day", "promo", "ad ", 
    "اشترك الآن", "اضغط هنا للفوز", "ربح المال", "إعلان ممول"
]

TECH_GLOSSARY = {
    "ذكاء التفاحة": "ذكاء آبل (Apple Intelligence)",
    "الذكاء الاصطناعي الإنجابي": "الذكاء الاصطناعي التوليدي",
    "ذكاء اصطناعي إنجابي": "ذكاء اصطناعي توليدي",
    "رقاقة": "معالج رقمي",
    "دردشة جي بي تي": "تطبيق ChatGPT",
    "أندرويد سنترال": "Android Central",
    "ماك رومرز": "MacRumors"
}

AUTO_HASHTAGS = {
    "🤖 AI": "\n\n#ذكاء_اصطناعي #ذكاء_توليدي #تقنية #تكنولوجيا",
    "🍎 Apple": "\n\n#آبل #آيفون #تكنولوجيا_آبل #iOS",
    "📱 Mobile": "\n\n#هواتف #أندرويد #سامسونج #جوالات",
    "🚀 Startups": "\n\n#شركات_ناشئة #استثمارات #ريادة_أعمال",
    "📢 سياسة": "\n\n#أخبار_السياسة #عاجل #أخبار_العالم #سياسة",
    "⚽ كرة القدم": "\n\n#كرة_القدم #انتقالات_اللاعبين #الدوري_الانجليزي #ريال_مدريد #برشلونة",
    "💻 Tech": "\n\n#أخبار_التقنية #تكنولوجيا #عالم_التقنية",
    "🚨 عاجل | Breaking": "\n\n#عاجل #أخبار_عاجلة #BreakingNews"
}

# --- 2. خادم الحفاظ على النشاط (Render Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Premium Automated Telebot Infrastructure is Live!")
    def log_message(self, format, *args):
        return

def run_health_server():
    logging.info(f"🚀 تشغيل خادم الويب الداخلي على المنفذ: {PORT}")
    server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
    server.serve_forever()

# --- 3. إدارة قاعدة البيانات المحلية السريعة ---
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

# --- 4. التصنيف التلقائي المطور + محرك انطباع الخبر (Sentiment Engine) ---
def get_sentiment_emoji(text):
    """محرك محلي لفحص انطباع كلمات العنوان وإضافة الإيموجي التفاعلي المناسب للعنوان"""
    text_lower = text.lower()
    positives = ["إطلاق", "تحديث", "أرباح", "فوز", "تتويج", "صفقة", "ثورة", "نجاح", "launch", "success", "profit", "win", "revenue", "deal"]
    negatives = ["ثغرة", "اختراق", "انخفاض", "خسائر", "حظر", "هزيمة", "إصابة", "تراجع", "vulnerability", "hack", "loss", "ban", "injury", "defeat", "drop"]
    
    if any(p in text_lower for p in positives):
        return "🚀 "
    if any(n in text_lower for n in negatives):
        return "⚠️ "
    return ""

def classify_article(title, content):
    text_to_scan = f"{title} {content}".lower()
    
    if any(x in text_to_scan for x in ["breaking", "urgent", "عاجل", "🚨"]):
        return "🚨 عاجل | Breaking"
        
    # حل صدمة جنرال ملاعب: الفئة السياسية لها حماية وأولوية قصوى قبل فحص الرياضة
    political_keywords = ["انتخابات", "رئيس", "وزير", "غارة", "معاهدة", "قمة", "تصويت", "البرلمان", "سياسة", "مفاوضات", "هدنة", "جيش", "قوات", "حزب الله", "إيران", "لبنان", "اسرائيل", "غزة", "حماس", "واشنطن", "government", "president", "minister", "election", "parliament", "military", "treaty", "politics"]
    if any(p in text_to_scan for p in political_keywords):
        return "📢 سياسة"
        
    categories = {
        "🤖 AI": ["ai", "artificial intelligence", "openai", "chatgpt", "llm", "claude", "anthropic", "deepmind", "gemini", "copilot", "machine learning", "meta ai", "xai", "mistral", "ذكاء اصطناعي", "توليدي"],
        "⚽ كرة القدم": ["انتقالات", "مباراة", "هدف", "برشلونة", "مدريد", "دوري", "لاعب", "اهداف", "كورة", "تعاقد", "football", "transfer", "match", "goal", "ucl", "chelsea", "liverpool", "bayern", "الدوري"],
        "🍎 Apple": ["apple", "iphone", "ipad", "mac", "macbook", "ios", "macrumors", "9to5mac", "apple watch", "آبل", "آيفون"],
        "📱 Mobile": ["mobile", "android", "smartphone", "gsmarena", "samsung", "galaxy", "pixel", "snapdragon", "xiaomi", "9to5google", "androidcentral", "هاتف", "جوال"],
        "🚀 Startups": ["startup", "funding", "venture", "techcrunch", "acquisition", "ipo", "raised", "شركة ناشئة"]
    }
    for category, keywords in categories.items():
        if any(keyword in text_to_scan for keyword in keywords):
            return category
    return "💻 Tech"

# --- 5. كشط آمن + الختم المائي التلقائي بجودة عالية (Auto-Watermark) ---
def clean_image_url(url):
    """حل مشكلة جودة الصور عبر تدمير لاحقات أحجام الـ Thumbnails لطلب الحجم الكامل الأصلي للمقال"""
    if not url:
        return url
    return re.sub(r'-\d+x\d+\.(jpg|jpeg|png|webp)', r'.\1', url)

def apply_watermark(image_content):
    try:
        img = Image.open(io.BytesIO(image_content)).convert("RGB")
        draw = ImageDraw.Draw(img)
        text = "@FeedTelebot"
        
        width, height = img.size
        x = width - 150
        y = height - 40
        
        # رسم الختم بظل أسود عريض لمنع ضياع النص واختفاءه
        draw.text((x + 1, y + 1), text, fill=(0, 0, 0))
        draw.text((x, y), text, fill=(255, 255, 255))
        
        output = io.BytesIO()
        # رفع الجودة إلى 95 للحفاظ على نقاء الألوان وتفادي تشويه تيليجرام
        img.save(output, format="JPEG", quality=95)
        output.seek(0)
        return output
    except Exception as e:
        logging.error(f"⚠️ فشل تطبيق العلامة المائية: {e}")
        return io.BytesIO(image_content)

def get_image_bytes(url):
    if not url:
        return None
    cleaned_img_url = clean_image_url(url)
    try:
        response = requests.get(cleaned_img_url, headers=HEADERS, timeout=5, stream=True)
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
        
    image_bytes = get_image_bytes(img_url)
    return image_bytes, " ".join(extracted_paragraphs)

# --- 6. محرك الترجمة والتهذيب اللغوي التقني ---
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

# --- 7. المنظومة الأساسية ومصفاة الوقت الحقيقي (Time-Based Engine) ---
async def check_and_broadcast_news(context: ContextTypes.DEFAULT_TYPE):
    global IS_PAUSED, STATS
    if IS_PAUSED:
        logging.info("⏸️ النشر التلقائي متوقف مؤقتاً حالياً بأمر من المشرف.")
        return

    logging.info("⏳ بدء دورة فحص الأخبار الجديدة والمطابقة للوقت...")
    bot = context.bot
    current_timestamp = time.time()
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if hasattr(feed, 'bozo') and feed.bozo:
                continue
            if not feed.entries:
                continue
                
            entries = feed.entries[:3]
            for entry in entries:
                news_url = entry.link
                original_title = entry.title
                
                # أ) مصفاة الكليكبايت والحشو الإعلاني
                if any(bad in original_title.lower() for bad in CLICKBAIT_BLACKLIST):
                    STATS["skipped_clickbait"] += 1
                    continue
                
                # ب) حل مشكلة مساحة Render المؤقتة (Time-Based Filter)
                # فحص وقت نشر الخبر؛ إذا كان أقدم من 35 دقيقة، يتم استبعاده فوراً لحماية القناة من الطوفان عند الـ Restart
                published_time = entry.get('published_parsed') or entry.get('updated_parsed')
                if published_time:
                    article_timestamp = time.mktime(published_time)
                    if (current_timestamp - article_timestamp) > 2100: # 35 دقيقة
                        STATS["skipped_old"] += 1
                        continue
                
                # ج) فحص منع التكرار في النطاق الزمني الحالي
                if is_duplicate(original_title, news_url):
                    STATS["skipped_duplicates"] += 1
                    continue
                
                logging.info(f"📰 اصطياد خبر طازج متوافق مع الشروط: {original_title}")
                STATS["scraped_total"] += 1
                
                image_bytes, summary_text = scrape_and_extract(news_url, entry)
                category = classify_article(original_title, summary_text)
                
                # جلب إيموجي الانطباع التلقائي
                sentiment_emoji = get_sentiment_emoji(original_title)
                
                translated_title = translate_and_refine(original_title, max_chars=120)
                translated_summary = translate_and_refine(summary_text, max_chars=350)
                
                if not translated_title:
                    translated_title = original_title
                    
                hashtags = AUTO_HASHTAGS.get(category, "\n\n#تقنية #أخبار")
                
                safe_category = html.escape(category)
                safe_title = html.escape(sentiment_emoji + translated_title)
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
                    STATS["published_total"] += 1
                    logging.info(f"✅ تم بث الخبر بنجاح: {original_title}")
                    
                    # د) نظام "تنقيط وتوزيع البث" الذكي (Dripping Mechanism)
                    # ترك مسافة دقيقة ونصف دقيقة بين المنشورات الفريش المتزامنة لعدم إزعاج المشترك
                    await asyncio.sleep(90)
                    
                except Exception as tx:
                    logging.error(f"❌ خطأ في إرسال الخبر عبر تيليجرام: {tx}")
                    
        except Exception as feed_parse_error:
            logging.error(f"❌ فشل معالجة رابط الـ RSS بالكامل للمصدر {feed_url}: {feed_parse_error}")

# --- 8. لوحة التحكم والتحكم عن بُعد من الموبايل (Admin Commands) ---
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(YOUR_CHAT_ID) and str(update.effective_user.id) != str(YOUR_CHAT_ID):
        return  # حماية البوت؛ لا يستجيب إلا لك شخصياً
        
    global STATS, IS_PAUSED
    status_str = "⏸️ متوقف مؤقتاً" if IS_PAUSED else "▶️ يعمل وينشر بنشاط"
    report = (
        f"📊 <b>لوحة تحكم المنظومة الإخبارية @FeedTelebot</b>\n\n"
        f"<b>• حالة البوت الحالية:</b> {status_str}\n"
        f"<b>• وقت الإقلاع:</b> {STATS['boot_time']}\n"
        f"<b>• الأخبار المنشورة بنجاح:</b> {STATS['published_total']}\n"
        f"<b>• الأخبار المفحوصة كلياً:</b> {STATS['scraped_total']}\n\n"
        f"🛡️ <b>منظومة الحماية والـ Filters:</b>\n"
        f"<b>• المكرر المستبعد:</b> {STATS['skipped_duplicates']}\n"
        f"<b>• القديم المستبعد (حماية الطوفان):</b> {STATS['skipped_old']}\n"
        f"<b>• الإعلانات والحشو المستبعد:</b> {STATS['skipped_clickbait']}\n"
    )
    await update.message.reply_text(report, parse_mode=ParseMode.HTML)

async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(YOUR_CHAT_ID):
        global IS_PAUSED
        IS_PAUSED = True
        await update.message.reply_text("⏸️ تم إيقاف النشر التلقائي في القناة مؤقتاً بنجاح.")

async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(YOUR_CHAT_ID):
        global IS_PAUSED
        IS_PAUSED = False
        await update.message.reply_text("▶️ تم إعادة تفعيل البث التلقائي، جاري الترصد للمصادر.")

# --- 9. الإقلاع والربط الهيكلي للمنظومة الحية ---
if __name__ == '__main__':
    init_db()
    
    # تشغيل خادم الويب الخاص بـ Render و UptimeRobot في الخلفية
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # بناء وتأسيس محرك التطبيق الذكي لتيليجرام
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # ربط الأوامر السرية للمشرف (من الموبايل مباشرة)
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("resume", resume_command))
    
    # استخدام المجدول المدمج والأصلي (Native Job Queue) لمنع التداخل والتعليق نهائياً
    # مبرمج ليعمل بدقة كل 15 دقيقة (900 ثانية)، ويبدأ الفحص الأول بعد 10 ثوانٍ من الإقلاع
    app.job_queue.run_repeating(check_and_broadcast_news, interval=900, first=10)

    
    logging.info("⚙️ تم ربط كافة الأنظمة الفرعية بنجاح. جاري تشغيل البوت واستقبال الأوامر...")
    
    # تشغيل البوت بشكل حي ومستمر
    app.run_polling()

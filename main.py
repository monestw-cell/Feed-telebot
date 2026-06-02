import os
import logging
import sqlite3
import feedparser
import time
import threading
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from scrapling import Fetcher
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.background import BackgroundScheduler

# إعدادات الـ Logging لمراقبة عمل البوت
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# جلب الإعدادات من بيئة التشغيل (Render Env Variables)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN_HERE")
YOUR_CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID_HERE")

# قائمة المصادر التقنية المحدثة (RSS Feeds)
RSS_FEEDS = [
    "https://www.theverge.com/rss/index.xml",
    "https://techcrunch.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.artificialintelligence-news.com/feed/",
    "https://www.androidcentral.com/feed",
    "https://www.macrumors.com/macrumors.xml",
    "https://www.gsmarena.com/rss-news-reviews.php3"
]

# --- 1. خادم ويب مصغر لاستقبال طلبات الحفاظ على النشاط (Render & UptimeRobot Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive and running fine!")

    def log_message(self, format, *args):
        return  # كتم تسجيل طلبات الـ HTTP لمنع امتلاء الـ Logs

def run_health_server():
    # Render يمرر تلقائياً رقم الـ Port في المتغير PORT
    port = int(os.environ.get("PORT", 8080))
    logging.info(f"جاري تشغيل خادم الويب الداخلي على البورت {port}...")
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# --- 2. إدارة قاعدة البيانات المحلية (SQLite) لمنع التكرار ---
DB_FILE = "news_archive.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS sent_news (url TEXT PRIMARY KEY)''')
    conn.commit()
    conn.close()

def is_news_sent(url):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM sent_news WHERE url = ?", (url,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def save_news_url(url):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO sent_news (url) VALUES (?)", (url,))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

# --- 3. محرك الكشط والاختصار الفوري والمحلي باستخدام Scrapling ---
def get_ultra_short_summary(url):
    try:
        page = Fetcher(url)
        paragraphs = page.css('p::text').all()
        
        valid_text = []
        for p in paragraphs:
            clean_p = p.strip()
            if len(clean_p) > 40:  # تخطي النصوص الإعلانية أو القصيرة جداً
                valid_text.append(clean_p)
            if len(valid_text) == 2:  # جلب أول فقرتين فقط للاختصار الشديد
                break
                
        summary = " ".join(valid_text)
        return summary[:250] + "..." if len(summary) > 250 else summary
    except Exception as e:
        logging.error(f"فشل كشط الرابط {url}: {e}")
        return ""

# --- 4. المهمة الدورية لفحص الأخبار وإرسالها ---
async def check_and_send_news():
    logging.info("⏳ بدء فحص مصادر الأخبار التقنية الآن...")
    bot = Bot(token=TELEGRAM_TOKEN)
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            # فحص آخر 3 مقالات فقط من كل موقع لضمان الحداثة دائمًا
            for entry in feed.entries[:3]:
                news_url = entry.link
                title = entry.title
                
                # منع التكرار
                if not is_news_sent(news_url):
                    # الكشط والاختصار المحلي
                    short_text = get_ultra_short_summary(news_url)
                    if not short_text:
                        short_text = entry.get('summary', '')[:200] + "..."
                    
                    # تنسيق رسالة تيليجرام
                    message = (
                        f"✨ **{title}**\n\n"
                        f"📝 {short_text}\n\n"
                        f"🔗 [اقرأ المزيد]({news_url})"
                    )
                    
                    try:
                        await bot.send_message(
                            chat_id=YOUR_CHAT_ID,
                            text=message,
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True
                        )
                        save_news_url(news_url)
                        logging.info(f"✅ تم إرسال خبر جديد بنجاح: {title}")
                        await asyncio.sleep(2)  # تأخير لتفادي الـ Anti-Flood من تيليجرام
                    except Exception as e:
                        logging.error(f"خطأ أثناء إرسال الرسالة إلى تيليجرام: {e}")
        except Exception as e:
            logging.error(f"خطأ أثناء قراءة الـ RSS للمصدر {feed_url}: {e}")

def run_news_job():
    # تشغيل الدالة الأسنكرونية داخل بيئة المجدول السينكروني
    asyncio.run(check_and_send_news())

if __name__ == '__main__':
    # تهيئة قاعدة البيانات المحلية
    init_db()
    
    # 1. تشغيل خادم الويب الداخلي في خلفية النظام (لـ Render و UptimeRobot)
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # 2. تشغيل المجدول الدوري لفحص الأخبار كل 30 دقيقة
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_news_job, 'interval', minutes=30)
    scheduler.start()
    logging.info("⏰ تم تفعيل مجدول الأخبار الدوري بنجاح (كل 30 دقيقة).")
    
    # تشغيل الفحص فوراً لمرة واحدة عند بداية إقلاع التطبيق
    run_news_job()
    
    # 3. إبقاء الـ Main Thread حي ومستمر
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logging.info("تم إيقاف البوت بنجاح.")

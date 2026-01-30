import os
import time
import logging
import random
import textwrap
import re
import requests
from urllib.parse import urlparse
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance
import feedparser
from bs4 import BeautifulSoup
from instagrapi import Client
from googlenewsdecoder import new_decoderv1

# ==================== CONFIGURATION ====================
class Config:
    # Instagram Credentials (Ambil dari Environment Variable agar Aman)
    # Jika tidak ada di Env (Local), pakai default string (HATI HATI)
    IG_USERNAME = os.getenv("IG_USERNAME", "kabaracehtenggara")
    IG_PASSWORD = os.getenv("IG_PASSWORD", "rezaeza007")

    # Target & Filter
    RSS_URL = "https://news.google.com/rss/search?q=Aceh+Tenggara+when:1d&hl=id&gl=ID&ceid=ID:id"
    KEYWORDS = [
        # Umum
        "aceh tenggara", "kutacane", "tanah alas", "leuser", "agara",
        "banjir", "longsor", "pembunuhan", "narkoba", "korupsi",
        "pilkada", "jalan rusak", "dprk", "pemkab",
        # Wilayah / Kacamata (Coverage Luas)
        "lawe sigala", "bambel", "bukit tusam", "semadam", "babul makmur",
        "babul rahmat", "tanoh alas", "lawe alas", "ketambe", "darul hasanah",
        "badar", "lawe bulan", "deleng pokhkisen", "lawe sumur"
    ]

    # Files
    POSTED_FILE = "posted.txt"
    TEMP_IMAGE_PATH = "temp_post.jpg"
    FONT_PATH = "arialbd.ttf" # Windows default

    # Settings
    WATERMARK_TEXT = "Kabar Aceh Tenggara"
    MAX_CAPTION_CHARS = 1600 # Sedikit dinaikkan
    BATCH_SIZE = 3
    BATCH_SLEEP_RANGE = (40, 60) # Interval aman

# ==================== LOGGING SETUP ====================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_log.txt", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

    # ==================== MODULE: HISTORY MANAGER ====================
class HistoryManager:
    def __init__(self, filepath):
        self.filepath = filepath
        self.urls_titles = set()
        self.image_hashes = [] # List khusus untuk simpan hash agar bisa dicek kemiripannya
        self._load()

    def _load(self):
        if not os.path.exists(self.filepath):
            open(self.filepath, 'w').close()

        with open(self.filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line: continue

                # Pisahkan mana Hash, mana URL/Judul
                if line.startswith("HASH:"):
                    self.image_hashes.append(line.replace("HASH:", ""))
                else:
                    self.urls_titles.add(line)

    def exists(self, item):
        return item in self.urls_titles

    def is_duplicate_image(self, new_hash_hex, threshold=5):
        """
        Cek apakah gambar mirip dengan yang ada di database.
        Threshold 5 artinya: Boleh beda maksimal 5 bit (Kemiripan ~92%).
        """
        if not new_hash_hex: return False

        new_hash_clean = new_hash_hex.replace("HASH:", "")

        # Konversi hex ke integer untuk perbandingan bit
        try:
            new_int = int(new_hash_clean, 16)
        except:
            return False

        for stored_hex in self.image_hashes:
            try:
                stored_int = int(stored_hex, 16)
                # Hitung Hamming Distance (Jumlah bit yang beda)
                # XOR lalu hitung jumlah angka 1
                distance = bin(new_int ^ stored_int).count('1')

                if distance <= threshold:
                    return True # Mirip!
            except:
                continue

        return False

    def add(self, *items):
        with open(self.filepath, 'a', encoding='utf-8') as f:
            for item in items:
                if item:
                    f.write(f"{item}\n")
                    if item.startswith("HASH:"):
                        self.image_hashes.append(item.replace("HASH:", ""))
                    else:
                        self.urls_titles.add(item)

# ==================== MODULE: IMAGE PROCESSOR ====================
class ImageProcessor:
    @staticmethod
    def load_font(size):
        try:
            return ImageFont.truetype(Config.FONT_PATH, size)
        except:
            return ImageFont.load_default()

    @staticmethod
    def get_image_hash(image_path):
        """
        Membuat 'Sidik Jari' dHash (Difference Hash) 64-bit.
        """
        try:
            with Image.open(image_path) as img:
                img = img.resize((9, 8), Image.Resampling.LANCZOS).convert("L")
                # Fix Deprecation Warning: Use pixel access object
                pixels = img.load()

                diff = []
                for row in range(8):
                    for col in range(8):
                        # Access pixel directly [x, y]
                        left = pixels[col, row]
                        right = pixels[col + 1, row]
                        diff.append("1" if left > right else "0")

                binary_str = "".join(diff)
                return f"HASH:{hex(int(binary_str, 2))[2:]}"
        except Exception:
            return None

    @staticmethod
    def create_news_card(image_path, title_text, badge_text):
        """Layout News Card Pro 70:30 dengan Auto-fit Font."""
        try:
            CANVAS_W, CANVAS_H = 1080, 1350
            SPLIT_Y = int(CANVAS_H * 0.70)

            final_img = Image.new("RGB", (CANVAS_W, CANVAS_H), "white")
            draw = ImageDraw.Draw(final_img)

            # 1. Image Processing
            with Image.open(image_path) as img_source:
                img_rgb = img_source.convert("RGB")

                # --- AUTO-CROP: Buang Banner Bawah ---
                # Crop 12% bawah (Banner) dan 2% atas (Garis header tipis)
                w_src, h_src = img_rgb.size
                img_rgb = img_rgb.crop((0, int(h_src*0.02), w_src, int(h_src * 0.88)))

                # --- QUALITY BOOST: Sharpness & Contrast ---
                # 1. Pertajam (Details)
                enhancer_sharp = ImageEnhance.Sharpness(img_rgb)
                img_rgb = enhancer_sharp.enhance(1.4) # Naik 40%

                # 2. Kontras (Warna Pop)
                enhancer_cont = ImageEnhance.Contrast(img_rgb)
                img_rgb = enhancer_cont.enhance(1.1) # Naik 10% agar tidak pudar

                img_filled = ImageOps.fit(
                    img_rgb, (CANVAS_W, SPLIT_Y),
                    method=Image.Resampling.LANCZOS, centering=(0.5, 0.0)
                )
                final_img.paste(img_filled, (0, 0))
                draw.line([(0, SPLIT_Y), (CANVAS_W, SPLIT_Y)], fill=(200, 200, 200), width=2)

            # 2. Text Processing (Auto Fit & Centering)
            if title_text:
                title_size = 65
                margin_x = 80
                max_width = CANVAS_W - (margin_x * 2)
                max_lines = 4
                line_spacing = 20

                font_title = ImageProcessor.load_font(title_size)
                wrapped_text = ""

                # Font fitting loop
                while title_size > 35:
                    avg_char_w = title_size * 0.5
                    est_chars = int(max_width / avg_char_w)
                    wrapped_text = textwrap.fill(title_text, width=est_chars)
                    # Use simpler bbox check logic for robustness
                    bbox = draw.multiline_textbbox((0,0), wrapped_text, font=font_title, spacing=line_spacing)
                    w = bbox[2] - bbox[0]
                    h = bbox[3] - bbox[1]

                    if w <= max_width and len(wrapped_text.split('\n')) <= max_lines:
                        break
                    title_size -= 2
                    font_title = ImageProcessor.load_font(title_size)

                # Vertical Center Logic
                bbox = draw.multiline_textbbox((0,0), wrapped_text, font=font_title, spacing=line_spacing)
                text_h = bbox[3] - bbox[1]
                area_h = CANVAS_H - SPLIT_Y
                text_y = SPLIT_Y + (area_h - text_h) // 2 - 10

                draw.multiline_text((margin_x, text_y), wrapped_text, font=font_title,
                                    fill=(21, 21, 21), spacing=line_spacing, align="left")

            # 3. Watermark Badge
            wm_text = badge_text.replace("@", "")
            font_wm = ImageProcessor.load_font(36)
            bbox_wm = draw.textbbox((0,0), wm_text, font=font_wm)
            wm_w, wm_h = bbox_wm[2] - bbox_wm[0], bbox_wm[3] - bbox_wm[1]

            px, py = 50, 26
            badge_w = wm_w + px
            badge_h = wm_h + py
            bx = (CANVAS_W - badge_w) // 2
            by = 60

            draw.rounded_rectangle((bx+4, by+4, bx+badge_w+4, by+badge_h+4), radius=badge_h/2, fill=(0,0,0,60))
            draw.rounded_rectangle((bx, by, bx+badge_w, by+badge_h), radius=badge_h/2, fill=(255,255,255,245), outline=(220,220,220), width=1)

            tx = bx + (badge_w - wm_w) // 2
            ty = by + (badge_h - wm_h) / 2 - 4
            draw.text((tx, ty), wm_text, font=font_wm, fill=(30,30,30))

            final_img.save(image_path, "JPEG", quality=95, subsampling=0)
            return True
        except Exception as e:
            logger.error(f"Layout Error: {e}")
            return False

# ==================== MODULE: CONTENT SCRAPER ====================
class ContentScraper:
    @staticmethod
    def get_real_url(rss_link):
        try:
            decoded = new_decoderv1(rss_link)
            return decoded.get("decoded_url", rss_link)
        except:
            return rss_link

    @staticmethod
    def extract_image_hd(url):
        try:
            r = session.get(url, timeout=10)
            soup = BeautifulSoup(r.content, 'html.parser')

            # Strategy: Meta tag -> Link rel -> First img
            img_url = None
            if meta := soup.find("meta", property="og:image"): img_url = meta["content"]
            elif link := soup.find("link", rel="image_src"): img_url = link["href"]

            if not img_url: return None, ""

            # Helper: Get Title
            page_title = soup.title.string if soup.title else ""
            return img_url, page_title
        except:
            return None, ""

    @staticmethod
    def scrape_summary(url):
        try:
            r = session.get(url, timeout=10)
            soup = BeautifulSoup(r.content, 'html.parser')
            for junk in soup(["script", "style", "nav", "footer", "header"]): junk.extract()

            content = []
            # 1. Regex Area Lokasi/Reporter (KUTACANE - )
            regex_loc = re.compile(r"^([A-Z\s,]+)\s?[-–]\s?")
            # 2. Regex Breadcrumb (Home / News / Aceh)
            regex_crumb = re.compile(r"(\w+\s*[/|>]\s*){2,}")

            junk_keywords = [
                "baca juga", "google news", "home /", "beranda >",
                "headline", "info tni", "nasional", "wib", "januari", "februari",
                "editor:", "penulis:", "sumber:", "foto:", "copyright"
            ]

            for p in soup.find_all('p'):
                text = p.get_text().strip()

                # Filter Dasar
                if len(text) < 60: continue
                if any(x in text.lower() for x in junk_keywords): continue

                # Filter Advance: Breadcrumb Pattern
                if regex_crumb.search(text): continue

                # Cleaning
                text = regex_loc.sub("", text)

                # Hapus info dalam kurung di akhir kalimat (cth: (Tribun/Ali))
                if text.endswith(")"):
                    last_open = text.rfind("(")
                    if last_open > 10: text = text[:last_open].strip()

                if text and text[0].isupper(): # Harus kalimat (Huruf Besar Awal)
                    if text not in content: content.append(text)

            summary = "\n\n".join(content)
            if not summary:
                if meta := soup.find("meta", attrs={"name": "description"}): summary = meta.get("content")

            # Safety Cut (Smart Truncate at Sentence)
            if summary and len(summary) > Config.MAX_CAPTION_CHARS:
                # Potong dulu sesuai max chars
                cut_text = summary[:Config.MAX_CAPTION_CHARS]
                # Cari titik terakhir agar kalimat utuh
                last_dot = cut_text.rfind(".")
                if last_dot > 100: # Pastikan tidak memotong terlalu pendek
                    summary = cut_text[:last_dot+1]
                else:
                    summary = cut_text + "..."

            return summary
        except:
            return ""

    @staticmethod
    def download_image_raw(url, target_path):
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                with open(target_path, 'wb') as f: f.write(r.content)
                return True
        except: pass
        return False

# ==================== MAIN BOT LOGIC ====================
class AgaraBot:
    def __init__(self):
        self.history = HistoryManager(Config.POSTED_FILE)
        self.client = Client()
        self.client.delay_range = [1, 3]

    def login(self):
        # 1. Coba Load Session (Agar aman dari IP Blacklist)
        session_file = "session.json"

        if os.path.exists(session_file):
            try:
                logger.info("📂 Loading session dari file...")
                self.client.load_settings(session_file)
                # Coba login ulang untuk refresh cookie, tapi pakai session yang ada
                self.client.login(Config.IG_USERNAME, Config.IG_PASSWORD)
                logger.info("✅ Login via Session Berhasil!")
                return True
            except Exception as e:
                logger.warning(f"⚠️ Gagal load session: {e}. Mencoba login manual...")

        # 2. Login Normal (Jika session gagal)
        for attempt in range(3):
            try:
                logger.info(f"🔄 Login Manual Instagram (Percobaan {attempt+1})...")
                self.client.login(Config.IG_USERNAME, Config.IG_PASSWORD)
                logger.info("✅ Login Berhasil!")
                return True
            except Exception as e:
                logger.error(f"❌ Login Gagal: {e}")
                time.sleep(30)
        return False

    def run(self):
        logger.info("🚀 Bot Started (V6 Professional Refactor)")

        if not self.login(): return

        logger.info("📡 Scanning Google News RSS...")
        feed = feedparser.parse(Config.RSS_URL)

        # Filter: Today Only
        today = datetime.now().date()
        valid_entries = []
        for e in feed.entries:
            if not e.published_parsed: continue
            if datetime(*e.published_parsed[:6]).date() == today:
                valid_entries.append(e)

        logger.info(f"📅 Berita Hari Ini: {len(valid_entries)}")
        success_count = 0
        found_new = False

        for i, entry in enumerate(valid_entries):
            title = entry.title.strip()
            rss_link = entry.link

            # --- 1. Filter Keyword ---
            full_text = f"{title} {entry.description if 'description' in entry else ''}"
            if not any(k.lower() in full_text.lower() for k in Config.KEYWORDS):
                continue

            # --- 2. Filter History (Fast) ---
            if self.history.exists(rss_link) or self.history.exists(title):
                continue

            logger.info(f"\n⚡ [{i+1}/{len(valid_entries)}] Processing: {title}")

            # --- 3. Resolve URL & Filter History (Deep) ---
            real_url = ContentScraper.get_real_url(rss_link)
            if self.history.exists(real_url):
                logger.info("⏩ Skip: URL Asli sudah pernah diposting.")
                self.history.add(rss_link) # Save RSS too
                continue

            # --- 4. Image Processing & Hash Filter ---
            img_url, _ = ContentScraper.extract_image_hd(real_url)
            if not img_url:
                logger.warning("⚠️ No valid image found.")
                continue

            if not ContentScraper.download_image_raw(img_url, Config.TEMP_IMAGE_PATH):
                continue

            # Check Visual Hash
            img_hash = ImageProcessor.get_image_hash(Config.TEMP_IMAGE_PATH)

            # AGGRESSIVE FILTER: Threshold naik ke 12
            # Artinya: Gambar beda hingga 20% pun tetap dianggap SAMA.
            if img_hash and self.history.is_duplicate_image(img_hash, threshold=12):
                logger.info("⛔ Visual Duplicate Detected (Sangat Mirip). Skip.")
                continue

            # Apply Layout
            final_title = title.split(" - ")[0]
            if not ImageProcessor.create_news_card(Config.TEMP_IMAGE_PATH, final_title, Config.WATERMARK_TEXT):
                continue

            # --- 5. Caption & Posting ---
            summary = ContentScraper.scrape_summary(real_url) or final_title
            domain = urlparse(real_url).netloc.replace("www.", "")

            caption = (
                f"{final_title}\n\n"
                f"{summary}\n\n"
                f"📍 #AcehTenggara #Kutacane\n"
                f"📰 {domain}\n"
                f"🔗 Link: {real_url}\n\n"
                f"#InfoKutacane #BeritaAceh #AgaraNews #KabarAcehTenggara #SeputarAceh"
            )

            try:
                logger.info("📤 Uploading to Instagram...")
                media = self.client.photo_upload(Config.TEMP_IMAGE_PATH, caption)
                logger.info(f"✅ POST SUKSES! PK: {media.pk}")

                # Save All Traces
                self.history.add(rss_link, real_url, title, img_hash)
                found_new = True
                success_count += 1

                # Cleanup
                if os.path.exists(Config.TEMP_IMAGE_PATH): os.remove(Config.TEMP_IMAGE_PATH)

                # Smart Batching
                if success_count % Config.BATCH_SIZE == 0:
                    rt = random.randint(*Config.BATCH_SLEEP_RANGE)
                    logger.info(f"☕ Batch Break: Istirahat {rt} detik...")
                    time.sleep(rt)

            except Exception as e:
                logger.error(f"❌ Upload Error: {e}")
                time.sleep(5)

        msg = "✅ Semua tugas selesai." if found_new else "✅ Tidak ada berita baru."
        logger.info(msg)

if __name__ == "__main__":
    bot = AgaraBot()
    bot.run()

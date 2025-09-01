# handlers/twitter.py

import asyncio
import html
import os
import re
import shutil
import uuid
import pathlib
from urllib.parse import urlsplit

import aiohttp
from aiohttp import ClientTimeout

from aiogram import types, Router, F
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.exceptions import TelegramRetryAfter

from pyrogram import Client as PyroClient  # ✅ Pyrogram

import messages as bm
from config import OUTPUT_DIR, CHANNEL_IDtwiter
from main import bot, db, send_analytics

# =========================
# إعدادات عامّة
# =========================
PYROGRAM_API_ID = int(os.environ.get('ID') or "0")
PYROGRAM_API_HASH = os.environ.get('HASH') or ""
PYROGRAM_SESSION_STRING = os.environ.get('PYRO_SESSION_STRING') or ""  # يجب أن تكون string session

MAX_FILE_SIZE = 50 * 1024 * 1024  # حد تليجرام للملف داخل البوت
HTTP_TIMEOUT = ClientTimeout(total=45)  # مهلة طلبات HTTP
MAX_CONCURRENT_DOWNLOADS = 4  # أقصى تنزيلات متوازية لكل محادثة

# أدوات yt-dlp/ffmpeg (اختياري للكوكيز للتغريدات المحمية)
YTDLP_BIN = shutil.which("yt-dlp") or "yt-dlp"
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
X_COOKIES = os.environ.get("X_COOKIES")  # اختياري: مسار cookies.txt إذا احتجت

router = Router()
album_accumulator: dict[int, dict[str, list]] = {}
chat_queues: dict[int, asyncio.Queue] = {}
chat_workers: dict[int, asyncio.Task] = {}
chat_semaphores: dict[int, asyncio.Semaphore] = {}


# =========================
# جلسات HTTP (aiohttp)
# =========================
def _get_session() -> aiohttp.ClientSession:
    """إنشاء جلسة aiohttp (تُستخدم مع: async with _get_session() as session)."""
    return aiohttp.ClientSession(timeout=HTTP_TIMEOUT, raise_for_status=True)


# =========================
# فك اختصار t.co واستخراج Tweet IDs
# =========================
async def _unshorten_link(session: aiohttp.ClientSession, short_url: str) -> str | None:
    """فك اختصار t.co"""
    try:
        async with session.get('https://' + short_url, allow_redirects=True) as resp:
            return str(resp.url)
    except Exception:
        return None


async def extract_tweet_ids(text: str) -> list[str] | None:
    """استخراج Tweet IDs من نص يحتوي روابط تويتر/إكس أو t.co"""
    text = text or ""
    unshortened_links = ''
    tco_links = re.findall(r't\.co/[a-zA-Z0-9]+', text)
    if tco_links:
        async with _get_session() as session:
            tasks = [asyncio.create_task(_unshorten_link(session, link)) for link in tco_links]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for url in results:
                if isinstance(url, str) and url:
                    unshortened_links += '\n' + url

    tweet_ids = re.findall(
        r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})",
        text + unshortened_links
    )
    return list(dict.fromkeys(tweet_ids)) if tweet_ids else None


# =========================
# استدعاء vxtwitter (للصور/الفيديو كـ fallback)
# =========================
async def scrape_media(tweet_id: str) -> dict:
    """جلب بيانات الوسائط عبر vxtwitter API"""
    url = f'https://api.vxtwitter.com/Twitter/status/{tweet_id}'
    async with _get_session() as session:
        try:
            async with session.get(url) as resp:
                # حاول JSON أولًا
                try:
                    return await resp.json()
                except aiohttp.ContentTypeError:
                    # لو الرد مش JSON، استخرج الخطأ من og:description إن وُجد
                    text = await resp.text()
                    if match := re.search(
                        r'<meta content="(.*?)" property="og:description"\s*/?>', text
                    ):
                        raise Exception(f'API returned error: {html.unescape(match.group(1))}')
                    raise
        except Exception:
            raise


async def download_media(session: aiohttp.ClientSession, media_url: str, file_path: str):
    """تنزيل غير متزامن لملف وسائط (صور/فيديو)"""
    async with session.get(media_url) as response:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'wb') as f:
            async for chunk in response.content.iter_chunked(8192):
                if chunk:
                    f.write(chunk)


# =========================
# yt-dlp: تنزيل فيديوهات X (أولوية أولى)
# =========================
def _ensure_tools_available():
    if not shutil.which(YTDLP_BIN):
        raise RuntimeError("yt-dlp غير مثبت أو غير موجود بالمسار.")
    if not shutil.which(FFMPEG_BIN):
        raise RuntimeError("ffmpeg غير مثبت أو غير موجود بالمسار.")


def _tweet_url_from_id(tweet_id: str) -> str:
    # رابط متوافق مع yt-dlp
    return f"https://x.com/i/status/{tweet_id}"


async def ytdlp_download_tweet_video(tweet_id: str, out_dir: str) -> str | None:
    """
    يحاول تنزيل فيديو تغريدة عبر yt-dlp ويخرج MP4 جاهز للتلغرام.
    يعيد مسار الملف على النجاح، أو None إن لم يجد فيديو/فشل.
    """
    _ensure_tools_available()
    os.makedirs(out_dir, exist_ok=True)

    base = f"x_{tweet_id}_{uuid.uuid4().hex}"
    out_tpl = str(pathlib.Path(out_dir) / f"{base}.%(ext)s")
    url = _tweet_url_from_id(tweet_id)

    # تنسيق: جرّب أفضل فيديو+صوت، وإجبار دمج mp4
    fmt = "bv*+ba/best"

    cmd = [
        YTDLP_BIN,
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--ffmpeg-location", FFMPEG_BIN,
        "-o", out_tpl,
        "--no-playlist",
        "--no-warnings",
        "--restrict-filenames",
        "--geo-bypass",
        "--retries", "3",
        "--fragment-retries", "3",
        "--concurrent-fragments", "8",
        url,
    ]

    # دعم كوكيز لو متاحة (للتغريدات المحمية/المقيدة)
    if X_COOKIES and os.path.isfile(X_COOKIES):
        cmd.extend(["--cookies", X_COOKIES])

    # نفّذ yt-dlp كعملية فرعية async
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        # فشل (قد لا تكون التغريدة فيديو/أو محمية بدون كوكيز)
        print(f"[yt-dlp] failed for {tweet_id}: {stderr.decode(errors='ignore')[:400]}")
        return None

    # ابحث عن المخرجات .mp4 في out_dir
    for p in pathlib.Path(out_dir).glob(f"{base}.*"):
        if p.suffix.lower() == ".mp4":
            return str(p)

    # اختيار أكبر ملف إن لم نجد mp4 صريح
    candidates = list(pathlib.Path(out_dir).glob(f"{base}.*"))
    if candidates:
        best = max(candidates, key=lambda x: x.stat().st_size)
        return str(best)

    return None


# =========================
# إرسال عبر Pyrogram عند تجاوز الحجم
# =========================
async def send_large_file_pyro(chat_id: int | str, file_path: str, caption: str | None = None):
    async with PyroClient(
        PYROGRAM_SESSION_STRING,
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
        in_memory=True
    ) as client:
        await client.send_document(chat_id=chat_id, document=file_path, caption=caption or "")


# =========================
# الرد بالوسائط (صور/فيديو) — منطقك الأصلي مع تحسينات Async
# =========================
async def reply_media(
    message: types.Message,
    tweet_id: str,
    tweet_media: dict,
    bot_url: str,
    business_id
):
    """تنزيل الوسائط من التغريدة وإرسالها كألبوم صور/فيديو"""
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")

    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media.get("text", "")
    user_captions = await db.get_user_captions(message.from_user.id)

    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)

    key = message.chat.id
    if key not in album_accumulator:
        album_accumulator[key] = {"image": [], "video": []}

    async with _get_session() as session:
        try:
            media_list = tweet_media.get('media_extended', []) or []

            # حد التوازي داخل الشات
            sem = chat_semaphores.setdefault(key, asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS))

            async def _fetch_one(media: dict):
                async with sem:
                    media_url = media['url']
                    media_type = media['type']
                    file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
                    await download_media(session, media_url, file_name)
                    if media_type == 'image':
                        album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
                    elif media_type in ['video', 'gif']:
                        album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))

            # نزّل كل وسائط التغريدة
            await asyncio.gather(*[asyncio.create_task(_fetch_one(m)) for m in media_list])

            # ✅ الصور (أرسل 5 صور كألبوم)
            if len(album_accumulator[key]["image"]) >= 5:
                album_to_send = album_accumulator[key]["image"][:5]
                media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
                for file_path, _, _ in album_to_send:
                    media_group.add_photo(media=FSInputFile(file_path))

                while True:
                    try:
                        await message.answer_media_group(media_group.build())
                        break
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after)

                # تنظيف الملفات المرسلة
                album_accumulator[key]["image"] = album_accumulator[key]["image"][5:]
                for file_path, _, dir_path in album_to_send:
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                    if os.path.exists(dir_path) and not os.listdir(dir_path):
                        os.rmdir(dir_path)
                await asyncio.sleep(0)

            # ✅ الفيديوهات (نفس منطقك: لو > 50MB أرسل للقناة)
            if len(album_accumulator[key]["video"]) >= 1:
                to_send = album_accumulator[key]["video"]
                album_accumulator[key]["video"] = []
                for file_path, _, dir_path in to_send:
                    if os.path.getsize(file_path) > MAX_FILE_SIZE:
                        try:
                            await send_large_file_pyro(CHANNEL_IDtwiter, file_path, caption="📤 تم رفع فيديو كبير ✅")
                            await message.answer(f"✅ تم إرسال فيديو كبير باستخدام Pyrogram: `{os.path.basename(file_path)}`")
                        except Exception as e:
                            print(f"[Pyrogram Error] {e}")
                            await message.answer("❌ حصل خطأ أثناء إرسال الملف الكبير.")
                    else:
                        try:
                            await message.answer_video(FSInputFile(file_path))
                        except Exception as e:
                            print(f"Error sending video: {e}")
                            await message.answer("❌ حصل خطأ أثناء إرسال الفيديو.")

                    # تنظيف
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                    if os.path.exists(dir_path) and not os.listdir(dir_path):
                        os.rmdir(dir_path)
                await asyncio.sleep(0)

        except Exception as e:
            print(e)
            if business_id is None:
                try:
                    await message.react([types.ReactionTypeEmoji(emoji="👎")])
                except Exception:
                    pass
            await message.reply("حدث خطأ أثناء معالجة الوسائط ☹️")


# =========================
# طابور التسلسل لكل محادثة
# =========================
async def process_chat_queue(chat_id: int):
    """طابور لمعالجة رسائل كل محادثة بالتسلسل (يتجنب التداخل والريت ليمت)"""
    while True:
        message: types.Message = await chat_queues[chat_id].get()
        try:
            await asyncio.sleep(0)  # إتاحة للّوب

            business_id = getattr(message, "business_connection_id", None)
            if business_id is None:
                try:
                    await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])
                except Exception:
                    pass

            bot_url = f"t.me/{(await bot.get_me()).username}"
            tweet_ids = await extract_tweet_ids(message.text or "")

            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")

                # نعالج التغريدات واحدة تلو الأخرى (أأمن على الريت ليمت)
                for tweet_id in tweet_ids:
                    # 1) جرّب yt-dlp أولًا للفيديوهات (أولوية أولى)
                    downloaded_via_ytdlp = None
                    try:
                        downloaded_via_ytdlp = await ytdlp_download_tweet_video(tweet_id, f"{OUTPUT_DIR}/{tweet_id}")
                    except Exception as e:
                        print(f"yt-dlp exception: {e}")

                    if downloaded_via_ytdlp:
                        # أرسل الفيديو مباشرة بنفس منطق الحجم (القناة عند تجاوز 50MB)
                        try:
                            if os.path.getsize(downloaded_via_ytdlp) > MAX_FILE_SIZE:
                                await send_large_file_pyro(CHANNEL_IDtwiter, downloaded_via_ytdlp, caption="📤 تم تنزيل فيديو X عبر yt-dlp")
                                await message.answer(f"✅ تم إرسال فيديو كبير عبر Pyrogram: `{os.path.basename(downloaded_via_ytdlp)}`")
                            else:
                                await message.answer_video(FSInputFile(downloaded_via_ytdlp), caption="✅ تم تنزيل فيديو X عبر yt-dlp")
                        except Exception as send_err:
                            print(f"Send video error: {send_err}")
                            await message.answer("❌ خطأ أثناء إرسال الفيديو الذي تم تنزيله.")
                        finally:
                            try:
                                os.remove(downloaded_via_ytdlp)
                            except Exception:
                                pass

                        # انتقل للتغريدة التالية (لا نحتاج scrape_media هنا)
                        await asyncio.sleep(0)
                        continue

                    # 2) لو فشل yt-dlp أو التغريدة ليست فيديو، نرجع لطريقتك الحالية (vxtwitter)
                    try:
                        media = await scrape_media(tweet_id)
                    except Exception as sm_err:
                        print(f"scrape_media error: {sm_err}")
                        # لو حتى vxtwitter فشل، نكمل للتالية
                        await message.answer("❌ لم أتمكن من جلب الوسائط لهذه التغريدة.")
                        await asyncio.sleep(0)
                        continue

                    await reply_media(message, tweet_id, media, bot_url, business_id)
                    await asyncio.sleep(0)

                # محاولة حذف رسالة الروابط بعد المعالجة
                await asyncio.sleep(0.05)
                try:
                    await message.delete()
                except Exception as delete_error:
                    print(f"Error deleting message: {delete_error}")
            else:
                if business_id is None:
                    try:
                        await message.react([types.ReactionTypeEmoji(emoji="👎")])
                    except Exception:
                        pass
                await message.answer("لم يتم العثور على تغريدات.")
        finally:
            chat_queues[chat_id].task_done()


# =========================
# Handlers للرسائل التي تحتوي روابط X/Twitter
# =========================
@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message: types.Message):
    """معالجة الرسائل التي تحتوي على روابط تويتر/إكس"""
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
        chat_semaphores[chat_id] = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    await chat_queues[chat_id].put(message)

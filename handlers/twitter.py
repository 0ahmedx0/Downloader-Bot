import asyncio
import html
import os
import re
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

# ✅ إعدادات Pyrogram من المتغيرات البيئية (string session)
PYROGRAM_API_ID = int(os.environ.get('ID'))
PYROGRAM_API_HASH = os.environ.get('HASH'))
PYROGRAM_SESSION_STRING = os.environ.get('PYRO_SESSION_STRING')  # يجب أن تكون string session

MAX_FILE_SIZE = 50 * 1024 * 1024  # حد تليجرام للملف داخل البوت
HTTP_TIMEOUT = ClientTimeout(total=45)  # مهلة طلبات HTTP
MAX_CONCURRENT_DOWNLOADS = 4  # أقصى تنزيلات متوازية لكل محادثة

router = Router()
album_accumulator = {}
chat_queues = {}
chat_workers = {}
chat_semaphores = {}  # لكل محادثة semaphore للحد من التوازي


async def _get_session():
    # جلسة aiohttp مشتركة داخل المهمة الحالية
    return aiohttp.ClientSession(timeout=HTTP_TIMEOUT, raise_for_status=True)


async def _unshorten_link(session: aiohttp.ClientSession, short_url: str) -> str | None:
    # نفك اختصار t.co عبر طلب GET يسمح بالتحويلات
    try:
        async with session.get('https://' + short_url, allow_redirects=True) as resp:
            return str(resp.url)
    except Exception:
        return None


async def extract_tweet_ids(text: str) -> list[str] | None:
    # نفك روابط t.co الموجودة قبل استخراج الـ IDs لزيادة الدقة
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


async def scrape_media(tweet_id: str) -> dict:
    # استدعاء vxtwitter API بشكل غير متزامن
    url = f'https://api.vxtwitter.com/Twitter/status/{tweet_id}'
    async with _get_session() as session:
        try:
            async with session.get(url) as resp:
                # حاول JSON أولًا
                try:
                    return await resp.json()
                except aiohttp.ContentTypeError:
                    # لو ما قدر يفك JSON، جرّب قراءة النص واستنباط الخطأ من og:description
                    text = await resp.text()
                    if match := re.search(
                        r'<meta content="(.*?)" property="og:description"\s*/?>', text
                    ):
                        raise Exception(f'API returned error: {html.unescape(match.group(1))}')
                    raise
        except Exception:
            raise


async def download_media(session: aiohttp.ClientSession, media_url: str, file_path: str):
    # تنزيل غير متزامن مع chunks
    async with session.get(media_url) as response:
        # تأكد من وجود المجلد
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        # اكتب الملف على أقسام
        with open(file_path, 'wb') as f:
            async for chunk in response.content.iter_chunked(8192):
                if chunk:
                    f.write(chunk)


# ✅ إرسال الملفات الكبيرة باستخدام string session مع Pyrogram
async def send_large_file_pyro(chat_id, file_path, caption=None):
    async with PyroClient(
        PYROGRAM_SESSION_STRING,
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
        in_memory=True
    ) as client:
        await client.send_document(chat_id=chat_id, document=file_path, caption=caption or "")


async def reply_media(message: types.Message, tweet_id: str, tweet_media: dict, bot_url: str, business_id):
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")
    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media.get("text", "")
    user_captions = await db.get_user_captions(message.from_user.id)

    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)

    key = message.chat.id
    if key not in album_accumulator:
        album_accumulator[key] = {"image": [], "video": []}

    # استخدم نفس الجلسة لتنزيل جميع الوسائط لهذا التغريد
    async with _get_session() as session:
        try:
            media_list = tweet_media.get('media_extended', [])
            # حد التوازي داخل الرسالة لتفادي ضغط الشبكة/اللوب
            sem = chat_semaphores.setdefault(key, asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS))

            async def _fetch_one(media):
                async with sem:
                    media_url = media['url']
                    media_type = media['type']
                    file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
                    await download_media(session, media_url, file_name)
                    if media_type == 'image':
                        album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
                    elif media_type in ['video', 'gif']:
                        album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))

            # نزّل الكل (مع الحد الأقصى للتوازي)
            await asyncio.gather(*[asyncio.create_task(_fetch_one(m)) for m in media_list])

            # ✅ الصور (كل 5 صور ألبوم)
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

                # نظّف الملفات المرسلة
                album_accumulator[key]["image"] = album_accumulator[key]["image"][5:]
                for file_path, _, dir_path in album_to_send:
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                    if os.path.exists(dir_path) and not os.listdir(dir_path):
                        os.rmdir(dir_path)
                await asyncio.sleep(0)  # إتاحة للدورة

            # ✅ الفيديوهات (أرسل كل فيديو على حدة)
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
                react = types.ReactionTypeEmoji(emoji="👎")
                await message.react([react])
            await message.reply("حدث خطأ أثناء معالجة الوسائط ☹️")


async def process_chat_queue(chat_id: int):
    while True:
        message: types.Message = await chat_queues[chat_id].get()
        try:
            # صغيرة علشان ما نكتم اللوب
            await asyncio.sleep(0)

            business_id = getattr(message, "business_connection_id", None)
            if business_id is None:
                try:
                    await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])
                except Exception:
                    pass

            bot_url = f"t.me/{(await bot.get_me()).username}"

            # ⚠️ صارت async الآن
            tweet_ids = await extract_tweet_ids(message.text or "")

            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")

                # عالج التغريدات واحدة تلو الأخرى (تقدر تخلّيها متوازية لو حبيت، بس التسلسل أأمن للريت ليمت)
                for tweet_id in tweet_ids:
                    media = await scrape_media(tweet_id)
                    await reply_media(message, tweet_id, media, bot_url, business_id)
                    await asyncio.sleep(0)

                # حاول حذف رسالة الروابط (اختياري)
                await asyncio.sleep(0.1)
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


@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
        chat_semaphores[chat_id] = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    await chat_queues[chat_id].put(message)

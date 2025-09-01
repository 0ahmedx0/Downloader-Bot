import asyncio
import html
import os
import re
from urllib.parse import urlsplit
import requests
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
PYROGRAM_API_HASH = os.environ.get('HASH')
PYROGRAM_SESSION_STRING = os.environ.get('PYRO_SESSION_STRING')  # يجب أن تكون string session

MAX_FILE_SIZE = 50 * 1024 * 1024  # حد تليجرام للملف داخل البوت

router = Router()
album_accumulator = {}
chat_queues = {}
chat_workers = {}

def extract_tweet_ids(text):
    unshortened_links = ''
    for link in re.findall(r't\.co\/[a-zA-Z0-9]+', text):
        try:
            unshortened_link = requests.get('https://' + link).url
            unshortened_links += '\n' + unshortened_link
        except:
            pass
    tweet_ids = re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)
    return list(dict.fromkeys(tweet_ids)) if tweet_ids else None

# ============== التعديل الوحيد هنا =================
def scrape_media(tweet_id):
    """
    يجلب بيانات التغريدة من FixTweet ثم يطبعها بصيغة
    متوافقة مع بقية الكود: {"text": str, "media_extended": [{type,url}, ...]}
    """
    url = f"https://api.fxtwitter.com/status/{tweet_id}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        # محاولة استخراج رسالة أو نص مختصر للمساعدة في التشخيص
        try:
            data = r.json()
            msg = data.get("message")
        except Exception:
            msg = r.text[:200]
        raise Exception(f"FixTweet HTTP {r.status_code}: {msg}") from e

    data = r.json()
    if not isinstance(data, dict) or "tweet" not in data:
        raise Exception(f"Unexpected FixTweet response: {str(data)[:200]}")

    t = data["tweet"]
    text = t.get("text") or ""
    media_extended = []

    media = t.get("media") or {}

    # صور
    for p in (media.get("photos") or []):
        url = p.get("url")
        if url:
            media_extended.append({"type": "image", "url": url})

    # فيديو/جيـف
    for v in (media.get("videos") or []):
        url_v = v.get("url")
        if url_v:
            mtype = v.get("type", "video")  # قد يعيد "gif" لبعض المقاطع
            media_extended.append({"type": mtype, "url": url_v})

    if not media_extended:
        raise Exception("No media in tweet (private/deleted/no-media)")

    return {"text": text, "media_extended": media_extended}
# ============ نهاية التعديل الوحيد =================

async def download_media(media_url, file_path):
    response = requests.get(media_url, stream=True)
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

# ✅ إرسال الملفات الكبيرة باستخدام string session مع Pyrogram
async def send_large_file_pyro(chat_id, file_path, caption=None):
    async with PyroClient(
        PYROGRAM_SESSION_STRING,
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
        in_memory=True
    ) as client:
        await client.send_document(chat_id=chat_id, document=file_path, caption=caption or "")

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")
    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media["text"]
    user_captions = await db.get_user_captions(message.from_user.id)

    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)

    key = message.chat.id
    if key not in album_accumulator:
        album_accumulator[key] = {"image": [], "video": []}

    try:
        for media in tweet_media['media_extended']:
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)

            if media_type == 'image':
                album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
            elif media_type in ['video', 'gif']:
                album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))

        # ✅ الصور
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
            album_accumulator[key]["image"] = album_accumulator[key]["image"][5:]
            for file_path, _, dir_path in album_to_send:
                os.remove(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
            await asyncio.sleep(5)

        # ✅ الفيديوهات
        if len(album_accumulator[key]["video"]) >= 1:
            for file_path, _, dir_path in album_accumulator[key]["video"]:
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

                os.remove(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)

            album_accumulator[key]["video"] = []
            await asyncio.sleep(5)

    except Exception as e:
        print(e)
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        await message.reply("حدث خطأ أثناء معالجة الوسائط ☹️")

async def process_chat_queue(chat_id):
    while True:
        message = await chat_queues[chat_id].get()
        try:
            await asyncio.sleep(1)
            business_id = message.business_connection_id
            if business_id is None:
                await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])
            bot_url = f"t.me/{(await bot.get_me()).username}"
            tweet_ids = extract_tweet_ids(message.text)
            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")
                for tweet_id in tweet_ids:
                    try:
                        media = scrape_media(tweet_id)
                        await reply_media(message, tweet_id, media, bot_url, business_id)
                    except Exception as e:
                        print(f"[scrape/reply error] {tweet_id}: {e}")
                        if business_id is None:
                            await message.answer(f"⚠️ لم أستطع معالجة الرابط: {tweet_id}")
                    await asyncio.sleep(3)
                await asyncio.sleep(2)
                try:
                    await message.delete()
                except Exception as delete_error:
                    print(f"Error deleting message: {delete_error}")
            else:
                if business_id is None:
                    await message.react([types.ReactionTypeEmoji(emoji="👎")])
                await message.answer("لم يتم العثور على تغريدات.")
        finally:
            chat_queues[chat_id].task_done()

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
    await chat_queues[chat_id].put(message)
    try:
        for media in tweet_media.get('media_extended', []):
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)

            if media_type == 'image':
                album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
            elif media_type in ['video', 'gif']:
                album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))

        # ✅ الصور (أرسل حتى 10 صور كألبوم)
        if len(album_accumulator[key]["image"]) >= ALBUM_IMAGE_LIMIT:
            album_to_send = album_accumulator[key]["image"][:ALBUM_IMAGE_LIMIT]
            media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
            for file_path, _, _ in album_to_send:
                media_group.add_photo(media=FSInputFile(file_path))
            while True:
                try:
                    await message.answer_media_group(media_group.build())
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
            # إزالة المُرسَل وتنظيف
            album_accumulator[key]["image"] = album_accumulator[key]["image"][ALBUM_IMAGE_LIMIT:]
            for file_path, _, dir_path in album_to_send:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
            await asyncio.sleep(5)

        # ✅ الفيديوهات
        if len(album_accumulator[key]["video"]) >= 1:
            for file_path, _, dir_path in album_accumulator[key]["video"]:
                if os.path.getsize(file_path) > MAX_FILE_SIZE:
                    try:
                        await send_large_file_pyro(CHANNEL_IDtwiter, file_path, caption="📤 تم رفع فيديو كبير ✅")
                        if business_id is None:
                            await message.answer(f"✅ تم إرسال فيديو كبير باستخدام Pyrogram: `{os.path.basename(file_path)}`")
                    except Exception as e:
                        print(f"[Pyrogram Error] {e}")
                        if business_id is None:
                            await message.answer("❌ حصل خطأ أثناء إرسال الملف الكبير.")
                else:
                    try:
                        await message.answer_video(FSInputFile(file_path))
                    except Exception as e:
                        print(f"Error sending video: {e}")
                        if business_id is None:
                            await message.answer("❌ حصل خطأ أثناء إرسال الفيديو.")

                try:
                    os.remove(file_path)
                except Exception:
                    pass
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)

            album_accumulator[key]["video"] = []
            await asyncio.sleep(5)

    except Exception as e:
        print(e)
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        if business_id is None:
            await message.reply("حدث خطأ أثناء معالجة الوسائط ☹️")

# -------------------------------
# عامل معالجة كل محادثة (مع دفعات ورسائل تقدم)
# -------------------------------
async def process_chat_queue(chat_id):
    while True:
        message = await chat_queues[chat_id].get()
        try:
            await asyncio.sleep(1)
            business_id = getattr(message, "business_connection_id", None)
            if business_id is None:
                await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])

            bot_url = f"t.me/{(await bot.get_me()).username}"
            tweet_ids = extract_tweet_ids(message.text or "")

            if tweet_ids:
                batches = list(chunk_list(tweet_ids, MAX_PER_BATCH))
                total = len(tweet_ids)
                done = 0
                progress_msg = None

                if business_id is None:
                    progress_msg = await message.answer(f"📥 تم استقبال {total} رابط. المعالجة على دفعات ({len(batches)}).")

                # لفت الانتباه بأن هناك معالجة جارية
                if business_id is None:
                    try:
                        await bot.send_chat_action(message.chat.id, "typing")
                    except Exception:
                        pass

                for bi, batch in enumerate(batches, start=1):
                    for tweet_id in batch:
                        try:
                            media = scrape_media(tweet_id)
                            await reply_media(message, tweet_id, media, bot_url, business_id)
                        except Exception as e:
                            print(f"[scrape/reply error] {tweet_id}: {e}")
                            if business_id is None:
                                await message.answer(f"⚠️ لم أستطع معالجة الرابط: {tweet_id}")
                        done += 1
                        await asyncio.sleep(3)

                    if progress_msg:
                        try:
                            await progress_msg.edit_text(f"⌛ دفعة {bi}/{len(batches)} — التقدم: {done}/{total}")
                        except Exception:
                            pass

                if progress_msg:
                    try:
                        await progress_msg.edit_text(f"✅ اكتملت المعالجة: {done}/{total}")
                    except Exception:
                        pass

                await asyncio.sleep(2)
                try:
                    await message.delete()
                except Exception as delete_error:
                    print(f"Error deleting message: {delete_error}")

            else:
                if business_id is None:
                    await message.react([types.ReactionTypeEmoji(emoji="👎")])
                    await message.answer("لم يتم العثور على تغريدات في الرسالة.")
        finally:
            chat_queues[chat_id].task_done()

# -------------------------------
# نقاط الدخول: رسالة واحدة قد تحتوي عدة روابط
# -------------------------------
LINKS_REGEX = r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"

@router.message(F.text.regexp(LINKS_REGEX))
@router.business_message(F.text.regexp(LINKS_REGEX))
async def handle_tweet_links(message):
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
    await chat_queues[chat_id].put(message)

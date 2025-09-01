# -*- coding: utf-8 -*-

# ==============================================================================
#                      الكود الكامل والجاهز للنسخ واللصق
# ==============================================================================

import asyncio
import html
import os
import re
import shutil  # مهم لحذف المجلدات وملفاتها بأمان
from urllib.parse import urlsplit

import requests
from aiogram import types, Router, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from pyrogram import Client as PyroClient

# --- تأكد من أن هذه الملفات والمتغيرات موجودة في مشروعك ---
import messages as bm
from config import OUTPUT_DIR, CHANNEL_IDtwiter
from main import bot, db, send_analytics
# ---------------------------------------------------------

# --- إعدادات Pyrogram والمتغيرات العامة ---
PYROGRAM_API_ID = int(os.environ.get('ID'))
PYROGRAM_API_HASH = os.environ.get('HASH')
PYROGRAM_SESSION_STRING = os.environ.get('PYRO_SESSION_STRING')
MAX_FILE_SIZE = 50 * 1024 * 1024  # حد حجم الملف 50 ميجابايت
TELEGRAM_ALBUM_LIMIT = 10  # الحد الأقصى للوسائط في الألبوم الواحد على تليجرام

router = Router()
chat_queues = {}
# متغير لإدارة حالة العامل ومنع تشغيل أكثر من عامل لنفس المحادثة
is_worker_active = {}


# ==============================================================================
#                            الدوال المساعدة (Helpers)
# ==============================================================================

def extract_tweet_ids(text: str) -> list[str] | None:
    """
    يستخرج كل أرقام تعريف التغريدات (Tweet IDs) من نص الرسالة.
    """
    unshortened_links = ''
    for link in re.findall(r't\.co\/[a-zA-Z0-9]+', text):
        try:
            unshortened_link = requests.get('https://' + link, timeout=5).url
            unshortened_links += '\n' + unshortened_link
        except requests.RequestException:
            pass
    tweet_ids = re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)
    return list(dict.fromkeys(tweet_ids)) if tweet_ids else None

def scrape_media(tweet_id: str) -> dict | None:
    """
    يجلب بيانات الوسائط للتغريدة من API. يعيد None في حال الفشل.
    """
    try:
        r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
        r.raise_for_status()
        return r.json()
    except requests.exceptions.RequestException as e:
        print(f"Request failed for tweet {tweet_id}: {e}")
        return None
    except requests.exceptions.JSONDecodeError:
        print(f"Failed to decode JSON for tweet {tweet_id}")
        return None

async def download_media(media_url: str, file_path: str):
    """
    يحمل ملف وسائط من رابط ويحفظه في المسار المحدد.
    """
    response = requests.get(media_url, stream=True, timeout=30)
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

async def send_large_file_pyro(chat_id: int, file_path: str, caption: str | None = None):
    """
    يرسل الملفات الكبيرة باستخدام Pyrogram (String Session).
    """
    async with PyroClient(PYROGRAM_SESSION_STRING, api_id=PYROGRAM_API_ID, api_hash=PYROGRAM_API_HASH, in_memory=True) as client:
        await client.send_document(chat_id=chat_id, document=file_path, caption=caption or "")


# ==============================================================================
#                            منطق المعالجة الأساسي (النسخة النهائية)
# ==============================================================================

async def process_single_tweet(message: types.Message, tweet_id: str, bot_url: str, business_id: str | None):
    """
    دالة معزولة ومكتفية ذاتيًا لمعالجة تغريدة واحدة بالكامل:
    تحميل -> إرسال -> تنظيف.
    """
    tweet_dir = os.path.join(OUTPUT_DIR, tweet_id)
    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)
    
    try:
        await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")
        
        # 1. جلب بيانات التغريدة والتحقق منها بقوة
        tweet_media = scrape_media(tweet_id)
        if tweet_media is None or not isinstance(tweet_media, dict):
            print(f"Failed to fetch valid data for tweet {tweet_id}. API returned None or invalid type.")
            await message.reply(f"لم أتمكن من جلب بيانات التغريدة `{tweet_id}`. قد تكون محذوفة أو الحساب خاص.")
            return # إيقاف معالجة هذه التغريدة والانتقال للتالية

        post_caption = tweet_media.get("text", "")
        
        # 2. التحقق من وجود الكابشن المخصص للمستخدم
        user_captions = await db.get_user_captions(message.from_user.id)
        if user_captions is None:
            user_captions = {} 
            
        final_caption = bm.captions(user_captions, post_caption, bot_url)

        images_to_send = []
        videos_to_send = []

        for media in tweet_media.get('media_extended', []):
            media_url = media['url']
            file_name = os.path.basename(urlsplit(media_url).path)
            file_path = os.path.join(tweet_dir, file_name)
            await download_media(media_url, file_path)
            
            if media['type'] == 'image': images_to_send.append(file_path)
            elif media['type'] in ['video', 'gif']: videos_to_send.append(file_path)

        if not images_to_send and not videos_to_send:
            await message.reply(f"التغريدة `{tweet_id}` لا تحتوي على صور أو فيديوهات قابلة للتحميل.")
            return

        if images_to_send:
            for i in range(0, len(images_to_send), TELEGRAM_ALBUM_LIMIT):
                chunk = images_to_send[i:i + TELEGRAM_ALBUM_LIMIT]
                media_group = MediaGroupBuilder(caption=final_caption if i == 0 else None)
                for img_path in chunk: media_group.add_photo(media=FSInputFile(img_path))
                while True:
                    try:
                        await message.answer_media_group(media_group.build())
                        break
                    except TelegramRetryAfter as e: await asyncio.sleep(e.retry_after)

        for video_path in videos_to_send:
            video_caption = final_caption if not images_to_send and videos_to_send.index(video_path) == 0 else None
            try:
                if os.path.getsize(video_path) > MAX_FILE_SIZE:
                    await send_large_file_pyro(CHANNEL_IDtwiter, video_path, caption=f"📤 فيديو كبير من تغريدة: {tweet_id}")
                    await message.answer(f"✅ تم إرسال فيديو بحجم كبير: `{os.path.basename(video_path)}`")
                else:
                    await message.answer_video(FSInputFile(video_path), caption=video_caption)
            except Exception as e:
                print(f"Error sending video {video_path}: {e}")
                await message.answer(f"❌ خطأ أثناء إرسال الفيديو: `{os.path.basename(video_path)}`")
            await asyncio.sleep(1)

    except Exception as e:
        print(f"An unexpected error occurred while processing tweet {tweet_id}: {e}")
        await message.reply(f"حدث خطأ غير متوقع أثناء معالجة التغريدة:\n`{tweet_id}`\nالسبب: `{e}`")
    finally:
        if os.path.exists(tweet_dir):
            shutil.rmtree(tweet_dir)


async def process_chat_queue(chat_id: int):
    """
    العامل (Worker): يعالج كل الرسائل في قائمة الانتظار الخاصة بمحادثة معينة.
    """
    is_worker_active[chat_id] = True
    print(f"Worker started for chat {chat_id}")
    
    while not chat_queues[chat_id].empty():
        message = await chat_queues[chat_id].get()
        try:
            business_id = message.business_connection_id
            if business_id is None:
                await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])
            
            bot_url = f"t.me/{(await bot.get_me()).username}"
            tweet_ids = extract_tweet_ids(message.text)

            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")
                
                for tweet_id in tweet_ids:
                    await process_single_tweet(message, tweet_id, bot_url, business_id)
                    await asyncio.sleep(3)

                try:
                    await message.delete()
                except Exception as delete_error:
                    print(f"Could not delete message {message.message_id}: {delete_error}")
            
        except Exception as e:
            print(f"Critical error in processing queue for chat {chat_id}: {e}")
        finally:
            chat_queues[chat_id].task_done()

    is_worker_active[chat_id] = False
    print(f"Worker finished for chat {chat_id}. Queue is empty.")


# ==============================================================================
#                      معالج الرسائل (Message Handler)
# ==============================================================================

@router.message(F.text.regexp(r"(https?://(www.)?(twitter|x).com/\S+|https?://t.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www.)?(twitter|x).com/\S+|https?://t.co/\S+)"))
async def handle_tweet_links(message: types.Message):
    """
    نقطة الدخول: يستقبل الرسائل ويضعها في قائمة الانتظار.
    """
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        is_worker_active[chat_id] = False

    await chat_queues[chat_id].put(message)

    if not is_worker_active.get(chat_id, False):
        asyncio.create_task(process_chat_queue(chat_id))

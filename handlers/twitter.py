import asyncio
import html
import os
import re
from urllib.parse import urlsplit
import requests
import aiohttp # ✅ إضافة aiohttp للطلبات غير المتزامنة

from aiogram import types, Router, F
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.exceptions import TelegramRetryAfter
from pyrogram import Client as PyroClient # ✅ Pyrogram

import messages as bm
from config import OUTPUT_DIR, CHANNEL_IDtwiter
from main import bot, db, send_analytics # افترض أن هذه الوحدات متوفرة

# ✅ إعدادات Pyrogram من المتغيرات البيئية (string session)
PYROGRAM_API_ID = int(os.environ.get('ID'))
PYROGRAM_API_HASH = os.environ.get('HASH')
PYROGRAM_SESSION_STRING = os.environ.get('PYRO_SESSION_STRING') # يجب أن تكون string session

MAX_FILE_SIZE = 50 * 1024 * 1024 # حد تليجرام للملف داخل البوت

router = Router()
album_accumulator = {}
chat_queues = {}
chat_workers = {}

# ✅ دالة مساعدة لفك اختصار الروابط بشكل غير متزامن
async def unshorten_link_async(session, link):
    try:
        # print(f"Attempting to unshorten: https://{link}") # لغرض التصحيح
        async with session.get('https://' + link, allow_redirects=True, timeout=10) as response:
            final_url = str(response.url)
            # print(f"Unshortened {link} to {final_url}") # لغرض التصحيح
            return final_url
    except Exception as e:
        print(f"❌ Error unshortening link {link}: {e}")
        return None

# ✅ تعديل دالة استخراج معرفات التغريدات لتكون غير متزامنة وتستخدم aiohttp
async def extract_tweet_ids_async(text):
    print(f"🔍 Starting to extract tweet IDs from text (first 100 chars): {text[:100]}...")
    unshortened_links_tasks = []
    tco_links = re.findall(r't\.co\/[a-zA-Z0-9]+', text)
    unshortened_links_str = ""

    if tco_links:
        print(f"🔗 Found {len(tco_links)} t.co links. Unshortening concurrently...")
        async with aiohttp.ClientSession() as session:
            for link in tco_links:
                unshortened_links_tasks.append(unshorten_link_async(session, link))
            unshortened_links_results = await asyncio.gather(*unshortened_links_tasks)
            unshortened_links_str = '\n'.join([ul for ul in unshortened_links_results if ul])
        print(f"🔗 Finished unshortening {len(tco_links)} t.co links.")
    else:
        print("🔗 No t.co links found.")

    full_text_for_regex = text + '\n' + unshortened_links_str
    tweet_ids = re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", full_text_for_regex)
    
    unique_tweet_ids = list(dict.fromkeys(tweet_ids)) if tweet_ids else None
    if unique_tweet_ids:
        print(f"✅ Extracted {len(unique_tweet_ids)} unique tweet IDs: {unique_tweet_ids}")
    else:
        print("⛔ No Twitter/X tweet IDs found.")
    return unique_tweet_ids

# تبقى هذه الدالة متزامنة لأنها تستخدم requests (يمكن تحويلها لـ aiohttp إذا رغبت)
def scrape_media(tweet_id):
    print(f"📡 Scraping media for tweet ID: {tweet_id} from VxTwitter API.")
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}', timeout=10) # أضف timeout
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            error_message = f'API returned error: {html.unescape(match.group(1))}'
            print(f"❌ VxTwitter API JSON Decode Error for {tweet_id}: {error_message}")
            raise Exception(error_message)
        print(f"❌ VxTwitter API JSON Decode Error, no specific message found for {tweet_id}")
        raise # أعد رفع الخطأ الأصلي إذا لم يتم العثور على وصف

# تبقى هذه الدالة متزامنة لأنها تستخدم requests (يمكن تحويلها لـ aiohttp إذا رغبت)
async def download_media(media_url, file_path):
    print(f"⬇️ Starting download: {media_url} to {file_path}")
    # يمكن هنا استخدام asyncio.to_thread إذا كنت تريد تشغيلها في ThreadPoolExecutor دون blocking loop الرئيسي
    # ولكن للتنزيلات الكبيرة، يفضل aiohttp للحصول على async بالكامل
    response = requests.get(media_url, stream=True, timeout=30) # أضف timeout
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
    print(f"✅ Download complete: {file_path}")

# ✅ إرسال الملفات الكبيرة باستخدام string session مع Pyrogram
async def send_large_file_pyro(chat_id, file_path, caption=None):
    print(f"📤 Sending large file via Pyrogram to chat {chat_id}: {file_path}")
    try:
        async with PyroClient(
            PYROGRAM_SESSION_STRING,
            api_id=PYROGRAM_API_ID,
            api_hash=PYROGRAM_API_HASH,
            in_memory=True
        ) as client:
            await client.send_document(chat_id=chat_id, document=file_path, caption=caption or "")
        print(f"✅ Large file sent successfully via Pyrogram: {file_path}")
    except Exception as e:
        print(f"❌ [Pyrogram Error] Failed to send {file_path}: {e}")
        raise # إعادة رفع الخطأ ليتم التعامل معه بواسطة try/except الخارجية

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")
    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media["text"]
    user_captions = await db.get_user_captions(message.from_user.id) # افترض أن هذه الدالة موجودة
    
    # تأكد من إنشاء الدليل قبل أي عمليات عليه
    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)
        print(f"📂 Created directory: {tweet_dir}")

    key = message.chat.id
    if key not in album_accumulator:
        album_accumulator[key] = {"image": [], "video": []}
        print(f"Initializing album_accumulator for chat {key}")

    try:
        # ✅ تحميل جميع الوسائط أولاً
        for media in tweet_media['media_extended']:
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)

            if media_type == 'image':
                album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
            elif media_type in ['video', 'gif']:
                album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))
        
        print(f"Loaded {len(album_accumulator[key]['image'])} images and {len(album_accumulator[key]['video'])} videos for tweet {tweet_id}")

        # ✅ إرسال الصور في مجموعات (ألبومات)
        while album_accumulator[key]["image"]:
            # إرسال 5 صور كحد أقصى لكل مجموعة
            album_to_send = album_accumulator[key]["image"][:5]
            
            # في حال وجود صورة واحدة فقط، يتم إرسالها كصورة عادية وليس ألبوم
            if len(album_to_send) == 1:
                file_path, _, dir_path = album_to_send[0]
                media_caption = bm.captions(user_captions, post_caption, bot_url) if album_to_send[0] == album_accumulator[key]["image"][0] else None
                print(f"🖼️ Sending single image for tweet {tweet_id}: {file_path}")
                while True:
                    try:
                        await message.answer_photo(FSInputFile(file_path), caption=media_caption)
                        break
                    except TelegramRetryAfter as e:
                        print(f"⏳ TelegramRetryAfter for image: {e.retry_after} seconds. Retrying...")
                        await asyncio.sleep(e.retry_after)
                
            else: # إذا كان هناك أكثر من صورة واحدة، يتم إرسالها كألبوم
                media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
                for file_path, _, _ in album_to_send:
                    media_group.add_photo(media=FSInputFile(file_path))
                print(f"📸 Sending image album of {len(album_to_send)} photos for tweet {tweet_id}")
                while True:
                    try:
                        await message.answer_media_group(media_group.build())
                        break
                    except TelegramRetryAfter as e:
                        print(f"⏳ TelegramRetryAfter for album: {e.retry_after} seconds. Retrying...")
                        await asyncio.sleep(e.retry_after)

            # إزالة الصور المرسلة وحذف الملفات المؤقتة
            for file_path, _, dir_path in album_to_send:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"🗑️ Removed file: {file_path}")
                # حذف الدليل إذا أصبح فارغًا
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    print(f"🗑️ Removed empty directory: {dir_path}")

            album_accumulator[key]["image"] = album_accumulator[key]["image"][len(album_to_send):]

        # ✅ إرسال الفيديوهات
        for file_path, _, dir_path in album_accumulator[key]["video"]:
            video_caption = bm.captions(user_captions, post_caption, bot_url) # الكابشن لكل فيديو
            print(f"🎥 Preparing to send video: {file_path} for tweet {tweet_id}")
            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                try:
                    # يجب أن يكون CHANNEL_IDtwiter هو معرف الدردشة الذي يملك الجلسة لإرسال ملفات Pyro
                    # إذا أردت الإرسال للمستخدم، استخدم message.chat.id
                    await send_large_file_pyro(message.chat.id, file_path, caption=video_caption)
                    await message.answer(f"✅ تم إرسال فيديو كبير باستخدام Pyrogram: `{os.path.basename(file_path)}`")
                except Exception as e:
                    print(f"❌ [Pyrogram Error] Failed to send large file: {e}")
                    await message.answer("❌ حصل خطأ أثناء إرسال الملف الكبير بواسطة Pyrogram.")
            else:
                while True:
                    try:
                        await message.answer_video(FSInputFile(file_path), caption=video_caption)
                        break
                    except TelegramRetryAfter as e:
                        print(f"⏳ TelegramRetryAfter for video: {e.retry_after} seconds. Retrying...")
                        await asyncio.sleep(e.retry_after)
                    except Exception as e:
                        print(f"❌ Error sending video {file_path}: {e}")
                        await message.answer("❌ حصل خطأ أثناء إرسال الفيديو.")
                        break # الخروج من حلقة المحاولة في حالة خطأ آخر غير RetryAfter

            # إزالة الفيديو بعد الإرسال وحذف الملف المؤقت
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🗑️ Removed file: {file_path}")
            if os.path.exists(dir_path) and not os.listdir(dir_path):
                os.rmdir(dir_path)
                print(f"🗑️ Removed empty directory: {dir_path}")

        album_accumulator[key]["video"] = [] # تفريغ قائمة الفيديوهات لهذه الدردشة

    except Exception as e:
        print(f"❌ General error in reply_media for tweet {tweet_id}: {e}")
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        await message.reply(f"حدث خطأ أثناء معالجة الوسائط لتغريدة {tweet_id} ☹️: {e}")
    finally:
        # التأكد من تنظيف الدليل الخاص بالتغريدة حتى لو حدث خطأ
        if os.path.exists(tweet_dir) and not os.listdir(tweet_dir):
            os.rmdir(tweet_dir)
            print(f"🗑️ Final cleanup: Removed empty tweet directory {tweet_dir}")
        elif os.path.exists(tweet_dir):
            # إذا لم يكن فارغًا، فهذا يعني أن هناك ملفات لم يتم حذفها (حدث خطأ أثناء المعالجة)
            print(f"⚠️ Warning: Directory {tweet_dir} not empty after processing.")


async def process_chat_queue(chat_id):
    print(f"Starting processing queue for chat {chat_id}")
    while True:
        message = await chat_queues[chat_id].get()
        print(f"🔄 Processing message from queue for chat {chat_id}, message ID: {message.message_id}")
        try:
            business_id = message.business_connection_id
            if business_id is None:
                await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")]) # رد فعل للمستخدم
            bot_url = f"t.me/{(await bot.get_me()).username}"
            
            # ✅ استخدام الدالة الجديدة غير المتزامنة
            tweet_ids = await extract_tweet_ids_async(message.text)
            
            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")
                for tweet_id in tweet_ids:
                    print(f"🚀 Handling tweet ID: {tweet_id} in chat {chat_id}")
                    try:
                        media = scrape_media(tweet_id)
                        await reply_media(message, tweet_id, media, bot_url, business_id)
                    except Exception as e:
                        print(f"❌ Error processing individual tweet {tweet_id}: {e}")
                        await message.reply(f"حدث خطأ أثناء معالجة التغريدة {tweet_id}: {e}")
            else:
                if business_id is None:
                    await message.react([types.ReactionTypeEmoji(emoji="👎")])
                await message.answer("لم يتم العثور على روابط X/Twitter صالحة في رسالتك.")
            
            # محاولة حذف الرسالة الأصلية بعد الانتهاء من المعالجة
            try:
                if business_id is None: # لا تحاول حذف رسائل الأعمال بشكل افتراضي إذا كان هذا قد يسبب مشاكل
                    await message.delete()
                    print(f"🗑️ Deleted original message {message.message_id} in chat {chat_id}")
            except Exception as delete_error:
                print(f"❌ Error deleting message {message.message_id} in chat {chat_id}: {delete_error}")
        finally:
            chat_queues[chat_id].task_done()
            print(f"✅ Finished processing message from queue for chat {chat_id}.")

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message: types.Message):
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        # ابدأ Worker للدردشة إذا لم يكن موجودًا
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
        print(f"🆕 Created new queue and worker for chat {chat_id}")
    else:
        print(f"➡️ Adding message to existing queue for chat {chat_id}")
    await chat_queues[chat_id].put(message)

# تأكد من أن هذه الوحدة يتم إضافتها إلى Dispatcher الرئيسي
# dp.include_router(router)

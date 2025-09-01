import asyncio
import html
import os
import re
from urllib.parse import urlsplit
import requests
import aiohttp

from aiogram import types, Router, F
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.exceptions import TelegramRetryAfter, AiogramError # إضافة AiogramError لمعالجة أخطاء تليجرام
from pyrogram import Client as PyroClient

import messages as bm
from config import OUTPUT_DIR, CHANNEL_IDtwiter
from main import bot, db, send_analytics # افترض أن هذه الوحدات متوفرة

# ✅ إعدادات Pyrogram من المتغيرات البيئية (string session)
PYROGRAM_API_ID = int(os.environ.get('ID'))
PYROGRAM_API_HASH = os.environ.get('HASH')
PYROGRAM_SESSION_STRING = os.environ.get('PYRO_SESSION_STRING')

MAX_FILE_SIZE = 50 * 1024 * 1024

router = Router()
album_accumulator = {}
chat_queues = {}
chat_workers = {}

async def unshorten_link_async(session, link):
    try:
        async with session.get('https://' + link, allow_redirects=True, timeout=10) as response:
            response.raise_for_status() # أضف للتحقق من الأكواد 4xx/5xx
            final_url = str(response.url)
            return final_url
    except aiohttp.ClientError as e: # Catch specific aiohttp errors
        print(f"❌ Aiohttp Client Error unshortening link {link}: {e}")
        return None
    except asyncio.TimeoutError:
        print(f"❌ Timeout unshortening link {link}")
        return None
    except Exception as e:
        print(f"❌ Generic Error unshortening link {link}: {e}")
        return None

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

# تبقى هذه الدالة متزامنة لأنها تستخدم requests
def scrape_media(tweet_id):
    print(f"📡 Scraping media for tweet ID: {tweet_id} from VxTwitter API.")
    try:
        r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}', timeout=10)
        r.raise_for_status() # يرفع استثناء HTTPError للأكواد 4xx/5xx
        try:
            return r.json()
        except requests.exceptions.JSONDecodeError:
            # معالجة الخطأ المحدد من VxTwitter
            if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
                error_message = f'VxTwitter API returned error: {html.unescape(match.group(1))}'
                print(f"❌ VxTwitter API JSON Decode Error for {tweet_id}: {error_message}")
                raise ValueError(error_message) # رفع ValueError لتمييزه
            # إذا لم يتم العثور على رسالة خطأ محددة في الميتا تاج
            print(f"❌ VxTwitter API JSON Decode Error for {tweet_id} (no specific description found).")
            raise ValueError(f"Failed to decode JSON from VxTwitter for tweet {tweet_id}") # رفع ValueError
    except requests.exceptions.Timeout:
        print(f"❌ Timeout scraping media for tweet {tweet_id} from VxTwitter API.")
        raise ConnectionError(f"Timeout connecting to VxTwitter API for tweet {tweet_id}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Request error scraping media for tweet {tweet_id} from VxTwitter API: {e}")
        raise ConnectionError(f"Request error with VxTwitter API for tweet {tweet_id}: {e}")
    except Exception as e:
        print(f"❌ Unexpected error in scrape_media for tweet {tweet_id}: {e}")
        raise

# تبقى هذه الدالة متزامنة لأنها تستخدم requests
async def download_media(media_url, file_path):
    print(f"⬇️ Starting download: {media_url} to {file_path}")
    try:
        response = requests.get(media_url, stream=True, timeout=30)
        response.raise_for_status()
        with open(file_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)
        print(f"✅ Download complete: {file_path}")
    except requests.exceptions.Timeout:
        print(f"❌ Timeout downloading media {media_url}")
        raise ConnectionError(f"Timeout downloading media {media_url}")
    except requests.exceptions.RequestException as e:
        print(f"❌ Request error downloading media {media_url}: {e}")
        raise ConnectionError(f"Request error downloading media {media_url}: {e}")
    except Exception as e:
        print(f"❌ Unexpected error downloading media {media_url}: {e}")
        raise

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
        # يجب أن تتعامل هذه النقطة مع الأخطاء وتسمح بالاستمرار
        # رفع الاستثناء سيؤدي إلى إيقاف معالجة التغريدات المتبقية في هذه الرسالة
        # إذا كنت تريد استمرار المعالجة، يمكنك تسجيل الخطأ هنا وعدم إعادة الرفع
        raise

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    # التأكد من أن tweet_media و media_extended موجودين
    if not tweet_media or "media_extended" not in tweet_media:
        raise ValueError(f"No media_extended found in tweet data for {tweet_id}")

    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")
    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media.get("text", "") # استخدام .get لتجنب KeyError
    user_captions = await db.get_user_captions(message.from_user.id)
    
    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)
        print(f"📂 Created directory: {tweet_dir}")

    key = message.chat.id
    if key not in album_accumulator:
        album_accumulator[key] = {"image": [], "video": []}
        print(f"Initializing album_accumulator for chat {key}")

    # Reset accumulator for this specific tweet ID to avoid mixing
    # Although album_accumulator[key] is chat-wide, for a single tweet processing it should be empty for a clean slate.
    # The logic might need adjustment if you want to accumulate media from multiple tweets into one album.
    # As currently implemented, each reply_media call processes one tweet, so a fresh list is implicitly handled.

    try:
        download_tasks = []
        for media_item in tweet_media['media_extended']:
            media_url = media_item['url']
            media_type = media_item['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            # إضافة مهام التنزيل إلى قائمة (لتحميلها بشكل متزامن إذا أردت)
            download_tasks.append(download_media(media_url, file_name))
            if media_type == 'image':
                album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
            elif media_type in ['video', 'gif']:
                album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))

        # تنفيذ جميع التنزيلات بشكل متزامن
        await asyncio.gather(*download_tasks, return_exceptions=True) # return_exceptions للسماح بانتهاء المهام حتى لو حدث خطأ في إحداها

        print(f"Loaded {len(album_accumulator[key]['image'])} images and {len(album_accumulator[key]['video'])} videos for tweet {tweet_id}")

        # ✅ إرسال الصور في مجموعات (ألبومات)
        while album_accumulator[key]["image"]:
            album_to_send = album_accumulator[key]["image"][:5]
            
            if len(album_to_send) == 1:
                file_path, _, dir_path = album_to_send[0]
                media_caption = bm.captions(user_captions, post_caption, bot_url)
                print(f"🖼️ Sending single image for tweet {tweet_id}: {file_path}")
                try:
                    await message.answer_photo(FSInputFile(file_path), caption=media_caption)
                except TelegramRetryAfter as e:
                    print(f"⏳ TelegramRetryAfter for image: {e.retry_after} seconds. Retrying...")
                    await asyncio.sleep(e.retry_after)
                    await message.answer_photo(FSInputFile(file_path), caption=media_caption) # إعادة المحاولة
                except AiogramError as e:
                    print(f"❌ Aiogram error sending single image {file_path}: {e}")
                    await message.answer(f"❌ حصل خطأ في تليجرام أثناء إرسال الصورة {os.path.basename(file_path)}: {e}")
                
            else:
                media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
                for file_path, _, _ in album_to_send:
                    if os.path.exists(file_path): # تأكد من وجود الملف قبل إضافته للألبوم
                         media_group.add_photo(media=FSInputFile(file_path))
                    else:
                        print(f"⚠️ Warning: File {file_path} not found for album, skipping.")
                
                if media_group.media: # تأكد أن هناك وسائط لإرسالها
                    print(f"📸 Sending image album of {len(media_group.media)} photos for tweet {tweet_id}")
                    try:
                        await message.answer_media_group(media_group.build())
                    except TelegramRetryAfter as e:
                        print(f"⏳ TelegramRetryAfter for album: {e.retry_after} seconds. Retrying...")
                        await asyncio.sleep(e.retry_after)
                        await message.answer_media_group(media_group.build()) # إعادة المحاولة
                    except AiogramError as e:
                        print(f"❌ Aiogram error sending image album: {e}")
                        await message.answer(f"❌ حصل خطأ في تليجرام أثناء إرسال ألبوم الصور: {e}")
                else:
                    print(f"⚠️ No media to send in album for tweet {tweet_id}")


            for file_path, _, dir_path in album_to_send:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"🗑️ Removed file: {file_path}")
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    print(f"🗑️ Removed empty directory: {dir_path}")

            album_accumulator[key]["image"] = album_accumulator[key]["image"][len(album_to_send):]

        # ✅ إرسال الفيديوهات
        for file_path, _, dir_path in album_accumulator[key]["video"]:
            video_caption = bm.captions(user_captions, post_caption, bot_url)
            print(f"🎥 Preparing to send video: {file_path} for tweet {tweet_id}")
            
            if not os.path.exists(file_path):
                print(f"❌ Error: Video file not found, skipping: {file_path}")
                continue # تخطي هذا الفيديو والمضي قدمًا

            if os.path.getsize(file_path) > MAX_FILE_SIZE:
                try:
                    await send_large_file_pyro(message.chat.id, file_path, caption=video_caption)
                    await message.answer(f"✅ تم إرسال فيديو كبير باستخدام Pyrogram: `{os.path.basename(file_path)}`")
                except Exception as e:
                    print(f"❌ [Pyrogram Error] Failed to send large file {file_path}: {e}")
                    await message.answer(f"❌ حصل خطأ أثناء إرسال الملف الكبير بواسطة Pyrogram ({os.path.basename(file_path)}).")
            else:
                try:
                    await message.answer_video(FSInputFile(file_path), caption=video_caption)
                except TelegramRetryAfter as e:
                    print(f"⏳ TelegramRetryAfter for video: {e.retry_after} seconds. Retrying...")
                    await asyncio.sleep(e.retry_after)
                    await message.answer_video(FSInputFile(file_path), caption=video_caption) # إعادة المحاولة
                except AiogramError as e:
                    print(f"❌ Aiogram error sending video {file_path}: {e}")
                    await message.answer(f"❌ حصل خطأ في تليجرام أثناء إرسال الفيديو {os.path.basename(file_path)}: {e}")
                except Exception as e:
                    print(f"❌ Unexpected error sending video {file_path}: {e}")
                    await message.answer(f"❌ حصل خطأ غير متوقع أثناء إرسال الفيديو {os.path.basename(file_path)}.")

            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🗑️ Removed file: {file_path}")
            if os.path.exists(dir_path) and not os.listdir(dir_path):
                os.rmdir(dir_path)
                print(f"🗑️ Removed empty directory: {dir_path}")

        album_accumulator[key]["video"] = []

    except Exception as e:
        print(f"❌ Critical error in reply_media for tweet {tweet_id}: {e}")
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        await message.reply(f"حدث خطأ فادح أثناء معالجة الوسائط لتغريدة {tweet_id} ☹️: {e}")
    finally:
        if os.path.exists(tweet_dir) and not os.listdir(tweet_dir):
            os.rmdir(tweet_dir)
            print(f"🗑️ Final cleanup: Removed empty tweet directory {tweet_dir}")
        elif os.path.exists(tweet_dir):
            print(f"⚠️ Warning: Directory {tweet_dir} not empty after processing for tweet {tweet_id}.")


async def process_chat_queue(chat_id):
    print(f"Starting processing queue for chat {chat_id}")
    while True:
        message = await chat_queues[chat_id].get()
        print(f"🔄 Processing message from queue for chat {chat_id}, message ID: {message.message_id}")
        try:
            business_id = message.business_connection_id
            if business_id is None:
                await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])
            bot_url = f"t.me/{(await bot.get_me()).username}"
            
            tweet_ids = await extract_tweet_ids_async(message.text)
            
            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")
                for tweet_id in tweet_ids:
                    print(f"🚀 Handling tweet ID: {tweet_id} in chat {chat_id}")
                    try:
                        media = scrape_media(tweet_id)
                        # ✅ التحقق من وجود 'media_extended' قبل تمريرها
                        if media and 'media_extended' in media:
                            await reply_media(message, tweet_id, media, bot_url, business_id)
                        else:
                            error_msg = f"لم يتم العثور على وسائط لتغريدة {tweet_id} أو فشل API.vxtwitter."
                            print(f"❌ {error_msg}")
                            await message.reply(error_msg)
                            if business_id is None:
                                await message.react([types.ReactionTypeEmoji(emoji="👎")])
                    except (ValueError, ConnectionError, AiogramError) as e: # Catch specific errors
                        print(f"❌ Known error processing individual tweet {tweet_id}: {e}")
                        await message.reply(f"حدث خطأ أثناء معالجة التغريدة {tweet_id}: {e}")
                        if business_id is None:
                            await message.react([types.ReactionTypeEmoji(emoji="👎")])
                    except Exception as e:
                        print(f"❌ Unexpected error processing individual tweet {tweet_id}: {e}")
                        await message.reply(f"حدث خطأ غير متوقع أثناء معالجة التغريدة {tweet_id}. الرجاء المحاولة مرة أخرى لاحقًا.")
                        if business_id is None:
                            await message.react([types.ReactionTypeEmoji(emoji="👎")])
            else:
                if business_id is None:
                    await message.react([types.ReactionTypeEmoji(emoji="👎")])
                await message.answer("لم يتم العثور على روابط X/Twitter صالحة في رسالتك.")
            
            try:
                if business_id is None:
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
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
        print(f"🆕 Created new queue and worker for chat {chat_id}")
    else:
        print(f"➡️ Adding message to existing queue for chat {chat_id}")
    await chat_queues[chat_id].put(message)

import asyncio
import html
import os
import re
from urllib.parse import urlsplit

import requests
from aiogram import types, Router, F
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder
from aiogram.utils.exceptions import FloodWait
import messages as bm
from config import OUTPUT_DIR, CHANNEL_IDtwiter
from main import bot, db, send_analytics

MAX_FILE_SIZE = 500 * 1024 * 1024
MAX_RETRIES = 3  # أقصى عدد من المحاولات عند الحظر

router = Router()
album_accumulator = {}
chat_queues = {}
chat_workers = {}

# تحديد عدد التغريدات التي يمكن معالجتها في نفس الوقت
CONCURRENT_TWEETS_SEMAPHORE = asyncio.Semaphore(3)

def extract_tweet_ids(text):
    """استخراج معرفات التغريدات من النص"""
    unshortened_links = ''
    for link in re.findall(r't\.co\/[a-zA-Z0-9]+', text):
        try:
            unshortened_link = requests.get('https://' + link).url
            unshortened_links += '\n' + unshortened_link
        except:
            pass

    tweet_ids = re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)
    return list(dict.fromkeys(tweet_ids)) if tweet_ids else None

async def scrape_media_with_retry(tweet_id):
    """استخراج الوسائط مع إعادة المحاولة"""
    async with CONCURRENT_TWEETS_SEMAPHORE:
        return await asyncio.to_thread(scrape_media, tweet_id)

def scrape_media(tweet_id):
    """الدالة الأساسية لاستخراج البيانات (تستخدم requests)"""
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            raise Exception(f'API returned error: {html.unescape(match.group(1))}')
        raise

async def download_media(media_url, file_path):
    """تنزيل الوسائط مع التحكم في حجم الملف"""
    response = requests.get(media_url, stream=True)
    response.raise_for_status()
    
    if int(response.headers.get('content-length', 0)) > MAX_FILE_SIZE:
        raise ValueError("File size exceeds maximum allowed limit")
    
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

async def send_media_group_with_retry(message, media_group, business_id):
    """إرسال مجموعة الوسائط مع إعادة المحاولة"""
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            await message.answer_media_group(media_group)
            await asyncio.sleep(5)  # تأخير بعد كل إرسال
            return True
        except FloodWait as e:
            print(f"FloodWait: Retry {retry_count+1}/{MAX_RETRIES} after {e.timeout}s")
            await asyncio.sleep(e.timeout)
            retry_count += 1
        except Exception as e:
            print(f"Error sending media: {e}")
            break
    return False

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    """معالجة الوسائط مع التحكم في التدفق"""
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
        # تنزيل وتجميع الوسائط
        for media in tweet_media['media_extended']:
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)
            
            if media_type == 'image':
                album_accumulator[key]["image"].append((file_name, media_type, tweet_dir))
            elif media_type in ['video', 'gif']:
                album_accumulator[key]["video"].append((file_name, media_type, tweet_dir))

        # معالجة الصور
        while len(album_accumulator[key]["image"]) >= 5:
            media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
            batch = album_accumulator[key]["image"][:5]
            
            for file_path, _, _ in batch:
                media_group.add_photo(media=FSInputFile(file_path))
                
            if not await send_media_group_with_retry(message, media_group.build(), business_id):
                raise Exception("Failed to send media group after retries")
            
            # تنظيف الملفات المرسلة
            for file_path, _, dir_path in batch:
                if os.path.exists(file_path): os.remove(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path): os.rmdir(dir_path)
                
            album_accumulator[key]["image"] = album_accumulator[key]["image"][5:]

        # معالجة الفيديوهات
        while len(album_accumulator[key]["video"]) >= 5:
            media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
            batch = album_accumulator[key]["video"][:5]
            
            for file_path, _, _ in batch:
                media_group.add_video(media=FSInputFile(file_path))
                
            if not await send_media_group_with_retry(message, media_group.build(), business_id):
                raise Exception("Failed to send media group after retries")
            
            # تنظيف الملفات المرسلة
            for file_path, _, dir_path in batch:
                if os.path.exists(file_path): os.remove(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path): os.rmdir(dir_path)
                
            album_accumulator[key]["video"] = album_accumulator[key]["video"][5:]

    except Exception as e:
        print(f"Error in reply_media: {e}")
        if business_id is None:
            await message.react([types.ReactionTypeEmoji(emoji="👎")])
        await message.answer("حدث خطأ أثناء المعالجة ⚠️")

async def process_chat_queue(chat_id):
    """معالجة قوائم الانتظار مع التحكم في التدفق"""
    while True:
        message = await chat_queues[chat_id].get()
        try:
            business_id = message.business_connection_id
            bot_url = f"t.me/{(await bot.get_me()).username}"
            
            if tweet_ids := extract_tweet_ids(message.text):
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")
                
                for tweet_id in tweet_ids:
                    try:
                        media = await scrape_media_with_retry(tweet_id)
                        await reply_media(message, tweet_id, media, bot_url, business_id)
                        await asyncio.sleep(3)  # تأخير بين التغريدات
                    except Exception as e:
                        print(f"Error processing tweet {tweet_id}: {e}")
                
                await asyncio.sleep(2)  # تأخير نهائي
                try:
                    await message.delete()
                except Exception as e:
                    print(f"Error deleting message: {e}")
            else:
                if business_id is None:
                    await message.react([types.ReactionTypeEmoji(emoji="👎")])
                await message.answer("لم يتم العثور على روابط صالحة ❌")
                
        finally:
            chat_queues[chat_id].task_done()
            await asyncio.sleep(1)  # تأخير بين الرسائل

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
    """إدارة قوائم الانتظار"""
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
    await chat_queues[chat_id].put(message)

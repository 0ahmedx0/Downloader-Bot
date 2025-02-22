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
MAX_TWEETS_AT_ONCE = 5  # 🔄 عدد التغريدات التي يتم معالجتها في نفس الوقت
MESSAGE_DELAY = 1  # ⏳ تأخير استقبال كل رسالة (1 ثانية)
TWEET_DELAY = 3  # ⏳ تأخير بين معالجة كل تغريدة (3 ثوانٍ)
RETRY_ATTEMPTS = 3  # 🔄 عدد محاولات إعادة الإرسال عند الحظر

router = Router()
album_accumulator = {}  # تخزين الصور والفيديوهات بشكل منفصل
chat_queues = {}  # قوائم الانتظار لكل دردشة
chat_workers = {}  # مهام المعالجة لكل دردشة

def extract_tweet_ids(text):
    """استخراج معرفات التغريدات من النص."""
    unshortened_links = ''
    for link in re.findall(r't\.co\/[a-zA-Z0-9]+', text):
        try:
            unshortened_link = requests.get('https://' + link).url
            unshortened_links += '\n' + unshortened_link
        except:
            pass

    tweet_ids = re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)
    return list(dict.fromkeys(tweet_ids)) if tweet_ids else None

def scrape_media(tweet_id):
    """جلب بيانات التغريدة من vxtwitter API."""
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            raise Exception(f'API returned error: {html.unescape(match.group(1))}')
        raise

async def download_media(media_url, file_path):
    """تنزيل الوسائط من التغريدة."""
    response = requests.get(media_url, stream=True)
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    """معالجة الوسائط وإرسالها إلى تيليجرام."""
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
            album_accumulator[key][media_type if media_type in ["image", "video"] else "video"].append((file_name, media_type, tweet_dir))

        await asyncio.sleep(3)  # ⏳ تأخير بين كل تغريدة وتغريدة

        for media_type in ["image", "video"]:
            if len(album_accumulator[key][media_type]) >= 5:
                album_to_send = album_accumulator[key][media_type][:5]
                media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
                for file_path, _, _ in album_to_send:
                    if media_type == "image":
                        media_group.add_photo(media=FSInputFile(file_path))
                    else:
                        media_group.add_video(media=FSInputFile(file_path))

                retry_attempts = 0
                while retry_attempts < RETRY_ATTEMPTS:
                    try:
                        await message.answer_media_group(media_group.build())
                        break
                    except FloodWait as e:
                        print(f"FloodWait: الانتظار لمدة {e.timeout} ثانية")
                        await asyncio.sleep(e.timeout)
                        retry_attempts += 1
                
                album_accumulator[key][media_type] = album_accumulator[key][media_type][5:]

                for file_path, _, dir_path in album_to_send:
                    os.remove(file_path) if os.path.exists(file_path) else None
                    os.rmdir(dir_path) if os.path.exists(dir_path) and not os.listdir(dir_path) else None

    except Exception as e:
        print(e)
        if business_id is None:
            await message.react([types.ReactionTypeEmoji(emoji="👎")])
        await message.reply("Something went wrong :(\nPlease try again later.")

async def process_chat_queue(chat_id):
    """معالجة الرسائل في قائمة الانتظار."""
    while True:
        message = await chat_queues[chat_id].get()
        try:
            business_id = message.business_connection_id
            bot_url = f"t.me/{(await bot.get_me()).username}"
            tweet_ids = extract_tweet_ids(message.text)

            if tweet_ids:
                for i in range(0, len(tweet_ids), MAX_TWEETS_AT_ONCE):
                    batch = tweet_ids[i:i + MAX_TWEETS_AT_ONCE]
                    for tweet_id in batch:
                        media = scrape_media(tweet_id)
                        await reply_media(message, tweet_id, media, bot_url, business_id)
                        await asyncio.sleep(TWEET_DELAY)
                await message.delete()
            else:
                await message.answer("No tweet IDs found.")
        finally:
            chat_queues[chat_id].task_done()
        await asyncio.sleep(MESSAGE_DELAY)  # ⏳ تأخير بين استقبال الرسائل

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
    """إضافة الرسالة إلى قائمة الانتظار."""
    chat_id = message.chat.id
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
    await chat_queues[chat_id].put(message)

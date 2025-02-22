import asyncio
import html
import os
import re
from urllib.parse import urlsplit

import requests
from aiogram import types, Router, F
from aiogram.types import FSInputFile
from aiogram.utils.media_group import MediaGroupBuilder

import messages as bm
from config import OUTPUT_DIR, CHANNEL_IDtwiter
from main import bot, db, send_analytics

MAX_FILE_SIZE = 500 * 1024 * 1024
MAX_CONCURRENT_TWEETS = 3  # ✅ عدد التغريدات التي تُعالج في نفس الوقت
PROCESSING_DELAY = 5  # ✅ تأخير بين كل تغريدة لمنع الفلود

router = Router()
album_accumulator_photos = {}
album_accumulator_videos = {}

tweet_queue = asyncio.Queue()  # ✅ قائمة انتظار للتغريدات


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


async def process_tweets():
    """معالجة التغريدات تدريجيًا من قائمة الانتظار."""
    while True:
        message, tweet_id, bot_url, business_id = await tweet_queue.get()
        try:
            media = scrape_media(tweet_id)
            await reply_media(message, tweet_id, media, bot_url, business_id)
        except Exception as e:
            print(f"❌ خطأ أثناء معالجة التغريدة {tweet_id}: {e}")

        await asyncio.sleep(PROCESSING_DELAY)  # ✅ تأخير بين كل تغريدة لتقليل الفلود


def scrape_media(tweet_id):
    """استخراج الوسائط من التغريدة."""
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            raise Exception(f'API returned error: {html.unescape(match.group(1))}')
        raise


async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    """تنزيل الوسائط وإرسالها للمستخدم."""
    chat_id = message.chat.id
    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media["text"]
    user_captions = await db.get_user_captions(message.from_user.id)

    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)

    downloaded_photos = []
    downloaded_videos = []

    try:
        for media in tweet_media['media_extended']:
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)

            if media_type == 'image':
                downloaded_photos.append((file_name, media_type, tweet_dir))
            elif media_type in ['video', 'gif']:
                downloaded_videos.append((file_name, media_type, tweet_dir))

        if chat_id not in album_accumulator_photos:
            album_accumulator_photos[chat_id] = []
        if chat_id not in album_accumulator_videos:
            album_accumulator_videos[chat_id] = []

        album_accumulator_photos[chat_id].extend(downloaded_photos)
        album_accumulator_videos[chat_id].extend(downloaded_videos)

    except Exception as e:
        print(e)
        if business_id is None:
            await message.react([types.ReactionTypeEmoji(emoji="👎")])
        await message.reply("حدث خطأ، الرجاء المحاولة لاحقًا.")


async def download_media(media_url, file_path):
    """تنزيل الوسائط وحفظها محليًا."""
    response = requests.get(media_url, stream=True)
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)


@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
    """إضافة التغريدات إلى قائمة الانتظار ومعالجتها تدريجيًا."""
    business_id = message.business_connection_id

    if business_id is None:
        await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])

    bot_url = f"t.me/{(await bot.get_me()).username}"

    tweet_ids = extract_tweet_ids(message.text)
    if tweet_ids:
        if business_id is None:
            await bot.send_chat_action(message.chat.id, "typing")

        for tweet_id in tweet_ids:
            await tweet_queue.put((message, tweet_id, bot_url, business_id))  # ✅ إضافة التغريدة إلى قائمة الانتظار

        # لا نحذف الرسائل مباشرة بل نتركها حتى تتم معالجتها بالكامل

    else:
        if business_id is None:
            await message.react([types.ReactionTypeEmoji(emoji="👎")])
        await message.answer("لم يتم العثور على معرفات تغريدات صالحة.")


async def start_tweet_processor():
    """تشغيل معالج التغريدات في الخلفية."""
    for _ in range(MAX_CONCURRENT_TWEETS):  # ✅ معالجة 3 تغريدات فقط في نفس الوقت
        asyncio.create_task(process_tweets())

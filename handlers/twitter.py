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

router = Router()
album_accumulator_photos = {}
album_accumulator_videos = {}

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
    """استخراج الوسائط من التغريدة باستخدام API خارجي."""
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            raise Exception(f'API returned error: {html.unescape(match.group(1))}')
        raise

async def download_media(media_url, file_path):
    """تنزيل الوسائط من الإنترنت وحفظها محليًا."""
    response = requests.get(media_url, stream=True)
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

async def send_album_safe(chat_id, album, album_type, user_captions, post_caption, bot_url):
    """إرسال ألبوم مع التعامل مع مشكلة الحظر."""
    media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
    for file_path, media_type, _ in album:
        if album_type == "photo":
            media_group.add_photo(media=FSInputFile(file_path))
        elif album_type == "video":
            media_group.add_video(media=FSInputFile(file_path))

    retry_attempts = 3  # عدد المحاولات عند الحظر
    for attempt in range(retry_attempts):
        try:
            sent_messages = await bot.send_media_group(chat_id=chat_id, media=media_group.build())
            return sent_messages
        except Exception as e:
            error_msg = str(e)
            if "Too Many Requests" in error_msg:
                retry_after = int(re.search(r"retry after (\d+)", error_msg).group(1))
                print(f"📛 Flood control exceeded! Retrying after {retry_after} seconds...")
                await asyncio.sleep(retry_after)
            else:
                print(f"❌ Error sending media group: {error_msg}")
                break

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    """تجميع الصور والفيديوهات في ألبومات منفصلة وإرسالها بطريقة منظمة."""
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

        async def send_if_ready(album_dict, album_type):
            if len(album_dict[chat_id]) >= 5:  # ✅ إرسال كل 5 ملفات فقط في كل دفعة
                await send_album_safe(chat_id, album_dict[chat_id][:5], album_type, user_captions, post_caption, bot_url)
                album_dict[chat_id] = album_dict[chat_id][5:]  # الاحتفاظ بالملفات المتبقية
                await asyncio.sleep(5)  # ✅ إضافة تأخير لتجنب الحظر

        await send_if_ready(album_accumulator_photos, "photo")
        await send_if_ready(album_accumulator_videos, "video")

    except Exception as e:
        print(e)
        if business_id is None:
            await message.react([types.ReactionTypeEmoji(emoji="👎")])
        await message.reply("Something went wrong :(\nPlease try again later.")

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
    """التعامل مع روابط التغريدات وتنزيل الوسائط."""
    business_id = message.business_connection_id

    if business_id is None:
        await message.react([types.ReactionTypeEmoji(emoji="👨‍💻")])

    bot_url = f"t.me/{(await bot.get_me()).username}"

    tweet_ids = extract_tweet_ids(message.text)
    if tweet_ids:
        if business_id is None:
            await bot.send_chat_action(message.chat.id, "typing")

        for tweet_id in tweet_ids:
            media = scrape_media(tweet_id)
            await reply_media(message, tweet_id, media, bot_url, business_id)

        await asyncio.sleep(2)  # ✅ تأخير بسيط قبل حذف الرسالة
        try:
            await message.delete()
        except Exception as delete_error:
            print(f"Error deleting message: {delete_error}")
    else:
        if business_id is None:
            await message.react([types.ReactionTypeEmoji(emoji="👎")])
        await message.answer("No tweet IDs found.")

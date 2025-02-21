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
album_accumulator = {}
chat_queues = {}  # قاموس لحفظ قوائم الانتظار لكل دردشة
chat_workers = {}  # قاموس لحفظ مهام المعالجة لكل دردشة

def extract_tweet_ids(text):
    """Extract tweet IDs from message text."""
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
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        if match := re.search(r'<meta content="(.*?)" property="og:description" />', r.text):
            raise Exception(f'API returned error: {html.unescape(match.group(1))}')
        raise

async def download_media(media_url, file_path):
    response = requests.get(media_url, stream=True)
    response.raise_for_status()
    with open(file_path, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    """معالجة الوسائط مع استخدام قائمة الانتظار"""
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")

    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media["text"]
    user_captions = await db.get_user_captions(message.from_user.id)

    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)

    downloaded_files = []

    try:
        for media in tweet_media['media_extended']:
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)
            if media_type in ['image', 'video', 'gif']:
                downloaded_files.append((file_name, media_type, tweet_dir))

        key = message.chat.id
        if key not in album_accumulator:
            album_accumulator[key] = []
        album_accumulator[key].extend(downloaded_files)

        # إرسال الألبوم عند تجميع 5 وسائط
        if len(album_accumulator[key]) >= 5:
            album_to_send = album_accumulator[key][:5]
            media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
            for file_path, media_type, _ in album_to_send:
                if media_type == 'image':
                    media_group.add_photo(media=FSInputFile(file_path))
                elif media_type in ['video', 'gif']:
                    media_group.add_video(media=FSInputFile(file_path))
            
            sent_messages = await message.answer_media_group(media_group.build())

            channel_media = []
            for msg in sent_messages:
                if msg.photo:
                    file_id = msg.photo[-1].file_id
                    channel_media.append(types.InputMediaPhoto(media=file_id))
                elif msg.video:
                    file_id = msg.video.file_id
                    channel_media.append(types.InputMediaVideo(media=file_id))

            # إزالة أول 5 وسائط من المُجمّع
            album_accumulator[key] = album_accumulator[key][5:]

            #await asyncio.sleep(10)
            #await bot.send_media_group(chat_id=CHANNEL_IDtwiter, media=channel_media)

            for file_path, _, dir_path in album_to_send:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)

    except Exception as e:
        print(e)
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        await message.reply("Something went wrong :(\nPlease try again later.")

async def process_chat_queue(chat_id):
    """معالجة الرسائل في قائمة الانتظار بالتتابع"""
    while True:
        message = await chat_queues[chat_id].get()
        try:
            business_id = message.business_connection_id

            if business_id is None:
                react = types.ReactionTypeEmoji(emoji="👨‍💻")
                await message.react([react])

            bot_url = f"t.me/{(await bot.get_me()).username}"

            tweet_ids = extract_tweet_ids(message.text)
            if tweet_ids:
                if business_id is None:
                    await bot.send_chat_action(message.chat.id, "typing")

                for tweet_id in tweet_ids:
                    media = scrape_media(tweet_id)
                    await reply_media(message, tweet_id, media, bot_url, business_id)
                
                await asyncio.sleep(2)
                try:
                    await message.delete()
                except Exception as delete_error:
                    print(f"Error deleting message: {delete_error}")
            else:
                if business_id is None:
                    react = types.ReactionTypeEmoji(emoji="👎")
                    await message.react([react])
                await message.answer("No tweet IDs found.")

        finally:
            chat_queues[chat_id].task_done()

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
    """إضافة الرسالة إلى قائمة الانتظار الخاصة بالدردشة"""
    chat_id = message.chat.id
    
    # إنشاء قائمة انتظار وعامل معالجة إذا لم يكن موجود
    if chat_id not in chat_queues:
        chat_queues[chat_id] = asyncio.Queue()
        chat_workers[chat_id] = asyncio.create_task(process_chat_queue(chat_id))
    
    # إضافة الرسالة إلى قائمة الانتظار
    await chat_queues[chat_id].put(message)

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
from config import OUTPUT_DIR
from main import bot, db, send_analytics

MAX_FILE_SIZE = 500 * 1024 * 1024

router = Router()
album_accumulator = {}

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
    """Reply to message with supported media as album when reaching 10 items."""
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")

    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = tweet_media["text"]
    user_captions = await db.get_user_captions(message.from_user.id)

    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)

    downloaded_files = []  # قائمة لتجميع الملفات التي تم تنزيلها

    try:
        for media in tweet_media['media_extended']:
            media_url = media['url']
            media_type = media['type']
            file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
            await download_media(media_url, file_name)
            # سنضيف جميع أنواع الوسائط (صور وفيديوهات) إلى القائمة
            if media_type in ['image', 'video', 'gif']:
                downloaded_files.append((file_name, media_type))

        # نجمع الملفات المُحمَّلة في المُجمِّع العالمي باستخدام معرف الدردشة كمفتاح
        key = message.chat.id
        if key not in album_accumulator:
            album_accumulator[key] = []
        album_accumulator[key].extend(downloaded_files)

        # نتحقق من عدد الوسائط المُجمَّعة؛ إذا وصلت إلى 10 أو أكثر نقوم بإرسال ألبوم
        if len(album_accumulator[key]) >= 10:
            media_group = MediaGroupBuilder(caption=bm.captions(user_captions, post_caption, bot_url))
            # نأخذ أول 10 عناصر فقط
            for file_path, media_type in album_accumulator[key][:10]:
                if media_type == 'image':
                    media_group.add_photo(media=FSInputFile(file_path))
                elif media_type in ['video', 'gif']:
                    media_group.add_video(media=FSInputFile(file_path))
            await message.answer_media_group(media_group.build())
            # بعد الإرسال، نحذف العناصر المرسلة من المُجمِّع
            album_accumulator[key] = album_accumulator[key][10:]

        # حذف الملفات من المجلد بعد التنزيل (يمكنك نقل هذه العملية إلى مرحلة لاحقة إذا رغبت بالحفاظ على الملفات مؤقتًا)
        await asyncio.sleep(5)
        for root, dirs, files in os.walk(tweet_dir):
            for file in files:
                os.remove(os.path.join(root, file))
        os.rmdir(tweet_dir)

    except Exception as e:
        print(e)
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        await message.reply("Something went wrong :(\nPlease try again later.")


@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)"))
async def handle_tweet_links(message):
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
    else:
        if business_id is None:
            react = types.ReactionTypeEmoji(emoji="👎")
            await message.react([react])
        await message.answer("No tweet IDs found.")

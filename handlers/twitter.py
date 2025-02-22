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
album_accumulator = {"images": {}, "videos": {}}

def extract_tweet_ids(text):
    """استخراج معرفات التغريدات من النص"""
    unshortened_links = ''
    for link in re.findall(r't\.co\/[a-zA-Z0-9]+', text):
        try:
            unshortened_link = requests.get('https://' + link).url
            unshortened_links += '\n' + unshortened_link
        except:
            pass
    return list(dict.fromkeys(re.findall(r"(?:twitter|x)\.com/.{1,15}/(?:web|status(?:es)?)/([0-9]{1,20})", text + unshortened_links)))

def scrape_media(tweet_id):
    """استخراج الوسائط من التغريدة عبر API"""
    r = requests.get(f'https://api.vxtwitter.com/Twitter/status/{tweet_id}')
    r.raise_for_status()
    return r.json()

def add_to_album(album_type, chat_id, file_path, media_type, tweet_dir):
    """إضافة وسائط إلى الألبوم المناسب"""
    if chat_id not in album_accumulator[album_type]:
        album_accumulator[album_type][chat_id] = []
    album_accumulator[album_type][chat_id].append((file_path, media_type, tweet_dir))

async def send_album(album_type, chat_id, caption):
    """إرسال ألبوم عند اكتماله"""
    media_group = MediaGroupBuilder(caption=caption)
    album_to_send = album_accumulator[album_type][chat_id][:10]
    
    for file_path, media_type, _ in album_to_send:
        if media_type == 'image':
            media_group.add_photo(media=FSInputFile(file_path))
        elif media_type in ['video', 'gif']:
            media_group.add_video(media=FSInputFile(file_path))
    
    sent_messages = await bot.send_media_group(chat_id=chat_id, media=media_group.build())
    album_accumulator[album_type][chat_id] = album_accumulator[album_type][chat_id][10:]
    
    for file_path, _, dir_path in album_to_send:
        if os.path.exists(file_path):
            os.remove(file_path)
        if os.path.exists(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)

async def reply_media(message, tweet_id, tweet_media, bot_url, business_id):
    """إضافة الوسائط إلى الألبوم المناسب وإرساله عند اكتماله"""
    await send_analytics(user_id=message.from_user.id, chat_type=message.chat.type, action_name="twitter")
    
    tweet_dir = f"{OUTPUT_DIR}/{tweet_id}"
    post_caption = "فيديو" if "video" in [m["type"] for m in tweet_media["media_extended"]] else "حصريات"
    user_captions = await db.get_user_captions(message.from_user.id)
    
    if not os.path.exists(tweet_dir):
        os.makedirs(tweet_dir)
    
    for media in tweet_media['media_extended']:
        media_url = media['url']
        media_type = media['type']
        file_name = os.path.join(tweet_dir, os.path.basename(urlsplit(media_url).path))
        await download_media(media_url, file_name)
        
        if media_type in ['image', 'video', 'gif']:
            album_type = "videos" if media_type in ['video', 'gif'] else "images"
            add_to_album(album_type, message.chat.id, file_name, media_type, tweet_dir)
    
    if len(album_accumulator["videos"].get(message.chat.id, [])) >= 10:
        await send_album("videos", message.chat.id, "فيديو")
    if len(album_accumulator["images"].get(message.chat.id, [])) >= 10:
        await send_album("images", message.chat.id, "حصريات")

@router.message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)")
@router.business_message(F.text.regexp(r"(https?://(www\.)?(twitter|x)\.com/\S+|https?://t\.co/\S+)")
async def handle_tweet_links(message):
    """معالجة روابط التغريدات وتنزيل الوسائط"""
    bot_url = f"t.me/{(await bot.get_me()).username}"
    tweet_ids = extract_tweet_ids(message.text)
    
    if tweet_ids:
        await bot.send_chat_action(message.chat.id, "typing")
        for tweet_id in tweet_ids:
            media = scrape_media(tweet_id)
            await reply_media(message, tweet_id, media, bot_url, None)
        await asyncio.sleep(2)
        try:
            await message.delete()
        except:
            pass
    else:
        await message.answer("No tweet IDs found.")

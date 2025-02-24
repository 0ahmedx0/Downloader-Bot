import os
import asyncio
import logging
from tempfile import NamedTemporaryFile
from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputMediaVideo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError
from pymediainfo import MediaInfo
from moviepy.editor import VideoFileClip

# إصلاح مشاكل الصوت وملفات النظام
os.environ["XDG_RUNTIME_DIR"] = "/tmp"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["AUDIODEV"] = "null"

# تهيئة القناة
raw_channel_id = os.getenv("CHANNEL_ID")
CHANNEL_ID = raw_channel_id if raw_channel_id.startswith("@") else int(raw_channel_id) if raw_channel_id else None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

MESSAGES = {
    "greeting": "Hello {username}! Send me videos up to 2GB to process and create albums.",
    "help": "Forward/send videos, then click 'Create Album' to process and send them in groups of 10.",
    "keyboard_done": "Create Album",
    "keyboard_clear": "Reset Album",
    "queue_cleared": "Queue cleared! Start fresh.",
    "processing_started": "Processing {count} videos with 3 threads...",
    "album_sent": "Album {index} sent successfully!",
    "file_too_big": "⚠️ File too large! Maximum allowed size is 2GB.",
}

# إنشاء مجلد مؤقت خاص
TEMP_DIR = "/tmp/telegram_bot"
os.makedirs(TEMP_DIR, exist_ok=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    await update.message.reply_text(
        MESSAGES["greeting"].format(username=update.effective_user.username),
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def get_video_info(file_path):
    """استخراج معلومات الفيديو باستخدام pymediainfo"""
    try:
        media_info = MediaInfo.parse(file_path)
        data = media_info.to_data()
        
        video_track = next((t for t in data['tracks'] if t['track_type'] == 'Video'), None)
        general_track = next((t for t in data['tracks'] if t['track_type'] == 'General'), None)
        
        if not video_track or not general_track:
            return None
        
        # إنشاء الصورة المصغرة
        thumb_path = None
        try:
            with NamedTemporaryFile(suffix=".jpg", delete=False, dir=TEMP_DIR) as thumb_file:
                clip = VideoFileClip(file_path)
                clip.save_frame(thumb_file.name, t=0.5)  # استخدام الإطار عند 0.5 ثانية
                thumb_path = thumb_file.name
        except Exception as e:
            logger.warning("Thumbnail creation failed: %s", e)
        
        return {
            "duration": int(general_track.get('duration', 0)) // 1000,
            "width": int(video_track.get('width', 0)),
            "height": int(video_track.get('height', 0)),
            "thumb_path": thumb_path,
        }
    except Exception as e:
        logger.error("MediaInfo error: %s", e)
        return None

async def sendvideo(message):
    """معالجة الفيديو وإرجاع البيانات المطلوبة"""
    try:
        # التحقق من حجم الملف
        if message.video.file_size > 2 * 1024 * 1024 * 1024:
            await message.reply_text(MESSAGES["file_too_big"])
            return None
        
        # تنزيل الفيديو إلى مجلد مؤقت
        file = await message.video.get_file()
        file_name = f"{message.video.file_unique_id}.mp4"
        temp_path = os.path.join(TEMP_DIR, file_name)
        
        await file.download_to_drive(temp_path)
        
        # استخراج المعلومات
        video_info = await get_video_info(temp_path)
        if not video_info:
            os.remove(temp_path)
            return None
        
        return {
            "type": "video",
            "media": temp_path,
            "thumb": video_info["thumb_path"],
            "duration": video_info["duration"],
            "width": video_info["width"],
            "height": video_info["height"],
            "caption": f"Processed: {os.path.basename(temp_path)}"
        }
    except TelegramError as e:
        logger.error("Telegram error: %s", e)
        return None
    except Exception as e:
        logger.error("Video processing failed: %s", e)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return None

async def add_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    context.user_data["media_queue"].append(update.message)
    logger.info("Added media to queue: %s", update.message.message_id)

async def process_media_task(sem, message, processed_media):
    async with sem:
        try:
            media_info = await sendvideo(message)
            if media_info:
                processed_media.append(media_info)
        except Exception as e:
            logger.error("Error processing media: %s", e)

async def create_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    queue = context.user_data.get("media_queue", [])
    if len(queue) < 2:
        await update.message.reply_text("Need at least 2 videos!")
        return

    await update.message.reply_text(MESSAGES["processing_started"].format(count=len(queue)))

    sem = asyncio.Semaphore(3)
    processed_media = []
    tasks = []
    
    for msg in queue:
        task = asyncio.create_task(process_media_task(sem, msg, processed_media))
        task.add_done_callback(lambda t: logger.info("Task completed"))
        tasks.append(task)
    
    await asyncio.gather(*tasks, return_exceptions=True)
    processed_media = [m for m in processed_media if m is not None]

    if not processed_media:
        await update.message.reply_text("⚠️ No videos were processed successfully!")
        return

    chunks = [processed_media[i:i+10] for i in range(0, len(processed_media), 10)]

    for i, chunk in enumerate(chunks, 1):
        input_media = []
        valid_files = []
        for item in chunk:
            if os.path.exists(item["media"]):
                valid_files.append(item)
                input_media.append(
                    InputMediaVideo(
                        media=open(item["media"], "rb"),
                        thumb=open(item["thumb"], "rb") if item["thumb"] and os.path.exists(item["thumb"]) else None,
                        caption=item["caption"],
                        duration=item["duration"],
                        width=item["width"],
                        height=item["height"]
                    )
                )
            else:
                logger.error("File not found: %s", item["media"])
        
        if not input_media:
            await update.message.reply_text(f"⚠️ Album {i} is empty due to processing errors")
            continue
        
        try:
            await context.bot.send_media_group(chat_id=CHANNEL_ID, media=input_media)
            await update.message.reply_text(MESSAGES["album_sent"].format(index=i))
        except Exception as e:
            logger.error("Album send error: %s", e)
            await update.message.reply_text(f"⚠️ Error sending album {i}: {str(e)}")
        
        # حذف الملفات بعد الإرسال
        for item in valid_files:
            try:
                if os.path.exists(item["media"]):
                    os.remove(item["media"])
                if item["thumb"] and os.path.exists(item["thumb"]):
                    os.remove(item["thumb"])
            except Exception as e:
                logger.error("Cleanup error: %s", e)
        
        await asyncio.sleep(10)

    context.user_data["media_queue"] = []
    await update.message.reply_text("All albums processed successfully!")

async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set")
        return

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.VIDEO, add_media))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$"), create_album))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$"), reset_album))
    application.add_error_handler(error_handler)

    application.run_polling()

if __name__ == "__main__":
    main()

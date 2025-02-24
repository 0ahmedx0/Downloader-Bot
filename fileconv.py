import os
import asyncio
import logging
import subprocess
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
from telegram.error import RetryAfter

# تهيئة القناة
raw_channel_id = os.getenv("CHANNEL_ID")
CHANNEL_ID = raw_channel_id if raw_channel_id.startswith("@") else int(raw_channel_id) if raw_channel_id else None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

MESSAGES = {
    "greeting": "Hello {username}! Send me videos to process and create albums.",
    "help": "Forward/send videos, then click 'Create Album' to process and send them in groups of 10.",
    "keyboard_done": "Create Album",
    "keyboard_clear": "Reset Album",
    "queue_cleared": "Queue cleared! Start fresh.",
    "processing_started": "Processing {count} videos with 3 threads...",
    "album_sent": "Album {index} sent successfully!",
    "ffmpeg_required": "⚠️ Error: FFmpeg is required but not found. Install it from https://ffmpeg.org/"
}

async def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except FileNotFoundError:
        return False

async def get_video_info(file_path):
    """استخراج معلومات الفيديو باستخدام FFmpeg"""
    try:
        # الحصول على البيانات الأساسية
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        info = json.loads(result.stdout)
        
        # استخراج التفاصيل
        stream = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
        duration = int(float(info["format"]["duration"]))
        width = int(stream["width"])
        height = int(stream["height"])
        
        # إنشاء الصورة المصغرة
        with NamedTemporaryFile(suffix=".jpg", delete=False) as thumb_file:
            thumb_path = thumb_file.name
            subprocess.run([
                "ffmpeg",
                "-i", file_path,
                "-ss", "00:00:01",  # أخذ الإطار الأول بعد الثانية الأولى
                "-vframes", "1",
                "-q:v", "2",
                thumb_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        return {
            "duration": duration,
            "width": width,
            "height": height,
            "thumb_path": thumb_path,
        }
    except Exception as e:
        logger.error("Error processing video info: %s", e)
        return None

async def sendvideo(message):
    """معالجة الفيديو وإرجاع البيانات المطلوبة"""
    try:
        # تنزيل الفيديو
        file = await message.video.get_file()
        with NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
            await file.download_to_drive(temp_file.name)
            file_path = temp_file.name
        
        # استخراج المعلومات
        video_info = await get_video_info(file_path)
        if not video_info:
            os.remove(file_path)
            return None
        
        return {
            "type": "video",
            "media": file_path,
            "thumb": video_info["thumb_path"],
            "duration": video_info["duration"],
            "width": video_info["width"],
            "height": video_info["height"],
            "caption": f"Processed: {os.path.basename(file_path)}"
        }
    except Exception as e:
        logger.error("Video processing failed: %s", e)
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_ffmpeg():
        await update.message.reply_text(MESSAGES["ffmpeg_required"])
        return
    
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    await update.message.reply_text(
        MESSAGES["greeting"].format(username=update.effective_user.username),
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

async def add_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_ffmpeg():
        await update.message.reply_text(MESSAGES["ffmpeg_required"])
        return
    
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
    if not await check_ffmpeg():
        await update.message.reply_text(MESSAGES["ffmpeg_required"])
        return
    
    queue = context.user_data.get("media_queue", [])
    if len(queue) < 2:
        await update.message.reply_text("Need at least 2 videos!")
        return

    await update.message.reply_text(MESSAGES["processing_started"].format(count=len(queue)))

    # معالجة الفيديوهات بثلاثة خيوط
    sem = asyncio.Semaphore(3)
    processed_media = []
    tasks = [process_media_task(sem, msg, processed_media) for msg in queue]
    await asyncio.gather(*tasks)

    # تقسيم إلى مجموعات كل 10
    chunks = [processed_media[i:i+10] for i in range(0, len(processed_media), 10)]

    for i, chunk in enumerate(chunks, 1):
        input_media = []
        for item in chunk:
            input_media.append(
                InputMediaVideo(
                    media=open(item["media"], "rb"),
                    thumb=open(item["thumb"], "rb") if item["thumb"] else None,
                    caption=item["caption"],
                    duration=item["duration"],
                    width=item["width"],
                    height=item["height"]
                )
            )
        
        # إرسال الألبوم إلى القناة
        await context.bot.send_media_group(chat_id=CHANNEL_ID, media=input_media)
        await update.message.reply_text(MESSAGES["album_sent"].format(index=i))
        
        # حذف الملفات المؤقتة
        for item in chunk:
            try:
                os.remove(item["media"])
                if item["thumb"] and os.path.exists(item["thumb"]):
                    os.remove(item["thumb"])
            except Exception as e:
                logger.error("Cleanup error: %s", e)
        
        # تأخير 10 ثوانٍ بين الألبومات
        await asyncio.sleep(10)

    context.user_data["media_queue"] = []
    await update.message.reply_text("All albums processed successfully!")

async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

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

    application.run_polling()

if __name__ == "__main__":
    main()

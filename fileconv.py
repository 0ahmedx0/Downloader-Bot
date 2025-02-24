import os
import asyncio
import logging
import concurrent.futures
from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputMediaPhoto,
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

# تعيين معرف القناة من متغيرات البيئة
raw_channel_id = os.getenv("CHANNEL_ID")
if raw_channel_id:
    if raw_channel_id.startswith("@"):
        CHANNEL_ID = raw_channel_id
    else:
        try:
            CHANNEL_ID = int(raw_channel_id)
        except ValueError:
            CHANNEL_ID = raw_channel_id
else:
    CHANNEL_ID = None

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الرسائل المستخدمة في البوت
MESSAGES = {
    "greeting": (
        "Hello {username}! Send me photos or videos and I'll create Albums for you."
    ),
    "help": "Just forward or send me multiple media files.",
    "settings": "There are no settings to be made here",
    "source": "https://github.com/wjclub/telegram-bot-album-creator",
    "keyboard_done": "Create Album",
    "keyboard_clear": "Reset Album",
    "keyboard_done_video": "Create Video Album",
    "keyboard_clear_video": "Reset Video Album",
    "not_enough_media_items": "Sorry, but you must send me more than two media elements.",
    "queue_cleared": "Queue cleared. You can start anew.",
    "album_caption": "حصريات🌈"
}

# ===================================================================
# الوظائف الخاصة بالأوامر العامة والتعامل مع الصور (كما في الكود الأصلي)
# ===================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])],
        [KeyboardButton(MESSAGES["keyboard_done_video"])],
        [KeyboardButton(MESSAGES["keyboard_clear_video"])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(message, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["help"])

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["settings"])

async def source_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["source"])

async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    photo = update.message.photo[-1]
    file_id = photo.file_id
    context.user_data["media_queue"].append({"type": "photo", "media": file_id})
    logger.info("Added photo: %s", file_id)

# ===================================================================
# التعديلات الخاصة بالفيديوهات
# ===================================================================

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    عند استقبال فيديو من المستخدم، يتم تخزين رسالة الفيديو كاملة في قائمة الانتظار.
    """
    if "video_queue" not in context.user_data:
        context.user_data["video_queue"] = []
    context.user_data["video_queue"].append(update.message)
    logger.info("Added video message with id: %s", update.message.message_id)

def _sendvideo_sync(video_message):
    """
    دالة تزامنية لمعالجة الفيديو:
    - تنزيل الفيديو باستخدام الدالة down
    - الحصول على معلومات الفيديو باستخدام mediainfo.allinfo
    - رفع الفيديو بصيغة Stream باستخدام الدالة up
    تُعيد الدالة:
      - رسالة الفيديو المرسلة (التي تحتوي على معرف الملف بعد الرفع)
      - مسار الملف المؤقت
      - مسار الصورة المصغرة (إن وُجد)
    يُفترض أن تكون الدوال down و mediainfo.allinfo و up معرفة خارجيًا.
    """
    # تنزيل الفيديو
    file_path, downloaded_msg = down(video_message)  # يجب تعريف down
    # الحصول على معلومات الفيديو
    thumb, duration, width, height = mediainfo.allinfo(file_path)  # يجب تعريف mediainfo.allinfo
    # رفع الفيديو بصيغة Stream
    sent_message = up(
        video_message,
        file_path,
        downloaded_msg,
        video=True,
        capt=f'**{os.path.basename(file_path)}**',
        thumb=thumb,
        duration=duration,
        height=height,
        widht=width
    )  # يجب تعريف up
    # لا نقوم بحذف الملفات هنا؛ سيتم ذلك بعد إرسال الألبوم
    return sent_message, file_path, thumb

async def process_video_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    دالة لمعالجة فيديوهات قائمة الانتظار:
      - تُعالَج الفيديوهات باستخدام 3 خيوط كحد أقصى (ThreadPoolExecutor)
      - تُجمع النتائج في دفعات من 10 فيديوهات
      - بعد كل دفعة يتم إنشاء ألبوم وإرساله للقناة مع تأخير زمني 10 ثوانٍ
      - بعد إرسال الألبوم، يتم حذف الملفات المؤقتة
    """
    video_queue = context.user_data.get("video_queue", [])
    if not video_queue:
        await update.message.reply_text("لا توجد فيديوهات للمعالجة.")
        return
    
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
    loop = asyncio.get_event_loop()
    batch_tasks = []
    album_media = []
    temp_files = []   # لتخزين مسارات الملفات المؤقتة
    temp_thumbs = []  # لتخزين مسارات الصور المصغرة

    for idx, video_msg in enumerate(video_queue):
        # جدولة معالجة الفيديو على خيوط منفصلة
        task = loop.run_in_executor(executor, _sendvideo_sync, video_msg)
        batch_tasks.append(task)
        
        # عند اكتمال دفعة من 10 فيديوهات أو إذا كانت هذه هي الأخيرة
        if (idx + 1) % 10 == 0 or (idx + 1) == len(video_queue):
            results = await asyncio.gather(*batch_tasks)
            for i, res in enumerate(results):
                sent_message, file_path, thumb = res
                try:
                    video_file_id = sent_message.video.file_id
                except AttributeError:
                    logger.error("فشل استخراج معرف الفيديو من الرسالة.")
                    continue
                # في الدفعة الأولى نضيف التسمية مع الفيديو
                if not album_media:
                    album_media.append(InputMediaVideo(media=video_file_id, caption=MESSAGES["album_caption"]))
                else:
                    album_media.append(InputMediaVideo(media=video_file_id))
                temp_files.append(file_path)
                if thumb:
                    temp_thumbs.append(thumb)
            
            # إرسال الألبوم للقناة
            if CHANNEL_ID:
                try:
                    await context.bot.send_media_group(chat_id=CHANNEL_ID, media=album_media)
                    logger.info("تم إرسال دفعة من 10 فيديوهات كألبوم للقناة.")
                except Exception as e:
                    logger.error("حدث خطأ أثناء إرسال الألبوم: %s", e)
            else:
                logger.error("لم يتم تعيين معرف القناة.")
            
            # تأخير 10 ثوانٍ بين الدفعات
            await asyncio.sleep(10)
            batch_tasks = []
            album_media = []
    
    # تنظيف الملفات المؤقتة بعد انتهاء جميع الدفعات
    for file_path in temp_files:
        if os.path.exists(file_path):
            os.remove(file_path)
    for thumb in temp_thumbs:
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
    context.user_data["video_queue"] = []
    await update.message.reply_text("تم معالجة جميع الفيديوهات وإرسال الألبومات للقناة.")

async def reset_video_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    دالة لإعادة تعيين قائمة انتظار الفيديوهات.
    """
    context.user_data["video_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

# ===================================================================
# الدوال الأصلية الخاصة بإنشاء الألبومات (للصور والفيديوهات المخزنة في media_queue)
# ===================================================================

async def send_media_group_with_backoff(update: Update, context: ContextTypes.DEFAULT_TYPE, input_media, channel_id, chunk_index):
    max_retries = 5
    delay = 5  # بداية التأخير بـ 5 ثواني
    for attempt in range(max_retries):
        try:
            await update.message.reply_media_group(media=input_media)
            if channel_id:
                await context.bot.send_media_group(chat_id=channel_id, media=input_media)
            return True
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.", chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
            delay *= 2
        except Exception as e:
            logger.error("Error sending album chunk %d on attempt %d: %s", chunk_index + 1, attempt + 1, e)
            return False
    return False

async def create_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    
    if total_media < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return
    
    logger.info("Starting album conversion. Total media stored: %d", total_media)
    chunks = [media_queue[i: i + 10] for i in range(0, total_media, 10)]
    total_albums = len(chunks)
    processed_albums = 0
    channel_id = CHANNEL_ID
    delay_between_albums = 30
    
    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            if i == 0:
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"], caption=MESSAGES["album_caption"]))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"], caption=MESSAGES["album_caption"]))
            else:
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"]))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"]))
        success = await send_media_group_with_backoff(update, context, input_media, channel_id, index)
        if not success:
            logger.error("Failed to send album chunk %d after retries.", index + 1)
        processed_albums += 1
        remaining_albums = total_albums - processed_albums
        estimated_time_remaining = remaining_albums * delay_between_albums
        minutes, seconds = divmod(estimated_time_remaining, 60)
        progress_message = f"Progress: {processed_albums}/{total_albums} albums sent.\nEstimated time remaining: {minutes} minutes and {seconds} seconds."
        logger.info(progress_message)
        await asyncio.sleep(delay_between_albums)
    
    context.user_data["media_queue"] = []
    await update.message.reply_text("All albums have been sent successfully!")

async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

# ===================================================================
# الدالة الرئيسية وتشغيل البوت
# ===================================================================

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return
    application = Application.builder().token(token).build()
    
    # تسجيل أوامر البوت
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))
    application.add_handler(CommandHandler("create_video_album", process_video_queue))
    application.add_handler(CommandHandler("reset_video_album", reset_video_album))
    
    # معالجات الصور والفيديوهات
    application.add_handler(MessageHandler(filters.PHOTO, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO, add_video))
    
    # معالجات رسائل لوحة المفاتيح للألبومات والصور
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$"), create_album))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$"), reset_album))
    
    # معالجات رسائل لوحة المفاتيح الخاصة بالفيديوهات
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done_video']}$"), process_video_queue))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear_video']}$"), reset_video_album))
    
    # بدء البوت باستخدام long polling
    application.run_polling()

if __name__ == '__main__':
    main()

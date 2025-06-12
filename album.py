import os
import asyncio
import logging
import random
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

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الرسائل المستخدمة
MESSAGES = {
    "greeting": (
        "Hello {username}! Have you ever found some wonderful images on Telegram, "
        "which you would like to group them together, but don't want to down- and upload them all again? "
        "Let me do that for you real quick.\n\nSend me any photos or videos and I will create Albums out of them!\n\n"
    ),
    "help": (
        'Just forward or send me multiple photos and/or videos. Once you are done, press the "Create Album" Button '
        'and get all your previously sent files jammed together as albums. If you screwed up, click "Clear Album" to start again.\n\n'
        "This piece of shit is made by @wjclub."
    ),
    "settings": "There are no settings to be made here",
    "source": "https://github.com/wjclub/telegram-bot-album-creator",
    "keyboard_done": "Create Album",
    "keyboard_clear": "Reset Album",
    "not_enough_media_items": "Sorry, but you must send me more than two Media elements (Images or Videos) to create an Album.",
    "queue_cleared": "I forgot about all the photos and videos you sent me. You got a new chance.",
    "album_caption": "حصريات🌈"
}

# دالة التأخير العشوائي
prev_delay = None

def get_random_delay(min_delay=5, max_delay=30, min_diff=7):
    global prev_delay
    delay = random.randint(min_delay, max_delay)
    while prev_delay is not None and abs(delay - prev_delay) < min_diff:
        delay = random.randint(min_delay, max_delay)
    prev_delay = delay
    return delay

# تهيئة بيانات المستخدم
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE):
    """يضمن تهيئة context.user_data وقائمة الوسائط."""
    if context.user_data is None:
        context.user_data = {} # تأكد أن context.user_data هو قاموس
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []

# الأوامر الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context) # تهيئة بيانات المستخدم عند بدء البوت
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(message, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["help"])

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["settings"])

async def source_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["source"])

# إضافة الوسائط
async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context) # التأكد من تهيئة البيانات قبل الإضافة
    photo = update.message.photo[-1]
    file_id = photo.file_id
    context.user_data["media_queue"].append({"type": "photo", "media": file_id})
    logger.info("Added photo: %s", file_id)

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context) # التأكد من تهيئة البيانات قبل الإضافة
    video = update.message.video
    file_id = video.file_id
    context.user_data["media_queue"].append({"type": "video", "media": file_id})
    logger.info("Added video: %s", file_id)

# إرسال الوسائط مع التعامل مع فيضانات تيليجرام
async def send_media_group_with_backoff(update: Update, context: ContextTypes.DEFAULT_TYPE, input_media, chat_id, chunk_index):
    max_retries = 5
    # لا داعي لتعريف delay هنا إذا لم يتم استخدامها، استخدم retry_after مباشرة.
    for attempt in range(max_retries):
        try:
            await context.bot.send_media_group(chat_id=chat_id, media=input_media)
            return True
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.",
                           chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
            # لا تضاعف التأخير، فقط استخدم القيمة التي يطلبها التيليجرام
        except Exception as e:
            logger.error("Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            await update.message.reply_text("❌ حدث خطأ أثناء إرسال الألبوم. يرجى المحاولة لاحقاً.")
            return False
    return False

# إنشاء الألبوم
async def create_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context) # التأكد من تهيئة البيانات قبل الاستخدام
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)

    if total_media < 2:
        await update.message.reply_text("📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.")
        return

    logger.info("Starting album conversion. Total media stored: %d", total_media)

    chunks = [media_queue[i: i + 10] for i in range(0, total_media, 10)]
    total_albums = len(chunks)
    processed_albums = 0

    chat_id = update.effective_chat.id

    await update.message.reply_text("⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...")

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

        success = await send_media_group_with_backoff(update, context, input_media, chat_id, index)
        if not success:
            logger.error("Failed to send album chunk %d after retries.", index + 1)
            # يمكن هنا أن تختار إنهاء العملية أو الاستمرار
            await update.message.reply_text(f"⚠️ فشل إرسال جزء من الألبوم ({index + 1}/{total_albums}). سأحاول الاستمرار مع البقية.")
            continue # حاول الاستمرار مع الشريحة التالية حتى لو فشلت هذه

        processed_albums += 1
        remaining_albums = total_albums - processed_albums
        
        # تحسين حساب الوقت المتبقي
        # يمكنك تتبع متوسط الوقت المستغرق لكل ألبوم بدلاً من الافتراض 60 ثانية
        # ولكن كتقدير بسيط، نستخدم متوسط التأخير المتوقع + وقت إرسال المجموعة
        
        avg_delay_per_album = (get_random_delay(min_delay=5, max_delay=30, min_diff=7) + 5) # تقدير تقريبي
        estimated_time_remaining = remaining_albums * avg_delay_per_album
        
        minutes, seconds = divmod(int(estimated_time_remaining), 60) # حول لعدد صحيح
        
        time_remaining_str = f"{minutes} دقيقة و {seconds} ثانية" if minutes > 0 else f"{seconds} ثانية"

        # هذا الـ logger.info جيد، لكن قد ترغب في تحديث رسالة للمستخدم
        progress_message = (
            f"جاري إرسال الألبوم: {processed_albums}/{total_albums}\n"
            f"الوقت المتبقي المقدر: {time_remaining_str}."
        )
        logger.info(progress_message)
        
        # يمكنك إرسال تحديث للمستخدم بشكل دوري إذا كانت العملية طويلة جدًا
        # ولكن يجب توخي الحذر لتجنب رسائل كثيرة جدًا
        if processed_albums % 2 == 0 or processed_albums == total_albums: # تحديث كل ألبومين أو عند الانتهاء
             try:
                 await update.message.reply_text(progress_message)
             except Exception as e:
                 logger.error("Failed to send progress update: %s", e)


        delay_between_albums = get_random_delay()
        if index < len(chunks) - 1: # لا تؤخر بعد آخر مجموعة
            await asyncio.sleep(delay_between_albums)

    context.user_data["media_queue"] = []
    await update.message.reply_text("✅ تم إنشاء جميع الألبومات بنجاح!")


# إعادة ضبط قائمة الوسائط
async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context) # التأكد من تهيئة البيانات قبل الإعادة
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

# تشغيل البوت
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return

    application = Application.builder().token(token).build()

    # يجب أن تتأكد من أن user_data يتم تهيئته عند بدء التطبيق
    # في بعض إصدارات python-telegram-bot قد تكون context.user_data غير مهيأة لأول مرة
    # هنا لا تحتاج إلى تهيئة خاصة في main، لكن تأكد من وجودها في Handlers.

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$") & ~filters.COMMAND, create_album)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$") & ~filters.COMMAND, reset_album)
    )

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

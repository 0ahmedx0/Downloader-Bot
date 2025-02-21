import os
import asyncio
import logging
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
from telegram.error import RetryAfter  # استيراد الخطأ الخاص بالفيضانات

# تعيين معرف القناة في متغيرات البيئة
raw_channel_id = os.getenv("CHANNEL_ID")
if raw_channel_id:
    # إذا كانت القيمة تبدأ بـ '@' نستخدمها كنص
    if raw_channel_id.startswith("@"):
        CHANNEL_ID = raw_channel_id
    else:
        try:
            CHANNEL_ID = int(raw_channel_id)
        except ValueError:
            # إذا حدث خطأ في التحويل، نستخدم القيمة كنص
            CHANNEL_ID = raw_channel_id
else:
    CHANNEL_ID = None
    
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    photo = update.message.photo[-1]
    file_id = photo.file_id
    context.user_data["media_queue"].append({"type": "photo", "media": file_id})
    logger.info("Added photo: %s", file_id)


async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    video = update.message.video
    file_id = video.file_id
    context.user_data["media_queue"].append({"type": "video", "media": file_id})
    logger.info("Added video: %s", file_id)


async def send_media_group_with_backoff(update: Update, context: ContextTypes.DEFAULT_TYPE, input_media, channel_id, chunk_index):
    """
    تحاول إرسال مجموعة الوسائط باستخدام تقنية exponential backoff عند حدوث خطأ RetryAfter.
    """
    max_retries = 5
    delay = 5  # بداية التأخير بـ 5 ثواني
    for attempt in range(max_retries):
        try:
            # إرسال المجموعة للمستخدم
            await update.message.reply_media_group(media=input_media)
            # إرسال المجموعة إلى القناة لحفظها
            if channel_id:
                await context.bot.send_media_group(chat_id=channel_id, media=input_media)
            return True  # إذا نجحت العملية نخرج من الدالة
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.", chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
            delay *= 2  # زيادة التأخير بشكل أُسّي
        except Exception as e:
            logger.error("Error sending album chunk %d on attempt %d: %s", chunk_index + 1, attempt + 1, e)
            # في حال حدوث خطأ غير مرتبط بالفيضانات، نخرج فوراً مع فشل الإرسال
            await update.message.reply_text(
                "Something went wrong while sending the album. Please try again in a minute or contact us."
            )
            return False
    # إذا استنفذنا المحاولات دون نجاح نرجع False
    return False


async def create_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    if total_media < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return

    logger.info("Starting album conversion. Total media stored: %d", total_media)
    # تقسيم الوسائط إلى مجموعات بحد أقصى 10 عناصر لكل مجموعة
    chunks = [media_queue[i: i + 10] for i in range(0, total_media, 10)]
    channel_id = os.environ.get("CHANNEL_ID")
    processed = 0

    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            if i == 0:
                # تطبيق التسمية على العنصر الأول بغض النظر عن نوعه
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"], caption=MESSAGES["album_caption"]))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"], caption=MESSAGES["album_caption"]))
            else:
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"]))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"]))
        
        # محاولة إرسال المجموعة مع استخدام تقنية exponential backoff
        success = await send_media_group_with_backoff(update, context, input_media, channel_id, index)
        if not success:
            logger.error("Failed to send album chunk %d after retries.", index + 1)
            # يمكن إضافة منطق إضافي لمعالجة الفشل هنا إذا دعت الحاجة
        processed += len(chunk)
        remaining = total_media - processed
        logger.info("Processed chunk %d. Remaining media: %d", index + 1, remaining)
        # الانتظار لفترة ثابتة إضافية بين المجموعات لتفادي تجاوز الحدود
        await asyncio.sleep(30)
    
    # تفريغ قائمة الوسائط بعد الانتهاء
    context.user_data["media_queue"] = []


async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return

    application = Application.builder().token(token).build()

    # تسجيل الأوامر الأساسية
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    # تسجيل معالجات الصور والفيديوهات
    application.add_handler(MessageHandler(filters.PHOTO, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO, add_video))

    # معالجات رسائل لوحة المفاتيح
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$"), create_album)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$"), reset_album)
    )

    # بدء البوت باستخدام long polling
    application.run_polling()


if __name__ == '__main__':
    main()

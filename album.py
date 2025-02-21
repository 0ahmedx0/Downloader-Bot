import os
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

# إعداد تسجيل الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# تعريف الرسائل المستخدمة (يمكن لاحقًا توسيعها لدعم لغات متعددة)
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
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])],
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
    # الحصول على الصورة ذات الجودة الأعلى (آخر عنصر في القائمة)
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


async def create_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media_queue = context.user_data.get("media_queue", [])
    if len(media_queue) < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return

    # تقسيم الوسائط إلى مجموعات بحد أقصى 10 عناصر لكل مجموعة
    chunks = [media_queue[i : i + 10] for i in range(0, len(media_queue), 10)]
    for chunk in chunks:
        input_media = []
        for item in chunk:
            if item["type"] == "photo":
                input_media.append(InputMediaPhoto(media=item["media"]))
            elif item["type"] == "video":
                input_media.append(InputMediaVideo(media=item["media"]))
        try:
            await update.message.reply_media_group(media=input_media)
        except Exception as e:
            logger.error("Error sending album: %s", e)
            await update.message.reply_text(
                "Something went wrong while sending the album. Please try again in a minute or contact us."
            )
    # بعد الإرسال يتم مسح قائمة الوسائط
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

    # معالجات رسائل لوحة المفاتيح (النصوص الثابتة)
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$"), create_album))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$"), reset_album))

    # بدء البوت بطريقة long polling
    application.run_polling()


if __name__ == '__main__':
    main()

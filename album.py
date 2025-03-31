import os
import asyncio
import random
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
from telegram.error import RetryAfter

# إعداد المعرف الخاص بالقناة
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

# دالة توليد تأخير عشوائي
prev_delay = None
def get_random_delay(min_delay=30, max_delay=90, min_diff=30):
    global prev_delay
    delay = random.randint(min_delay, max_delay)
    while prev_delay is not None and abs(delay - prev_delay) < min_diff:
        delay = random.randint(min_delay, max_delay)
    prev_delay = delay
    return delay

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
    max_retries = 5
    for attempt in range(max_retries):
        try:
            await context.bot.send_media_group(chat_id=channel_id, media=input_media)
            return True
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.",
                           chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error("Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            return False
    return False

async def create_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)

    if total_media < 2:
        print("Not enough media items to create an album.")
        return

    logger.info("Starting album conversion. Total media stored: %d", total_media)

    chunks = [media_queue[i: i + 10] for i in range(0, total_media, 10)]
    total_albums = len(chunks)
    processed_albums = 0

    global CHANNEL_ID
    if not CHANNEL_ID:
        print("No channel has been set yet. Use /setchannel in your channel.")
        return

    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            if i == 0:
                if item["type"] == "photo":
                    input_media.append(
                        InputMediaPhoto(media=item["media"], caption=MESSAGES["album_caption"])
                    )
                elif item["type"] == "video":
                    input_media.append(
                        InputMediaVideo(media=item["media"], caption=MESSAGES["album_caption"])
                    )
            else:
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"]))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"]))

        success = await send_media_group_with_backoff(update, context, input_media, CHANNEL_ID, index)
        if not success:
            logger.error("Failed to send album chunk %d after retries.", index + 1)

        processed_albums += 1
        remaining_albums = total_albums - processed_albums
        delay = get_random_delay()
        estimated_time_remaining = remaining_albums * delay
        minutes, seconds = divmod(estimated_time_remaining, 60)
        time_remaining_str = f"{minutes} minutes and {seconds} seconds"

        progress_message = (
            f"Progress: {processed_albums}/{total_albums} albums sent.\n"
            f"Estimated time remaining: {time_remaining_str}."
        )
        print(progress_message)
        logger.info(progress_message)

        await asyncio.sleep(delay)

    context.user_data["media_queue"] = []
    print("All albums have been sent successfully!")

async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))
    application.add_handler(MessageHandler(filters.PHOTO, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO, add_video))
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$"), create_album)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$"), reset_album)
    )
    application.run_polling()

if __name__ == '__main__':
    main()

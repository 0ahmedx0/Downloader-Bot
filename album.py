import os
import asyncio
import logging
import random
import math

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    ReplyKeyboardRemove, # لإزالة لوحة المفاتيح المخصصة
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler, # لاستخدام محادثات متعددة الخطوات
)
from telegram.error import RetryAfter
from telegram.constants import ParseMode


# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحالات للمحادثة
ASKING_FOR_CAPTION = 1
# يمكنك إضافة المزيد من الحالات إذا احتجت محادثات متعددة الخطوات أكثر تعقيدًا

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
    "not_enough_media_items": "📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.",
    "queue_cleared": "لقد نسيت كل الصور والفيديوهات التي أرسلتها لي. لديك فرصة جديدة.",
    "album_caption_prompt": "الرجاء إدخال *التعليق* الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`",
    "album_caption_confirm": "👍 حسناً! التعليق الذي اخترته هو: `{caption}`.\nجاري إنشاء الألبوم الآن...",
    "processing_album": "⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...",
    "progress_update": "جاري إرسال الألبوم: *{processed_albums}/{total_albums}*\nالوقت المتبقي المقدر: *{time_remaining_str}*.",
    "album_creation_success": "✅ تم إنشاء جميع الألبومات بنجاح!",
    "album_creation_error": "❌ حدث خطأ أثناء إرسال الألبوم. يرجى المحاولة لاحقاً.",
    "album_chunk_fail": "⚠️ فشل إرسال جزء من الألبوم ({index}/{total_albums}). سأحاول الاستمرار مع البقية.",
    "cancel_caption": "لقد ألغيت عملية إنشاء الألبوم. يمكنك البدء من جديد."
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
    # لا حاجة للتحقق من None لأن context.user_data يتم تهيئته تلقائيًا بواسطة PTB
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    if "messages_to_delete" not in context.user_data:
        context.user_data["messages_to_delete"] = []

async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """يحذف جميع الرسائل المخزنة في قائمة messages_to_delete."""
    if "messages_to_delete" in context.user_data:
        message_ids = list(context.user_data["messages_to_delete"])
        for msg_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.info(f"Deleted message with ID: {msg_id}")
            except Exception as e:
                logger.warning(f"Could not delete message {msg_id} in chat {chat_id}: {e}")
        context.user_data["messages_to_delete"].clear()

# الأوامر الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context)
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
    await initialize_user_data(context)
    photo = update.message.photo[-1]
    file_id = photo.file_id
    context.user_data["media_queue"].append({"type": "photo", "media": file_id})
    logger.info("Added photo: %s", file_id)

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context)
    video = update.message.video
    file_id = video.file_id
    context.user_data["media_queue"].append({"type": "video", "media": file_id})
    logger.info("Added video: %s", file_id)

# إرسال الوسائط مع التعامل مع فيضانات تيليجرام
async def send_media_group_with_backoff(update: Update, context: ContextTypes.DEFAULT_TYPE, input_media, chat_id, chunk_index):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            await context.bot.send_media_group(chat_id=chat_id, media=input_media)
            return True
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.",
                           chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error("Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            await update.message.reply_text(MESSAGES["album_creation_error"])
            return False
    return False

# -------------------------------------------------------------
# دوال ConversationHandler
# -------------------------------------------------------------

async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الأولى في محادثة إنشاء الألبوم: تطلب من المستخدم إدخال التعليق.
    """
    await initialize_user_data(context)
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)

    if total_media < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return ConversationHandler.END # إنهاء المحادثة
    
    # إرسال رسالة طلب التعليق وتخزينها لحذفها لاحقاً
    prompt_msg = await update.message.reply_text(
        MESSAGES["album_caption_prompt"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove() # إزالة لوحة المفاتيح المخصصة مؤقتًا
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)
    
    # ندخل حالة انتظار التعليق
    return ASKING_FOR_CAPTION

async def receive_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الثانية: تستقبل التعليق وتبدأ في إنشاء الألبوم.
    """
    user_caption = update.message.text
    if user_caption == '.': # إذا أدخل المستخدم نقطة، لا يوجد تعليق
        user_caption = ""

    context.user_data["current_album_caption"] = user_caption

    # تأكيد التعليق وبدء العملية
    confirm_msg = await update.message.reply_text(
        MESSAGES["album_caption_confirm"].format(caption=user_caption if user_caption else "*لا يوجد تعليق*"),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["messages_to_delete"].append(confirm_msg.message_id)

    # يمكننا الآن استدعاء الدالة التي تقوم فعليًا بإنشاء الألبوم
    # يتم تمرير update و context هنا
    await execute_album_creation(update, context, user_caption)

    # بعد الانتهاء من الإرسال، نقوم بإعادة لوحة المفاتيح وإغلاق المحادثة
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("يمكنك الآن إرسال المزيد من الوسائط أو استخدام الأزرار أدناه.", reply_markup=reply_markup)

    return ConversationHandler.END # إنهاء المحادثة

async def cancel_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي محادثة التعليق إذا ضغط المستخدم على Clear Album أثناء المطالبة.
    """
    chat_id = update.effective_chat.id
    await delete_messages_from_queue(context, chat_id) # حذف رسالة المطالبة بالتعليق
    context.user_data["media_queue"] = [] # إعادة تعيين قائمة الوسائط
    await update.message.reply_text(
        MESSAGES["cancel_caption"],
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton(MESSAGES["keyboard_done"])],
            [KeyboardButton(MESSAGES["keyboard_clear"])]
        ], resize_keyboard=True, one_time_keyboard=False)
    )
    return ConversationHandler.END # إنهاء المحادثة

# دالة منفصلة لإنشاء الألبوم الفعلي
async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, album_caption: str) -> None:
    """
    يقوم بإنشاء وإرسال الألبوم بناءً على الوسائط المخزنة والتعليق المحدد.
    """
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    chat_id = update.effective_chat.id

    logger.info("Starting album conversion. Total media stored: %d", total_media)

    # الحد الأقصى للعناصر في الألبوم الواحد (تيليجرام يسمح بـ 10)
    max_items_per_album = 10
    num_albums = math.ceil(total_media / max_items_per_album)
    
    base_chunk_size = total_media // num_albums
    remainder = total_media % num_albums
    
    chunk_sizes = []
    for i in range(num_albums):
        current_size = base_chunk_size
        if i < remainder:
            current_size += 1
        chunk_sizes.append(current_size)
        
    chunks = []
    current_idx = 0
    for size in chunk_sizes:
        chunks.append(media_queue[current_idx : current_idx + size])
        current_idx += size

    total_albums = len(chunks)
    processed_albums = 0
    
    progress_msg = None 

    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            if i == 0:
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"], caption=album_caption))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"], caption=album_caption))
            else:
                if item["type"] == "photo":
                    input_media.append(InputMediaPhoto(media=item["media"]))
                elif item["type"] == "video":
                    input_media.append(InputMediaVideo(media=item["media"]))

        success = await send_media_group_with_backoff(update, context, input_media, chat_id, index)
        if not success:
            logger.error("Failed to send album chunk %d after retries.", index + 1)
            error_message = MESSAGES["album_chunk_fail"].format(index=index + 1, total_albums=total_albums)
            try:
                error_feedback_msg = await update.message.reply_text(error_message)
                context.user_data["messages_to_delete"].append(error_feedback_msg.message_id)
            except Exception as e:
                logger.error(f"Failed to send error feedback message: {e}")
            continue

        processed_albums += 1
        remaining_albums = total_albums - processed_albums
        
        avg_delay_per_album = (get_random_delay(min_delay=5, max_delay=30, min_diff=7) + 5)
        estimated_time_remaining = remaining_albums * avg_delay_per_album
        
        minutes, seconds = divmod(int(estimated_time_remaining), 60)
        time_remaining_str = f"{minutes} دقيقة و {seconds} ثانية" if minutes > 0 else f"{seconds} ثانية"

        current_progress_text = MESSAGES["progress_update"].format(
            processed_albums=processed_albums,
            total_albums=total_albums,
            time_remaining_str=time_remaining_str
        )
        
        try:
            if progress_msg:
                await progress_msg.edit_text(current_progress_text, parse_mode=ParseMode.MARKDOWN)
            else:
                progress_msg = await update.message.reply_text(current_progress_text, parse_mode=ParseMode.MARKDOWN)
                context.user_data["messages_to_delete"].append(progress_msg.message_id)
        except Exception as e:
            logger.error("Failed to update/send progress message: %s", e)

        delay_between_albums = get_random_delay()
        if index < len(chunks) - 1:
            await asyncio.sleep(delay_between_albums)
    
    context.user_data["media_queue"] = []
    
    # حذف جميع الرسائل التي تم تتبعها
    await delete_messages_from_queue(context, chat_id)

    await update.message.reply_text(MESSAGES["album_creation_success"])

# إعادة ضبط قائمة الوسائط
async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context)
    chat_id = update.effective_chat.id
    # إذا كان هناك محادثة جارية، قم بإنهاءها أولاً
    if "current_album_caption" in context.user_data: # check if conversation is active (rough check)
        context.user_data.pop("current_album_caption", None) # clear potential caption state
    
    await delete_messages_from_queue(context, chat_id)
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

    # هنا نضمن إزالة لوحة المفاتيح المؤقتة وإعادة لوحة المفاتيح الرئيسية إذا كانت محادثة التعليق قيد التقدم
    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("البوت جاهز لاستقبال ملفات جديدة.", reply_markup=reply_markup)
    
    # إنهاء أي محادثة جارية بشكل صريح عند الضغط على "Reset Album"
    return ConversationHandler.END


# تشغيل البوت
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return

    application = Application.builder().token(token).build()

    # Conversation Handler لعملية التعليق
    caption_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_done']}$") & ~filters.COMMAND, start_album_creation_process)
        ],
        states={
            ASKING_FOR_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_album_caption)
            ]
        },
        fallbacks=[
            # إذا ضغط المستخدم على "Clear Album" أثناء طلب التعليق
            MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$") & ~filters.COMMAND, cancel_album_creation),
            # للتعامل مع أي أوامر أخرى أثناء المحادثة (يمكن تعديلها حسب الحاجة)
            CommandHandler("start", cancel_album_creation), # للهروب من المحادثة
            CommandHandler("help", cancel_album_creation),
            CommandHandler("settings", cancel_album_creation),
            CommandHandler("source", cancel_album_creation),
            # الرسائل الأخرى (صور/فيديو) ستكسر المحادثة مؤقتاً لكنها لا تلغيها بالكامل
            # يمكن إضافة معالج filters.ALL لضمان أن كل شيء يقود لـ cancel إذا أردت صارمية
        ]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    application.add_handler(caption_conversation_handler) # أضف الـ ConversationHandler أولاً

    # تأكد أن هذه المعالجات لن تتضارب مع ConversationHandler أثناء حالاته
    # ConversationHandler يتعامل مع filters.TEXT أولاً في هذه الحالة
    # ومعالجات add_photo/add_video ستظل تعمل خارج المحادثة
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    # تأكد أن معالج Reset Album يعمل دائمًا، سواء كان جزءًا من محادثة أم لا.
    # بما أننا أضفناه كـ fallback لـ ConversationHandler، فهو يتعامل مع حالة المحادثة
    # ولضمان عمله في جميع الأوقات، سنتركه هنا أيضًا. Python-telegram-bot
    # يتعامل مع أولوية المعالجات. إذا تم استهلاك الرسالة بواسطة ConversationHandler
    # فلن تصل إلى هذا المعالج. ولكننا قمنا بتعديل cancel_album_creation ليؤدي وظيفة إعادة التعيين.
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{MESSAGES['keyboard_clear']}$") & ~filters.COMMAND, reset_album))


    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

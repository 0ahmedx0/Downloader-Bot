import os
import asyncio
import logging
import random
import math
import re # import re for filters.Regex

from telegram import (
    Update,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
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
ASKING_FOR_MANUAL_CAPTION = 2 
# حالة جديدة لطلب مكان الإرسال
ASKING_FOR_SEND_LOCATION = 3 

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
    "keyboard_done": "إنشاء ألبوم",
    "keyboard_clear": "إعادة تعيين الألبوم",
    "not_enough_media_items": "📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.",
    "queue_cleared": "لقد نسيت كل الصور والفيديوهات التي أرسلتها لي. لديك فرصة جديدة.",
    "album_caption_prompt": "الرجاء اختيار تعليق للألبوم من الأزرار أدناه، أو اختر *إدخال تعليق يدوي*:",
    "album_caption_manual_prompt": "الرجاء إدخال التعليق الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`",
    "album_caption_confirm": "👍 حسناً! التعليق الذي اخترته هو: `{caption}`.",
    "album_caption_confirm_no_caption": "👍 حسناً! لن يكون هناك تعليق للألبوم.",
    "processing_album": "⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...",
    "progress_update": "جاري إرسال الألبوم: *{processed_albums}/{total_albums}*\nالوقت المتبقي المقدر: *{time_remaining_str}*.",
    "album_creation_success": "✅ تم إنشاء جميع الألبومات بنجاح!",
    "album_creation_error": "❌ حدث خطأ أثناء إرسال الألبوم. يرجى المحاولة لاحقاً.",
    "album_chunk_fail": "⚠️ فشل إرسال جزء من الألبوم ({index}/{total_albums}). سأحاول الاستمرار مع البقية.",
    "cancel_caption": "لقد ألغيت عملية إنشاء الألبوم. يمكنك البدء من جديد.",
    "album_comment_option_manual": "إدخال تعليق يدوي",
    # رسائل جديدة لطلب مكان الإرسال
    "ask_send_location": "أين تود إرسال الألبوم؟",
    "send_to_channel_button": "القناة 📢",
    "send_to_chat_button": "المحادثة معي 👤",
    "channel_id_missing": "❌ لم يتم ضبط متغير البيئة CHANNEL_ID. لا يمكن الإرسال للقناة.",
    "invalid_send_location_choice": "خيار غير صالح. الرجاء الاختيار من الأزرار.",
}

# التعليقات الجاهزة كأزرار
PREDEFINED_CAPTION_BUTTONS = [
    "عرض ورعان اجانب 🌈💋",
    "🌈 🔥 .",
    "حصريات منوع🌈🔥.",
    "حصريات🌈",
    "عربي منوع🌈🔥.",
    "اجنبي منوع🌈🔥.",
]


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
async def send_media_group_with_backoff(context: ContextTypes.DEFAULT_TYPE, chat_id, input_media, chunk_index, update: Update = None):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            sent_messages = await context.bot.send_media_group(chat_id=chat_id, media=input_media)
            return True, sent_messages
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.",
                           chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error("Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            if update:
                await update.message.reply_text(MESSAGES["album_creation_error"])
            return False, None
    return False, None

# -------------------------------------------------------------
# دوال ConversationHandler
# -------------------------------------------------------------

async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الأولى في محادثة إنشاء الألبوم: تطلب من المستخدم اختيار أو إدخال التعليق.
    """
    await initialize_user_data(context)
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)

    if total_media < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return ConversationHandler.END # إنهاء المحادثة
    
    # إنشاء لوحة المفاتيح مع التعليقات الجاهزة وخيار التعليق اليدوي
    keyboard = []
    for caption_text in PREDEFINED_CAPTION_BUTTONS:
        keyboard.append([KeyboardButton(caption_text)])
    keyboard.append([KeyboardButton(MESSAGES["album_comment_option_manual"])])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    # إرسال رسالة طلب التعليق وتخزينها لحذفها لاحقاً
    prompt_msg = await update.message.reply_text(
        MESSAGES["album_caption_prompt"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)
    
    return ASKING_FOR_CAPTION

async def handle_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الثانية: تستقبل اختيار التعليق من الأزرار.
    """
    user_choice = update.message.text
    # حذف رسالة السؤال عن التعليق (أو رسالة خطأ سابقة)
    await delete_messages_from_queue(context, update.effective_chat.id)

    if user_choice == MESSAGES["album_comment_option_manual"]:
        # إذا اختار المستخدم "إدخال تعليق يدوي"
        prompt_manual_msg = await update.message.reply_text(
            MESSAGES["album_caption_manual_prompt"],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["messages_to_delete"].append(prompt_manual_msg.message_id)
        return ASKING_FOR_MANUAL_CAPTION
    elif user_choice in PREDEFINED_CAPTION_BUTTONS:
        # إذا اختار المستخدم تعليقًا جاهزًا
        user_caption = user_choice
        context.user_data["current_album_caption"] = user_caption

        confirm_msg = await update.message.reply_text(
            MESSAGES["album_caption_confirm"].format(caption=user_caption),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardRemove()
        )
        context.user_data["messages_to_delete"].append(confirm_msg.message_id)
        
        # ننتقل لطلب مكان الإرسال بدلاً من الإرسال مباشرة
        return await ask_for_send_location(update, context)

    else:
        await update.message.reply_text("خيار غير صالح. الرجاء الاختيار من الأزرار المقدمة أو الضغط على /cancel لإلغاء العملية.")
        return ASKING_FOR_CAPTION

async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الثالثة: تستقبل التعليق اليدوي.
    """
    user_caption = update.message.text
    if user_caption == '.':
        user_caption = ""

    context.user_data["current_album_caption"] = user_caption
    
    # حذف رسالة السؤال عن التعليق اليدوي
    await delete_messages_from_queue(context, update.effective_chat.id)

    # تأكيد التعليق (بدون إزالة لوحة المفاتيح هنا، سيتم إزالتها لاحقاً)
    confirm_message = MESSAGES["album_caption_confirm"].format(caption=user_caption) if user_caption else MESSAGES["album_caption_confirm_no_caption"]
    confirm_msg_obj = await update.message.reply_text(
        confirm_message,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["messages_to_delete"].append(confirm_msg_obj.message_id)

    # ننتقل لطلب مكان الإرسال
    return await ask_for_send_location(update, context)

async def ask_for_send_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تطلب من المستخدم اختيار مكان إرسال الألبوم.
    """
    keyboard = [
        [KeyboardButton(MESSAGES["send_to_channel_button"])],
        [KeyboardButton(MESSAGES["send_to_chat_button"])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    prompt_msg = await update.message.reply_text(
        MESSAGES["ask_send_location"],
        reply_markup=reply_markup
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)

    return ASKING_FOR_SEND_LOCATION

async def handle_send_location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل اختيار المستخدم لمكان إرسال الألبوم وتنفذه.
    """
    send_location_choice = update.message.text
    user_caption = context.user_data.get("current_album_caption", "")
    user_chat_id = update.effective_chat.id
    
    # حذف رسالة السؤال عن مكان الإرسال
    await delete_messages_from_queue(context, user_chat_id)

    send_chat_id = None
    if send_location_choice == MESSAGES["send_to_channel_button"]:
        send_chat_id = os.getenv("CHANNEL_ID")
        if not send_chat_id:
            await update.message.reply_text(MESSAGES["channel_id_missing"])
            # نعود لطلب الاختيار إذا لم يكن هناك CHANNEL_ID
            return await ask_for_send_location(update, context)
        logger.info(f"User chose to send to channel: {send_chat_id}")
    elif send_location_choice == MESSAGES["send_to_chat_button"]:
        send_chat_id = user_chat_id
        logger.info(f"User chose to send to private chat: {send_chat_id}")
    else:
        await update.message.reply_text(MESSAGES["invalid_send_location_choice"])
        return ASKING_FOR_SEND_LOCATION # البقاء في نفس الحالة

    # إزالة لوحة المفاتيح بعد الاختيار
    await update.message.reply_text(MESSAGES["processing_album"], reply_markup=ReplyKeyboardRemove())

    await execute_album_creation(update, context, user_caption, send_chat_id)

    # إعادة لوحة المفاتيح الرئيسية بعد الانتهاء
    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("يمكنك الآن إرسال المزيد من الوسائط أو استخدام الأزرار أدناه.", reply_markup=reply_markup)

    return ConversationHandler.END # إنهاء المحادثة

async def cancel_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي محادثة التعليق إذا ضغط المستخدم على Clear Album أو /cancel أثناء المطالبة.
    """
    chat_id = update.effective_chat.id
    await delete_messages_from_queue(context, chat_id)
    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None) # clear potential caption state

    await update.message.reply_text(
        MESSAGES["cancel_caption"],
        reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton(MESSAGES["keyboard_done"])],
            [KeyboardButton(MESSAGES["keyboard_clear"])]
        ], resize_keyboard=True, one_time_keyboard=False)
    )
    return ConversationHandler.END

# دالة منفصلة لإنشاء الألبوم الفعلي
async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, album_caption: str, target_chat_id: int) -> None:
    """
    يقوم بإنشاء وإرسال الألبوم بناءً على الوسائط المخزنة والتعليق المحدد.
    الآن تستقبل target_chat_id لتحديد مكان الإرسال.
    """
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    user_chat_id = update.effective_chat.id # chat id of the user, not necessarily the target chat for the album

    logger.info("بدء تحويل الألبوم. عدد الوسائط: %d. الهدف: %s", total_media, target_chat_id)

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
        chunks.append(media_queue[current_idx: current_idx + size])
        current_idx += size

    total_albums = len(chunks)
    processed_albums = 0
    progress_msg = None

    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            caption = album_caption if i == 0 else None
            if item["type"] == "photo":
                input_media.append(InputMediaPhoto(media=item["media"], caption=caption))
            elif item["type"] == "video":
                input_media.append(InputMediaVideo(media=item["media"], caption=caption))

        # إرسال الألبوم
        success, sent_messages = await send_media_group_with_backoff(
            context=context,
            chat_id=target_chat_id, # استخدام target_chat_id هنا
            input_media=input_media,
            chunk_index=index,
            update=update # تمرير التحديث للتعامل مع رسائل الأخطاء
        )

        if not success:
            logger.error(f"Failed to send chunk {index + 1} to {target_chat_id}.")
            await update.message.reply_text(MESSAGES["album_chunk_fail"].format(index=index + 1, total_albums=total_albums))
            continue # حاول الاستمرار مع الأجزاء الأخرى

        logger.info(f"تم إرسال الدفعة {index + 1} إلى {target_chat_id}.")

        # تثبيت أول رسالة من الألبوم إذا تم الإرسال للقناة
        if target_chat_id == os.getenv("CHANNEL_ID") and sent_messages:
            try:
                await context.bot.pin_chat_message(chat_id=target_chat_id, message_id=sent_messages[0].message_id, disable_notification=True)
                logger.info("تم تثبيت الرسالة الأولى من الألبوم.")
            except Exception as pin_err:
                logger.error("فشل في تثبيت الرسالة: %s", pin_err)
                if user_chat_id != target_chat_id: # Avoid sending this error in the channel itself
                    await update.message.reply_text("⚠️ تم إرسال الألبوم للقناة ولكن تعذر تثبيت الرسالة الأولى.")
                
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
            # رسالة التقدم يجب أن ترسل دائمًا إلى المحادثة الخاصة مع المستخدم
            if progress_msg:
                await progress_msg.edit_text(current_progress_text, parse_mode=ParseMode.MARKDOWN)
            else:
                progress_msg = await context.bot.send_message(chat_id=user_chat_id, text=current_progress_text, parse_mode=ParseMode.MARKDOWN)
                context.user_data["messages_to_delete"].append(progress_msg.message_id)
        except Exception as e:
            logger.error("فشل في تحديث رسالة التقدم: %s", e)

        if index < len(chunks) - 1:
            await asyncio.sleep(get_random_delay())

    context.user_data["media_queue"] = []
    # يجب أن نحذف الرسائل من المحادثة الخاصة بالمستخدم، وليس القناة
    await delete_messages_from_queue(context, user_chat_id) 
    await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["album_creation_success"]) # رسالة النجاح للمستخدم

# إعادة ضبط قائمة الوسائط
async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await initialize_user_data(context)
    chat_id = update.effective_chat.id
    context.user_data.pop("current_album_caption", None)
    
    await delete_messages_from_queue(context, chat_id)
    context.user_data["media_queue"] = []
    await update.message.reply_text(MESSAGES["queue_cleared"])

    keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text("البوت جاهز لاستقبال ملفات جديدة.", reply_markup=reply_markup)
    
    return ConversationHandler.END


# تشغيل البوت
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return

    application = Application.builder().token(token).build()

    # Conversation Handler لعملية التعليق ومكان الإرسال
    caption_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$") & ~filters.COMMAND, start_album_creation_process)
        ],
        states={
            ASKING_FOR_CAPTION: [
                MessageHandler(filters.TEXT & filters.Regex(f"({'|'.join(map(re.escape, PREDEFINED_CAPTION_BUTTONS + [MESSAGES['album_comment_option_manual']]))})") & ~filters.COMMAND, handle_caption_choice),
            ],
            ASKING_FOR_MANUAL_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption),
            ],
            # الحالة الجديدة
            ASKING_FOR_SEND_LOCATION: [
                MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['send_to_channel_button'])}$|^^{re.escape(MESSAGES['send_to_chat_button'])}$") & ~filters.COMMAND, handle_send_location_choice),
            ],
        },
        fallbacks=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, cancel_album_creation),
            CommandHandler("cancel", cancel_album_creation),
            CommandHandler("start", cancel_album_creation),
            CommandHandler("help", cancel_album_creation),
            CommandHandler("settings", cancel_album_creation),
            CommandHandler("source", cancel_album_creation),
        ]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    application.add_handler(caption_conversation_handler)

    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_album))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

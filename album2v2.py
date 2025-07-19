import os
import asyncio
import logging
import random
import math
import re

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import RetryAfter, TelegramError, BadRequest
from telegram.constants import ParseMode


# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحالات للمحادثة
ASKING_FOR_CAPTION = 1
ASKING_FOR_MANUAL_CAPTION = 2
CHANGING_SPLIT_MODE = 4


# Callbacks prefixes
CAPTION_CB_PREFIX = "cap_"
CANCEL_CB_DATA = "cancel_op"
SPLIT_SET_CB_PREFIX = "splitset_"


# الرسائل المستخدمة
MESSAGES = {
    "greeting": (
        "مرحباً {username}! هل سبق أن وجدت صوراً رائعة على تيليجرام "
        "وأردت تجميعها في ألبوم، لكن لم ترغب في تنزيلها ثم إعادة رفعها؟ "
        "دعني أقوم بذلك بسرعة!\n\n"
        "أرسل لي أي صور أو فيديوهات وسأقوم بإنشاء ألبومات منها!\n\n"
    ),
    "destination_set_success": "👍 تم تعيين هذه الدردشة كوجهة تلقائية لإرسال الألبومات.",
    "help": (
        'عندما تنتهي من إرسال الصور والفيديوهات، سيظهر لك خيار إنشاء الألبوم تلقائيًا بعد 3 ثوانٍ من إرسال أول ملف. يمكنك أيضًا الضغط على زر "إنشاء ألبوم" يدويًا في أي وقت. إذا أخطأت، انقر على "إعادة تعيين الألبوم" للبدء من جديد.\n\n'
        "هذا العمل تم بواسطة @wjclub."
    ),
    "settings": "لا توجد إعدادات لتغييرها هنا.",
    "source": "https://github.com/wjclub/telegram-bot-album-creator",
    "keyboard_done": "إنشاء ألبوم",
    "keyboard_clear": "إعادة تعيين الألبوم",
    "keyboard_change_split_mode": "تغيير نمط التقسيم 📊",
    "not_enough_media_items": "📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.",
    "queue_cleared": "لقد نسيت كل الصور والفيديوهات التي أرسلتها لي. لديك فرصة جديدة.",
    "album_caption_prompt": "الرجاء اختيار تعليق للألبوم من الأزرار أدناه:",
    "album_caption_manual_prompt": "الرجاء إدخال التعليق الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`",
    "album_caption_confirm": "👍 حسناً! التعليق الذي اخترته هو: `{caption}`.\n",
    "album_caption_confirm_no_caption": "👍 حسناً! لن يكون هناك تعليق للألبوم.\n",
    "processing_album_start": "⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...",
    "progress_update": "جاري إرسال الألبوم: *{processed_albums}/{total_albums}*\nالوقت المتبقي المقدر: *{time_remaining_str}*",
    "cancel_caption": "لقد ألغيت عملية إنشاء الألبوم. يمكنك البدء من جديد.",
    "cancel_operation": "تم إلغاء العملية.",
    "album_comment_option_manual": "إدخال تعليق يدوي ✍️", # تم تعديل النص لتمييزه
    "invalid_input_choice": "خيار غير صالح أو إدخال غير متوقع. الرجاء الاختيار من الأزرار أو إلغاء العملية.",
    "success_message_permanent_prompt": "يمكنك الآن إرسال المزيد من الوسائط أو استخدام الأزرار أدناه.",
    "ask_split_mode_setting": "اختر نمط تقسيم الألبوم الافتراضي. سيتم استخدامه لكل الألبومات القادمة حتى تغييره مرة أخرى.",
    "split_mode_set_success": "👍 تم تعيين نمط تقسيم الألبومات إلى: *{split_mode_name}*.",
    "album_split_mode_full": "ألبومات كاملة (10 عناصر)",
    "album_split_mode_equal": "تقسيم متساوي",
}

# --- التغيير المطلوب: تم تحديث قائمة التعليقات ---
PREDEFINED_CAPTION_OPTIONS = [
    "حصريات عربي 🌈🔥.", 
    "حصريات اجنبي 🌈🔥.",
    "لا يوجد تعليق", 
    MESSAGES["album_comment_option_manual"],
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
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """يضمن تهيئة context.user_data بالكامل وتعيين وجهة الإرسال."""
    if not context.user_data: # تهيئة كاملة إذا كانت البيانات فارغة
        defaults = {
            "media_queue": [],
            "messages_to_delete": [],
            "temp_messages_to_clean": [],
            "progress_message_id": None,
            "album_split_mode": "equal", # "equal" أو "full_10"
            "album_split_mode_name": MESSAGES["album_split_mode_equal"],
            "album_creation_started": False, # علم لتتبع بدء العملية
        }
        for key, value in defaults.items():
            context.user_data[key] = value if not isinstance(value, list) else list(value)
    
    context.user_data["album_destination_chat_id"] = chat_id
    context.user_data["album_destination_name"] = "هذه المحادثة"

# دالة بناء لوحة المفاتيح الرئيسية
def get_main_reply_markup() -> ReplyKeyboardMarkup:
    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"]), KeyboardButton(MESSAGES["keyboard_clear"])],
        [KeyboardButton(MESSAGES["keyboard_change_split_mode"])]
    ]
    return ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)


async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    message_ids = list(context.user_data.get("messages_to_delete", []))
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except BadRequest:
            pass
        except Exception as e:
            logger.warning(f"Could not delete message {msg_id} in chat {chat_id}: {e}")
    context.user_data.get("messages_to_delete", []).clear()


# --- الإضافة الجديدة: دالة مساعدة لبدء عملية إنشاء الألبوم بعد تأخير ---
async def trigger_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    تنتظر 3 ثوانٍ ثم تبدأ محادثة إنشاء الألبوم.
    """
    await asyncio.sleep(3)
    # التحقق مما إذا كان المستخدم قد ألغى العملية أثناء الانتظار
    if not context.user_data.get("media_queue"):
        logger.info("Auto-creation cancelled because queue is empty.")
        return
    
    # التحقق مما إذا كانت العملية قد بدأت بالفعل (مثلاً، عن طريق الضغط على الزر)
    if context.user_data.get("album_creation_started", False):
        logger.info("Auto-creation skipped because a process is already running.")
        return

    logger.info("Auto-triggering album creation process...")
    # استدعاء الدالة التي تبدأ المحادثة
    await start_album_creation_process(update, context, is_auto_trigger=True)


# الأوامر الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)
    
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)
    await update.message.reply_text(message, reply_markup=get_main_reply_markup())
    await update.message.reply_text(MESSAGES["destination_set_success"])

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["help"])


# --- التغيير المطلوب: تعديل وظائف إضافة الوسائط ---
async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context, update.effective_chat.id)
    
    # التحقق مما إذا كانت هذه هي أول وسائط
    is_first_item = len(context.user_data.get("media_queue", [])) == 0

    context.user_data["media_queue"].append({"type": "photo", "media": update.message.photo[-1].file_id})
    logger.info("Added photo")
    
    # إذا كانت هذه أول وسائط ولم تبدأ عملية إنشاء ألبوم بالفعل، فابدأ العد التنازلي
    if is_first_item and not context.user_data.get("album_creation_started", False):
        asyncio.create_task(trigger_album_creation(update, context))

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context, update.effective_chat.id)
    
    # التحقق مما إذا كانت هذه هي أول وسائط
    is_first_item = len(context.user_data.get("media_queue", [])) == 0
    
    context.user_data["media_queue"].append({"type": "video", "media": update.message.video.file_id})
    logger.info("Added video")
    
    # إذا كانت هذه أول وسائط ولم تبدأ عملية إنشاء ألبوم بالفعل، فابدأ العد التنازلي
    if is_first_item and not context.user_data.get("album_creation_started", False):
        asyncio.create_task(trigger_album_creation(update, context))


# دوال ConversationHandler (لتغيير نمط التقسيم)
async def prompt_for_split_mode_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton(MESSAGES["album_split_mode_full"], callback_data=f"{SPLIT_SET_CB_PREFIX}full_10")],
        [InlineKeyboardButton(MESSAGES["album_split_mode_equal"], callback_data=f"{SPLIT_SET_CB_PREFIX}equal")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)]
    ]
    prompt_msg = await update.message.reply_text(MESSAGES["ask_split_mode_setting"], reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    return CHANGING_SPLIT_MODE

async def handle_split_mode_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    choice = query.data
    await query.answer()
    try: await query.delete_message()
    except BadRequest: pass

    if choice == CANCEL_CB_DATA:
        await cancel_operation_general(update, context)
        return ConversationHandler.END

    mode, mode_name = (None, None)
    if choice == f"{SPLIT_SET_CB_PREFIX}full_10":
        mode, mode_name = "full_10", MESSAGES["album_split_mode_full"]
    elif choice == f"{SPLIT_SET_CB_PREFIX}equal":
        mode, mode_name = "equal", MESSAGES["album_split_mode_equal"]
    
    if mode:
        context.user_data["album_split_mode"] = mode
        context.user_data["album_split_mode_name"] = mode_name
        await context.bot.send_message(query.message.chat_id, MESSAGES["split_mode_set_success"].format(split_mode_name=mode_name), parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# دوال ConversationHandler (لإنشاء الألبوم)
async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE, is_auto_trigger: bool = False) -> int:
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)
    
    # تعيين علم بأن العملية قد بدأت لمنع التشغيل التلقائي المتعدد
    context.user_data["album_creation_started"] = True
    
    if len(context.user_data.get("media_queue", [])) < 2:
        # لا ترسل رسالة خطأ إذا كان التشغيل تلقائيًا، فقط انتظر المزيد من الملفات
        if not is_auto_trigger:
             await update.message.reply_text(MESSAGES["not_enough_media_items"])
        context.user_data["album_creation_started"] = False # إعادة التعيين للسماح بالمحاولة مرة أخرى
        return ConversationHandler.END

    keyboard = []
    for i, caption in enumerate(PREDEFINED_CAPTION_OPTIONS):
        keyboard.append([InlineKeyboardButton(caption, callback_data=f"{CAPTION_CB_PREFIX}{i}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])
    
    # استخدام context.bot.send_message بدلاً من update.message.reply_text لضمان العمل مع التشغيل التلقائي
    prompt_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["album_caption_prompt"],
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    
    return ASKING_FOR_CAPTION

async def handle_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    choice = query.data
    await query.answer()
    try: await query.delete_message()
    except BadRequest: pass

    if choice == CANCEL_CB_DATA:
        await cancel_album_creation(update, context)
        return ConversationHandler.END
    
    caption_index = int(choice.replace(CAPTION_CB_PREFIX, ""))
    selected_option = PREDEFINED_CAPTION_OPTIONS[caption_index]

    if selected_option == MESSAGES["album_comment_option_manual"]:
        prompt_msg = await context.bot.send_message(
            query.message.chat_id,
            MESSAGES["album_caption_manual_prompt"],
            reply_markup=ReplyKeyboardRemove(),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
        return ASKING_FOR_MANUAL_CAPTION
    
    user_caption = "" if selected_option == "لا يوجد تعليق" else selected_option
    context.user_data["current_album_caption"] = user_caption
    return await finalize_album_action(update, context)

async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_caption = update.message.text
    context.user_data["current_album_caption"] = "" if user_caption == '.' else user_caption
    return await finalize_album_action(update, context)

async def finalize_album_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # لتحديد chat_id بشكل صحيح سواء جاء التحديث من رسالة أو callback
    chat_id = update.effective_chat.id
    await delete_messages_from_queue(context, chat_id)

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["processing_album_start"],
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["progress_message_id"] = progress_msg.message_id

    await execute_album_creation(update, context)
    
    # إعادة تعيين علم بدء العملية بعد الانتهاء
    context.user_data["album_creation_started"] = False

    progress_msg_id = context.user_data.pop("progress_message_id", None)
    if progress_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
        except Exception:
            pass
    
    return ConversationHandler.END


# دوال التنفيذ والإلغاء
async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    user_chat_id = update.effective_chat.id
    target_chat_id = context.user_data["album_destination_chat_id"]
    album_caption = context.user_data.get("current_album_caption", "")
    
    split_mode = context.user_data.get("album_split_mode", "equal")
    logger.info(f"Creating album. Media: {total_media}, Split mode: {split_mode}")

    chunks = []
    if total_media > 0:
        max_items_per_album = 10
        if split_mode == 'full_10':
            chunks = [media_queue[i:i + max_items_per_album] for i in range(0, total_media, max_items_per_album)]
        else: # equal split
            num_albums = math.ceil(total_media / max_items_per_album)
            base_size = total_media // num_albums
            rem = total_media % num_albums
            sizes = [base_size + 1 if i < rem else base_size for i in range(num_albums)]
            start_idx = 0
            for size in sizes:
                chunks.append(media_queue[start_idx:start_idx + size])
                start_idx += size

    total_albums = len(chunks)
    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            caption = album_caption if i == 0 else None
            MediaClass = InputMediaPhoto if item["type"] == "photo" else InputMediaVideo
            input_media.append(MediaClass(media=item["media"], caption=caption))
        
        for attempt in range(5):
            try:
                await context.bot.send_media_group(chat_id=target_chat_id, media=input_media)
                break
            except RetryAfter as e:
                logger.warning(f"RetryAfter on chunk {index+1}, waiting {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                logger.error(f"Failed to send chunk {index+1}: {e}")
                break
        
        progress_msg_id = context.user_data.get("progress_message_id")
        if progress_msg_id and total_albums > 1:
            try:
                progress_text = f"{MESSAGES['processing_album_start']}\n"
                progress_text += MESSAGES['progress_update'].format(processed_albums=index + 1, total_albums=total_albums, time_remaining_str="...")
                await context.bot.edit_message_text(chat_id=user_chat_id, message_id=progress_msg_id, text=progress_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Failed to update progress message: {e}")
        
        if index < total_albums - 1:
            await asyncio.sleep(get_random_delay())

    # إعادة تعيين قائمة الانتظار والبيانات المؤقتة
    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)

async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context, update.effective_chat.id)
    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)
    context.user_data["album_creation_started"] = False # إعادة التعيين
    await update.message.reply_text(MESSAGES["queue_cleared"], reply_markup=get_main_reply_markup())

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.answer()
        chat_id = update.callback_query.message.chat_id
        try: await update.callback_query.delete_message()
        except: pass
    
    await delete_messages_from_queue(context, chat_id)
    
    text, markup = (MESSAGES["cancel_operation"], get_main_reply_markup())
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    
    context.user_data.pop("current_album_caption", None)
    context.user_data["album_creation_started"] = False # إعادة التعيين عند الإلغاء
    
    return ConversationHandler.END

cancel_album_creation = cancel_operation
cancel_operation_general = cancel_operation


# تشغيل البوت
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return
    
    application = Application.builder().token(token).build()
    
    split_mode_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_split_mode'])}$"), prompt_for_split_mode_setting)],
        states={CHANGING_SPLIT_MODE: [CallbackQueryHandler(handle_split_mode_choice, pattern=f"^{SPLIT_SET_CB_PREFIX}.*|^{CANCEL_CB_DATA}$")]},
        fallbacks=[CommandHandler("cancel", cancel_operation_general)]
    )
    
    album_creation_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$"), start_album_creation_process)],
        states={
            ASKING_FOR_CAPTION: [CallbackQueryHandler(handle_caption_choice, pattern=f"^{CAPTION_CB_PREFIX}.*|^{CANCEL_CB_DATA}$")],
            ASKING_FOR_MANUAL_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption)],
        },
        fallbacks=[CommandHandler("cancel", cancel_album_creation)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    application.add_handler(split_mode_conv)
    application.add_handler(album_creation_conv)
    
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$"), reset_album))
    
    # معالجات الوسائط ستعمل بشكل مستقل الآن لبدء العملية تلقائيًا
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

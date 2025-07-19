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

# --- تغيير: حالات محادثة جديدة ومنفصلة ---
# حالات المحادثة الرئيسية (التي لم تعد تستخدم كثيراً)
ASKING_FOR_CAPTION = 1
# حالات المحادثة الجديدة لإدخال التعليق اليدوي
ASKING_FOR_MANUAL_CAPTION = 2 
CHANGING_SPLIT_MODE = 4


# Callbacks prefixes
CAPTION_CB_PREFIX = "cap_"
# --- إضافة: Prefixes جديدة للتحكم بشكل أفضل ---
MANUAL_CAPTION_CB_DATA = "cap_manual"
NO_CAPTION_CB_DATA = "cap_none"
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
    "keyboard_done": "إنشاء ألبوم",
    "keyboard_clear": "إعادة تعيين الألبوم",
    "keyboard_change_split_mode": "تغيير نمط التقسيم 📊",
    "not_enough_media_items": "📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.",
    "queue_cleared": "لقد نسيت كل الصور والفيديوهات التي أرسلتها لي. لديك فرصة جديدة.",
    "album_caption_prompt": "الرجاء اختيار تعليق للألبوم من الأزرار أدناه:",
    "album_caption_manual_prompt": "الرجاء إدخال التعليق الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`",
    "processing_album_start": "⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...",
    "cancel_operation": "تم إلغاء العملية.",
    "album_comment_option_manual": "إدخال تعليق يدوي ✍️",
    "split_mode_set_success": "👍 تم تعيين نمط تقسيم الألبومات إلى: *{split_mode_name}*.",
    # ... بقية الرسائل كما هي
}

PREDEFINED_CAPTION_OPTIONS = {
    "cap_1": "حصريات عربي 🌈🔥.", 
    "cap_2": "حصريات اجنبي 🌈🔥.",
}


# --- بداية التغييرات الجوهرية ---

# تهيئة بيانات المستخدم
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    if "messages_to_delete" not in context.user_data:
        context.user_data["messages_to_delete"] = []
    if "album_creation_started" not in context.user_data:
        context.user_data["album_creation_started"] = False
    context.user_data["album_destination_chat_id"] = chat_id


# دالة لبدء عملية إنشاء الألبوم تلقائيًا
async def trigger_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(3)
    if not context.user_data.get("media_queue") or context.user_data.get("album_creation_started", False):
        return
    logger.info("Auto-triggering album creation process...")
    await start_album_creation_process(update, context, is_auto_trigger=True)

# إضافة الوسائط (مع التفعيل التلقائي)
async def add_media(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str):
    await initialize_user_data(context, update.effective_chat.id)
    is_first_item = len(context.user_data.get("media_queue", [])) == 0
    
    file_id = update.message.photo[-1].file_id if media_type == "photo" else update.message.video.file_id
    context.user_data["media_queue"].append({"type": media_type, "media": file_id})
    logger.info(f"Added {media_type}")
    
    if is_first_item and not context.user_data.get("album_creation_started", False):
        asyncio.create_task(trigger_album_creation(update, context))

async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_media(update, context, "photo")

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_media(update, context, "video")

# العملية الرئيسية لطلب التعليق
async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE, is_auto_trigger: bool = False):
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)
    context.user_data["album_creation_started"] = True
    
    if len(context.user_data.get("media_queue", [])) < 2:
        if not is_auto_trigger:
            await update.message.reply_text(MESSAGES["not_enough_media_items"])
        context.user_data["album_creation_started"] = False
        return ConversationHandler.END

    keyboard = []
    for key, text in PREDEFINED_CAPTION_OPTIONS.items():
        keyboard.append([InlineKeyboardButton(text, callback_data=key)])
    
    # إضافة الأزرار الثابتة
    keyboard.append([InlineKeyboardButton("لا يوجد تعليق", callback_data=NO_CAPTION_CB_DATA)])
    keyboard.append([InlineKeyboardButton(MESSAGES["album_comment_option_manual"], callback_data=MANUAL_CAPTION_CB_DATA)])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])

    prompt_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["album_caption_prompt"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    # لا نرجع حالة هنا لأن المعالج سيكون عاماً
    return


# معالج الأزرار للتعليقات المحددة مسبقًا (عام وليس داخل محادثة)
async def handle_predefined_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    caption_key = query.data
    user_caption = PREDEFINED_CAPTION_OPTIONS.get(caption_key, "") # الحصول على التعليق من القاموس
    context.user_data["current_album_caption"] = user_caption

    await finalize_album_action(update, context)


# معالج زر "لا يوجد تعليق"
async def handle_no_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["current_album_caption"] = ""
    await finalize_album_action(update, context)


# معالج الدخول في محادثة إدخال التعليق اليدوي
async def prompt_for_manual_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    try: await query.delete_message() 
    except BadRequest: pass

    prompt_msg = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=MESSAGES["album_caption_manual_prompt"],
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    return ASKING_FOR_MANUAL_CAPTION


# استقبال التعليق اليدوي وإنهاء المحادثة المصغرة
async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_caption = update.message.text
    context.user_data["current_album_caption"] = "" if user_caption == '.' else user_caption
    await finalize_album_action(update, context)
    return ConversationHandler.END


# الدالة النهائية لإنشاء الألبوم
async def finalize_album_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if update.callback_query:
        try: await update.callback_query.delete_message()
        except BadRequest: pass

    await delete_messages_from_queue(context, chat_id)

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["processing_album_start"],
    )
    
    await execute_album_creation(update, context)
    
    context.user_data["album_creation_started"] = False
    context.user_data.pop("current_album_caption", None)
    try: await context.bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
    except Exception: pass


async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذه الدالة تبقى كما هي بدون تغيير
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    target_chat_id = context.user_data["album_destination_chat_id"]
    album_caption = context.user_data.get("current_album_caption", "")
    split_mode = context.user_data.get("album_split_mode", "equal")
    chunks = []
    max_items_per_album = 10
    if total_media > 0:
        if split_mode == 'full_10':
            chunks = [media_queue[i:i + max_items_per_album] for i in range(0, total_media, max_items_per_album)]
        else:
            num_albums = math.ceil(total_media / max_items_per_album)
            base_size = total_media // num_albums
            rem = total_media % num_albums
            sizes = [base_size + 1 if i < rem else base_size for i in range(num_albums)]
            start_idx = 0
            for size in sizes:
                chunks.append(media_queue[start_idx:start_idx + size])
                start_idx += size
    for index, chunk in enumerate(chunks):
        input_media = [
            (InputMediaPhoto(media=item["media"], caption=album_caption) if i == 0 else InputMediaPhoto(media=item["media"])) if item["type"] == "photo" 
            else (InputMediaVideo(media=item["media"], caption=album_caption) if i == 0 else InputMediaVideo(media=item["media"]))
            for i, item in enumerate(chunk)
        ]
        await context.bot.send_media_group(chat_id=target_chat_id, media=input_media)
        if index < len(chunks) - 1:
            await asyncio.sleep(random.randint(5,15)) # تأخير بسيط بين الألبومات
    context.user_data["media_queue"] = []
    logger.info(f"Successfully created {len(chunks)} albums.")

async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    # دالة مساعدة لحذف الرسائل
    message_ids = context.user_data.get("messages_to_delete", [])
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    context.user_data["messages_to_delete"] = []

# دوال الإلغاء وإعادة التعيين
async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await initialize_user_data(context, update.effective_chat.id)
    context.user_data["media_queue"] = []
    context.user_data["album_creation_started"] = False
    await update.message.reply_text(MESSAGES["queue_cleared"])

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        try: await query.delete_message()
        except BadRequest: pass
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=MESSAGES["cancel_operation"])
    context.user_data["album_creation_started"] = False
    return ConversationHandler.END


# الأوامر الأساسية (start, help, etc)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MESSAGES["greeting"].format(username=update.effective_user.username))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MESSAGES["help"])


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return
    
    application = Application.builder().token(token).build()

    # --- تغيير: محادثة جديدة ومصغرة للتعليق اليدوي فقط ---
    manual_caption_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(prompt_for_manual_caption, pattern=f"^{MANUAL_CAPTION_CB_DATA}$")],
        states={
            ASKING_FOR_MANUAL_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption)],
        },
        fallbacks=[CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"), CommandHandler("cancel", cancel_operation)]
    )

    # المعالج اليدوي لإنشاء الألبوم (يظل كما هو)
    manual_start_handler = MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$"), start_album_creation_process)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # إضافة المعالجات العامة
    application.add_handler(manual_start_handler)
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$"), reset_album))

    # إضافة المحادثة المصغرة للتعليقات اليدوية
    application.add_handler(manual_caption_conv)

    # --- تغيير: إضافة معالجات الأزرار كمعالجات عامة ---
    application.add_handler(CallbackQueryHandler(handle_predefined_caption_choice, pattern=r"^cap_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_no_caption_choice, pattern=f"^{NO_CAPTION_CB_DATA}$"))
    application.add_handler(CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"))
    
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

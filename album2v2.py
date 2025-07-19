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

# حالات المحادثة
ASKING_FOR_CAPTION = 1 # لم تعد تستخدم كثيرًا
ASKING_FOR_MANUAL_CAPTION = 2 
CHANGING_SPLIT_MODE = 4 # حالة جديدة لتغيير نمط التقسيم


# Callbacks prefixes
CAPTION_CB_PREFIX = "cap_"
MANUAL_CAPTION_CB_DATA = "cap_manual"
NO_CAPTION_CB_DATA = "cap_none"
CANCEL_CB_DATA = "cancel_op"

# --- إضافة: Prefix جديد لأنماط التقسيم ---
SPLIT_MODE_CB_PREFIX = "split_mode_"


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
    "split_mode_prompt": "الرجاء اختيار نمط تقسيم الألبومات:",
    # يمكنك إضافة أسماء الأنماط هنا لتكون مركزية
    "split_mode_name_equal": "تقسيم متساوي (قدر الإمكان)",
    "split_mode_name_full_10": "ألبوم من 10 (ثم جديد)",
}

PREDEFINED_CAPTION_OPTIONS = {
    "cap_1": "حصريات عربي 🌈🔥.", 
    "cap_2": "حصريات اجنبي 🌈🔥.",
}


# --- دوال لوحة المفاتيح الرئيسية ---
def get_main_keyboard() -> ReplyKeyboardMarkup:
    """ينشئ ويعيد ReplyKeyboardMarkup مع الأزرار الرئيسية."""
    keyboard = [
        [
            KeyboardButton(MESSAGES["keyboard_done"]),
            KeyboardButton(MESSAGES["keyboard_clear"]),
            KeyboardButton(MESSAGES["keyboard_change_split_mode"]),
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


# --- تهيئة بيانات المستخدم ---
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    if "messages_to_delete" not in context.user_data:
        context.user_data["messages_to_delete"] = []
    if "album_creation_started" not in context.user_data:
        context.user_data["album_creation_started"] = False
    context.user_data["album_destination_chat_id"] = chat_id
    # تعيين نمط التقسيم الافتراضي إذا لم يكن موجودًا
    if "album_split_mode" not in context.user_data:
        context.user_data["album_split_mode"] = "equal" # الافتراضي: تقسيم متساوي

# --- بدء عملية إنشاء الألبوم تلقائيًا ---
async def trigger_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(1) # تأخير قصير
    if not context.user_data.get("media_queue") or context.user_data.get("album_creation_started", False):
        return
    logger.info("Auto-triggering album creation process...")
    # تمرير تحديث وهمي لأن الدالة تستدعي بشكل تلقائي وليست من أمر مباشر
    await start_album_creation_process(update, context, is_auto_trigger=True)


# --- إضافة الوسائط (صور وفيديوهات) ---
async def add_media(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str):
    await initialize_user_data(context, update.effective_chat.id)
    is_first_item = len(context.user_data.get("media_queue", [])) == 0
    
    file_id = update.message.photo[-1].file_id if media_type == "photo" else update.message.video.file_id
    context.user_data["media_queue"].append({"type": media_type, "media": file_id})
    logger.info(f"Added {media_type}")
    
    # إظهار لوحة المفاتيح الرئيسية بعد إضافة أول وسيط
    if is_first_item and not context.user_data.get("album_creation_started", False):
        await update.message.reply_text(
            "تم إضافة الوسائط! يمكنك إرسال المزيد أو استخدام الأزرار أدناه:",
            reply_markup=get_main_keyboard()
        )
        # تشغيل الإنشاء التلقائي للألبوم بعد إضافة أول ملف
        asyncio.create_task(trigger_album_creation(update, context))

async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_media(update, context, "photo")

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await add_media(update, context, "video")


# --- عملية إنشاء الألبوم: طلب التعليق ---
async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE, is_auto_trigger: bool = False):
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id) # تأكد من تهيئة البيانات

    # إذا كانت عملية إنشاء الألبوم قد بدأت بالفعل أو لم يكن هناك وسائط، تجاهل الطلب
    if context.user_data.get("album_creation_started", False):
        return ConversationHandler.END # أو لا تفعل شيئًا إذا كانت قادمة من تفعيل تلقائي
    
    # تحقق من عدد عناصر الوسائط
    if len(context.user_data.get("media_queue", [])) < 2:
        if not is_auto_trigger: # لا ترد برسالة إذا كان التفعيل تلقائيا لمنع إزعاج المستخدم
            await context.bot.send_message(
                chat_id=chat_id,
                text=MESSAGES["not_enough_media_items"],
                reply_markup=get_main_keyboard() # إعادة الأزرار الرئيسية
            )
        context.user_data["album_creation_started"] = False
        return ConversationHandler.END

    context.user_data["album_creation_started"] = True # تعيين علامة بدء العملية

    # بناء أزرار اختيار التعليق
    keyboard = []
    for key, text in PREDEFINED_CAPTION_OPTIONS.items():
        keyboard.append([InlineKeyboardButton(text, callback_data=key)])
    
    keyboard.append([InlineKeyboardButton(MESSAGES["album_comment_option_manual"], callback_data=MANUAL_CAPTION_CB_DATA)])
    keyboard.append([InlineKeyboardButton("لا يوجد تعليق", callback_data=NO_CAPTION_CB_DATA)])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])

    # إرسال رسالة طلب التعليق مع InlineKeyboard
    prompt_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["album_caption_prompt"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    # إضافة الرسالة لقائمة الرسائل التي سيتم حذفها لاحقًا
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    return # لا نرجع حالة هنا، المعالج يعالج CallbackQueries


# --- معالجات اختيار التعليق ---
async def handle_predefined_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    caption_key = query.data
    user_caption = PREDEFINED_CAPTION_OPTIONS.get(caption_key, "")
    context.user_data["current_album_caption"] = user_caption

    await finalize_album_action(update, context)

async def handle_no_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["current_album_caption"] = ""
    await finalize_album_action(update, context)

# --- محادثة إدخال التعليق اليدوي ---
async def prompt_for_manual_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # حاول حذف رسالة طلب التعليق (Inline Keyboard)
    try: await query.delete_message() 
    except BadRequest: pass

    # طلب التعليق اليدوي وإزالة ReplyKeyboard مؤقتًا
    prompt_msg = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=MESSAGES["album_caption_manual_prompt"],
        reply_markup=ReplyKeyboardRemove(), # إزالة لوحة المفاتيح الرئيسية هنا
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    return ASKING_FOR_MANUAL_CAPTION

async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_caption = update.message.text
    context.user_data["current_album_caption"] = "" if user_caption == '.' else user_caption
    await finalize_album_action(update, context)
    return ConversationHandler.END


# --- تنفيذ إنشاء الألبوم فعليًا ---
async def finalize_album_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    # حاول حذف رسالة لوحة المفاتيح Inline (في حال وجود Query)
    if update.callback_query:
        try: await update.callback_query.delete_message()
        except BadRequest: pass

    await delete_messages_from_queue(context, chat_id) # حذف الرسائل القديمة

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["processing_album_start"],
    )
    
    await execute_album_creation(update, context) # تنفيذ الإنشاء الفعلي
    
    context.user_data["album_creation_started"] = False
    context.user_data.pop("current_album_caption", None)
    
    # حذف رسالة التقدم وإعادة لوحة المفاتيح الرئيسية
    try: 
        await context.bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="الألبوم جاهز! يمكنك إرسال المزيد من الوسائط.",
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logger.warning(f"Failed to delete progress message or send final message: {e}")


async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يقوم بتقسيم قائمة الوسائط وإنشاء مجموعات الألبومات."""
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    target_chat_id = context.user_data["album_destination_chat_id"]
    album_caption = context.user_data.get("current_album_caption", "")
    split_mode = context.user_data.get("album_split_mode", "equal") # استرجاع النمط المحفوظ، الافتراضي 'equal'
    
    chunks = []
    max_items_per_album = 10 # الحد الأقصى للملفات في ألبوم واحد

    if total_media == 0:
        return

    if split_mode == 'full_10':
        # تقسيم إلى ألبومات بحد أقصى 10 عناصر
        chunks = [media_queue[i:i + max_items_per_album] for i in range(0, total_media, max_items_per_album)]
    else: # split_mode == 'equal' (أو أي شيء آخر كافتراضي)
        # تقسيم الوسائط بالتساوي على أقل عدد ممكن من الألبومات
        # هذا يضمن أن الألبومات تكون متساوية قدر الإمكان حتى لو كان العدد الكلي لا يقسم على 10
        num_albums = math.ceil(total_media / max_items_per_album)
        if num_albums == 0: # في حالة total_media < 10 وما زالت num_albums 0
             num_albums = 1 # لضمان ألبوم واحد على الأقل
        
        base_size = total_media // num_albums
        rem = total_media % num_albums
        
        sizes = [base_size + 1 if i < rem else base_size for i in range(num_albums)]
        
        start_idx = 0
        for size in sizes:
            chunks.append(media_queue[start_idx:start_idx + size])
            start_idx += size

    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            if item["type"] == "photo":
                # إضافة التعليق فقط لأول صورة في أول ألبوم في المجموعة
                input_media.append(InputMediaPhoto(media=item["media"], caption=album_caption if i == 0 and index == 0 else None))
            elif item["type"] == "video":
                # إضافة التعليق فقط لأول فيديو في أول ألبوم في المجموعة
                input_media.append(InputMediaVideo(media=item["media"], caption=album_caption if i == 0 and index == 0 else None))
        
        try:
            await context.bot.send_media_group(chat_id=target_chat_id, media=input_media)
        except TelegramError as e:
            logger.error(f"Failed to send media group: {e}")
            # يمكنك إضافة رسالة خطأ للمستخدم هنا إذا أردت
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ حدث خطأ أثناء إرسال الألبوم: {e}")

        # تأخير بين الألبومات إذا كان هناك أكثر من ألبوم واحد
        if index < len(chunks) - 1:
            await asyncio.sleep(random.randint(5,20)) # تأخير عشوائي لمنع حظر API

    context.user_data["media_queue"] = [] # تفريغ قائمة الوسائط بعد الإنشاء
    logger.info(f"Successfully created {len(chunks)} albums.")


# --- دوال مساعدة ---
async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """تحذف الرسائل التي تم تخزين IDs الخاص بها في قائمة الحذف."""
    message_ids = context.user_data.get("messages_to_delete", [])
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception as e:
            logger.warning(f"Failed to delete message {msg_id}: {e}")
    context.user_data["messages_to_delete"] = []

# --- دوال الإلغاء وإعادة التعيين ---
async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await initialize_user_data(context, update.effective_chat.id)
    context.user_data["media_queue"] = []
    context.user_data["album_creation_started"] = False
    # إعادة الأزرار الرئيسية بعد إعادة التعيين
    await update.message.reply_text(MESSAGES["queue_cleared"], reply_markup=get_main_keyboard())

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يلغي أي عملية حالية ويعيد الأزرار الرئيسية."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    if query:
        await query.answer()
        # حاول حذف الرسالة التي تحتوي على زر الإلغاء (إذا كان زر Inline)
        try: await query.delete_message()
        except BadRequest: pass
    else: # إذا تم استدعاؤها عبر أمر /cancel مثلاً
        # يمكن إرسال رد برسالة "تم الإلغاء" هنا
        pass
    
    # تفريغ قائمة الوسائط والحالات
    context.user_data["media_queue"] = []
    context.user_data["album_creation_started"] = False
    context.user_data.pop("current_album_caption", None)
    context.user_data["messages_to_delete"] = [] # تأكد من مسح الرسائل للحذف

    # إرسال رسالة إلغاء وإعادة لوحة المفاتيح الرئيسية
    await context.bot.send_message(
        chat_id=chat_id, 
        text=MESSAGES["cancel_operation"], 
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END # إنهاء أي ConversationHandler حالي


# --- دوال تغيير نمط التقسيم ---
async def change_split_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعالج الضغط على زر "تغيير نمط التقسيم" ويعرض خيارات inline."""
    chat_id = update.effective_chat.id

    keyboard = [
        [InlineKeyboardButton(MESSAGES["split_mode_name_equal"], callback_data=f"{SPLIT_MODE_CB_PREFIX}equal")],
        [InlineKeyboardButton(MESSAGES["split_mode_name_full_10"], callback_data=f"{SPLIT_MODE_CB_PREFIX}full_10")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # إرسال رسالة بطلب اختيار نمط التقسيم
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["split_mode_prompt"],
        reply_markup=reply_markup,
    )
    # إضافة الرسالة لقائمة الرسائل التي سيتم حذفها لاحقًا
    context.user_data.get("messages_to_delete", []).append(msg.message_id)
    return CHANGING_SPLIT_MODE # ندخل في حالة ConversationHandler الجديدة

async def set_split_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """يعالج اختيار المستخدم لنمط التقسيم من InlineKeyboard."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    chosen_mode = query.data.replace(SPLIT_MODE_CB_PREFIX, "")
    context.user_data["album_split_mode"] = chosen_mode # حفظ النمط في بيانات المستخدم

    # عرض اسم النمط المناسب للمستخدم
    mode_name_display = MESSAGES["split_mode_name_equal"] if chosen_mode == "equal" else MESSAGES["split_mode_name_full_10"]
    
    # تعديل رسالة الأزرار لعرض التأكيد وإزالة الأزرار Inline
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=query.message.message_id,
        text=MESSAGES["split_mode_set_success"].format(split_mode_name=mode_name_display),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=None # إزالة أزرار Inline بعد الاختيار
    )
    
    # بعد الاختيار، يجب أن تظهر لوحة المفاتيح الرئيسية مرة أخرى
    await context.bot.send_message(
        chat_id=chat_id, 
        text="تم تعيين نمط التقسيم.", 
        reply_markup=get_main_keyboard()
    )
    context.user_data.get("messages_to_delete", []).clear() # مسح قائمة الرسائل للحذف
    return ConversationHandler.END # إنهاء محادثة تغيير النمط


# --- الأوامر الأساسية (start, help) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل رسالة الترحيب مع الأزرار الرئيسية."""
    await update.message.reply_text(
        MESSAGES["greeting"].format(username=update.effective_user.username),
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يرسل رسالة المساعدة مع الأزرار الرئيسية."""
    await update.message.reply_text(MESSAGES["help"], reply_markup=get_main_keyboard())


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        raise ValueError("BOT_TOKEN environment variable not set.") # ارفع خطأ لإيقاف التشغيل إذا لم يكن هناك توكن
    
    application = Application.builder().token(token).build()

    # --- محادثات ConversationHandler ---
    # محادثة اختيار التعليق اليدوي
    manual_caption_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(prompt_for_manual_caption, pattern=f"^{MANUAL_CAPTION_CB_DATA}$")],
        states={
            ASKING_FOR_MANUAL_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption)],
        },
        fallbacks=[CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"), CommandHandler("cancel", cancel_operation)],
        allow_reentry=True # تسمح بالدخول للمحادثة حتى لو كانت فعالة
    )

    # محادثة تغيير نمط التقسيم
    split_mode_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_split_mode'])}$"), change_split_mode)
        ],
        states={
            CHANGING_SPLIT_MODE: [
                CallbackQueryHandler(set_split_mode, pattern=f"^{SPLIT_MODE_CB_PREFIX}.+$"),
            ]
        },
        fallbacks=[CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"), CommandHandler("cancel", cancel_operation)],
        allow_reentry=True
    )


    # --- إضافة المعالجات العامة للـ Application ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # معالجات أزرار ReplyKeyboard
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$"), start_album_creation_process))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$"), reset_album))
    # Note: the 'change_split_mode' button is now handled by split_mode_conv's entry_points

    # إضافة ConversationHandlers
    application.add_handler(manual_caption_conv)
    application.add_handler(split_mode_conv) 

    # معالجات أزرار InlineKeyboard (للتعليقات والإلغاء)
    application.add_handler(CallbackQueryHandler(handle_predefined_caption_choice, pattern=r"^cap_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_no_caption_choice, pattern=f"^{NO_CAPTION_CB_DATA}$"))
    application.add_handler(CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"))
    
    # معالجات الوسائط (صور وفيديوهات)
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

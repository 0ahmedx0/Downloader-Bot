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
# INITIATING_ALBUM_AUTO = 0 # تم إزالة هذه الحالة لأننا لم نعد نستخدمها بهذه الطريقة
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
        'فقط قم بتحويل أو إرسال صور وفيديوهات متعددة. عندما تنتهي، اضغط على زر "إنشاء ألبوم" '
        'وستحصل على جميع ملفاتك التي أرسلتها مسبقاً مجمعة كألبومات. إذا أخطأت، انقر على "إعادة تعيين الألبوم" للبدء من جديد.\n\n'
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
    "album_comment_option_manual": "إدخال تعليق يدوي",
    "invalid_input_choice": "خيار غير صالح أو إدخال غير متوقع. الرجاء الاختيار من الأزرار أو إلغاء العملية.",
    "success_message_permanent_prompt": "يمكنك الآن إرسال المزيد من الوسائط أو استخدام الأزرار أدناه.",
    "ask_split_mode_setting": "اختر نمط تقسيم الألبوم الافتراضي. سيتم استخدامه لكل الألبومات القادمة حتى تغييره مرة أخرى.",
    "split_mode_set_success": "👍 تم تعيين نمط تقسيم الألبومات إلى: *{split_mode_name}*.",
    "album_split_mode_full": "ألبومات كاملة (10 عناصر)",
    "album_split_mode_equal": "تقسيم متساوي",
    "auto_album_prompt": "مستعد لإنشاء ألبوم! الرجاء اختيار تعليق:",
}

# التعليقات الجاهزة
PREDEFINED_CAPTION_OPTIONS = [
    "عرض ورعان اجانب 🌈💋", "🌈 🔥 .", "حصريات منوع🌈🔥.", "حصريات🌈",
    "عربي منوع🌈🔥.", "اجنبي منوع🌈🔥.", "عربي 🌈🔥.", "اجنبي 🌈🔥.",
    "منوعات 🌈🔥.", "حصريات عربي 🌈🔥.", "حصريات اجنبي 🌈🔥.",
    "لا يوجد تعليق", MESSAGES["album_comment_option_manual"],
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
    defaults = {
        "media_queue": [],
        "messages_to_delete": [],
        "temp_messages_to_clean": [],
        "progress_message_id": None,
        "album_split_mode": "equal", # "equal" أو "full_10"
        "album_split_mode_name": MESSAGES["album_split_mode_equal"],
        # "auto_album_timer": None # لم نعد بحاجة إلى هذا لـ JobQueue
    }
    for key, value in defaults.items():
        if key not in context.user_data:
            context.user_data[key] = value if not isinstance(value, list) else list(value)
    
    # تعيين وجهة الإرسال تلقائيًا إلى الدردشة الحالية دائمًا
    context.user_data["album_destination_chat_id"] = chat_id
    context.user_data["album_destination_name"] = "هذه المحادثة"

# دالة بناء لوحة المفاتيح الرئيسية
def get_main_reply_markup() -> ReplyKeyboardMarkup:
    # تم إزالة زر تغيير الوجهة
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
        except BadRequest: # Message already deleted
            pass
        except Exception as e:
            logger.warning(f"Could not delete message {msg_id} in chat {chat_id}: {e}")
    context.user_data["messages_to_delete"].clear()

# الأوامر الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)
    
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)
    await update.message.reply_text(message, reply_markup=get_main_reply_markup())
    
    # إرسال رسالة تأكيد بأن الوجهة تم تحديدها تلقائيًا
    await update.message.reply_text(MESSAGES["destination_set_success"])

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["help"])

# -------------------------------------------------------------
# دالة موحدة لإضافة الوسائط وبدء المؤقت
# -------------------------------------------------------------
async def add_media_and_schedule_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await initialize_user_data(context, chat_id)

    if media_type == "photo":
        file_id = update.message.photo[-1].file_id
    elif media_type == "video":
        file_id = update.message.video.file_id
    else:
        return

    context.user_data["media_queue"].append({"type": media_type, "media": file_id})
    logger.info(f"Added {media_type}. Queue size: {len(context.user_data['media_queue'])}")

    job_name = f"auto_album_prompt_{chat_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        logger.info(f"Cancelled existing auto album job for {chat_id}.")
    
    if len(context.user_data["media_queue"]) >= 1: 
        context.job_queue.run_once(
            callback=timeout_callback_auto_album_entry,
            when=2,  # هذا هو الزمن الذي يمكنك تعديله
            name=job_name,
            chat_id=chat_id,
            user_id=user_id,
            data={"chat_id": chat_id, "user_id": user_id},
        )
        logger.info(f"Scheduled new auto album prompt job for chat {chat_id} in 2 seconds.")


# -------------------------------------------------------------
# دوال ConversationHandler (لتغيير نمط التقسيم)
# -------------------------------------------------------------
async def prompt_for_split_mode_setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تطلب من المستخدم اختيار إعداد التقسيم الدائم."""
    keyboard = [
        [InlineKeyboardButton(MESSAGES["album_split_mode_full"], callback_data=f"{SPLIT_SET_CB_PREFIX}full_10")],
        [InlineKeyboardButton(MESSAGES["album_split_mode_equal"], callback_data=f"{SPLIT_SET_CB_PREFIX}equal")],
        [InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)]
    ]
    if update.callback_query:
        await update.callback_query.answer()
        prompt_msg = await update.callback_query.message.reply_text(MESSAGES["ask_split_mode_setting"], reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        prompt_msg = await update.message.reply_text(MESSAGES["ask_split_mode_setting"], reply_markup=InlineKeyboardMarkup(keyboard))
    
    context.user_data.get("messages_to_delete", []).append(prompt_msg.message_id)
    return CHANGING_SPLIT_MODE

async def handle_split_mode_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تستقبل اختيار المستخدم وتخزنه كإعداد دائم."""
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
        await context.bot.send_message(query.message.chat_id, MESSAGES["split_mode_set_success"].format(split_mode_name=mode_name), parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_reply_markup())

    return ConversationHandler.END

# -------------------------------------------------------------
# دوال ConversationHandler (لإنشاء الألبوم)
# -------------------------------------------------------------

async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الأولى لإنشاء الألبوم (يتم تشغيلها بالزر اليدوي): تتحقق من كل شيء وتطلب التعليق.
    """
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)
    
    # ألغِ المؤقت التلقائي إذا ضغط المستخدم على زر "إنشاء ألبوم" يدويًا
    job_name = f"auto_album_prompt_{chat_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        logger.info(f"Manual 'Done' button pressed, cancelled auto-album job for {chat_id}.")

    if len(context.user_data["media_queue"]) < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"], reply_markup=get_main_reply_markup())
        return ConversationHandler.END

    return await prompt_for_album_caption(update, context, auto_prompt=False) # ليست مطالبة تلقائية

async def prompt_for_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE, auto_prompt: bool = False) -> int:
    """
    دالة موحدة لطلب تعليق الألبوم، سواء بالضغط على زر "Done" أو تلقائيًا بعد 2 ثانية.
    """
    chat_id = update.effective_chat.id
    keyboard = []
    for i, caption in enumerate(PREDEFINED_CAPTION_OPTIONS):
        keyboard.append([InlineKeyboardButton(caption, callback_data=f"{CAPTION_CB_PREFIX}{i}")])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])
    
    prompt_message = MESSAGES["album_caption_prompt"]
    if auto_prompt:
        prompt_message = MESSAGES["auto_album_prompt"] # رسالة خاصة للبدء التلقائي

    await context.bot.send_chat_action(chat_id=chat_id, action="typing") # إشارة أن البوت يعمل
    
    prompt_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=prompt_message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)
    
    return ASKING_FOR_CAPTION


async def handle_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    choice = query.data
    await query.answer()
    try: await query.delete_message() # Delete the caption choice message
    except BadRequest: pass

    if choice == CANCEL_CB_DATA:
        await cancel_album_creation(update, context)
        return ConversationHandler.END
    
    caption_index = int(choice.replace(CAPTION_CB_PREFIX, ""))
    selected_option = PREDEFINED_CAPTION_OPTIONS[caption_index]

    if selected_option == MESSAGES["album_comment_option_manual"]:
        prompt_msg = await context.bot.send_message(query.message.chat_id, MESSAGES["album_caption_manual_prompt"], reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.MARKDOWN)
        context.user_data["messages_to_delete"].append(prompt_msg.message_id)
        return ASKING_FOR_MANUAL_CAPTION
    
    user_caption = "" if selected_option == "لا يوجد تعليق" else selected_option
    context.user_data["current_album_caption"] = user_caption
    
    # Confirm caption and proceed to album creation
    confirm_message = (MESSAGES["album_caption_confirm"].format(caption=user_caption) 
                       if user_caption else MESSAGES["album_caption_confirm_no_caption"])
    await context.bot.send_message(query.message.chat_id, confirm_message, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_reply_markup())

    return await finalize_album_action(update, context)

async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_caption = update.message.text
    context.user_data["current_album_caption"] = "" if user_caption == '.' else user_caption
    
    # Confirm caption and proceed to album creation
    confirm_message = (MESSAGES["album_caption_confirm"].format(caption=context.user_data["current_album_caption"]) 
                       if context.user_data["current_album_caption"] else MESSAGES["album_caption_confirm_no_caption"])
    await update.message.reply_text(confirm_message, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_reply_markup())

    return await finalize_album_action(update, context)

async def finalize_album_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    await delete_messages_from_queue(context, chat_id) # Clean up any prior messages like caption prompts

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["processing_album_start"],
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["progress_message_id"] = progress_msg.message_id

    await execute_album_creation(update, context)

    context.user_data.pop("current_album_caption", None)
    
    progress_msg_id = context.user_data.pop("progress_message_id", None)
    if progress_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
        except Exception:
            pass
    
    # Send a final success message and show the main keyboard
    await context.bot.send_message(chat_id=chat_id, text=MESSAGES["success_message_permanent_prompt"], reply_markup=get_main_reply_markup())

    return ConversationHandler.END

# -------------------------------------------------------------
# دوال التنفيذ والإلغاء
# -------------------------------------------------------------

async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    user_chat_id = update.effective_chat.id
    target_chat_id = context.user_data["album_destination_chat_id"] # سيتم تعيينها دائمًا
    album_caption = context.user_data.get("current_album_caption", "")
    
    split_mode = context.user_data.get("album_split_mode", "equal")
    logger.info(f"Creating album. Media: {total_media}, Split mode: {split_mode}")

    chunks = []
    max_items_per_album = 10
    if split_mode == 'full_10':
        chunks = [media_queue[i:i + max_items_per_album] for i in range(0, total_media, max_items_per_album)]
    else: # equal split
        if total_media > 0:
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

    context.user_data["media_queue"] = [] # Clear the queue after successful creation

async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)
    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)
    
    # ألغِ أي مؤقت تلقائي قيد التشغيل
    job_name = f"auto_album_prompt_{chat_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        logger.info(f"Resetting queue, cancelled auto-album job for {chat_id}.")

    await update.message.reply_text(MESSAGES["queue_cleared"], reply_markup=get_main_reply_markup())
    
    # تأكد من إنهاء أي محادثات جارية تخص الألبوم
    # Note: Accessing album_creation_conv here might be tricky if it's not global/passed correctly
    # But usually, if it's defined at the module level before main, it's accessible.
    if context.user_data.get("_conversation_state", {}).get("album_creation_conv"): # Use string name for robustness
         context.user_data["_conversation_state"]["album_creation_conv"] = ConversationHandler.END


async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.answer()
        chat_id = update.callback_query.message.chat_id
        try: await update.callback_query.message.delete() # delete the message with buttons
        except: pass
    elif update.message:
        chat_id = update.effective_chat.id
        # We don't delete the user's /cancel command.
    
    await delete_messages_from_queue(context, chat_id) # Clean up temporary bot messages

    # Clear queue and any pending caption
    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)

    # Cancel auto album timer if active
    job_name = f"auto_album_prompt_{chat_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        logger.info(f"Cancelled operation, cancelled auto-album job for {chat_id}.")

    text, markup = (MESSAGES["cancel_operation"], get_main_reply_markup())
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    
    # تأكد من إنهاء أي محادثات جارية تخص الألبوم
    if context.user_data.get("_conversation_state", {}).get("album_creation_conv"): # Use string name for robustness
         context.user_data["_conversation_state"]["album_creation_conv"] = ConversationHandler.END

    return ConversationHandler.END

cancel_album_creation = cancel_operation
cancel_operation_general = cancel_operation


async def timeout_callback_auto_album_entry(context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    هذا هو المعالج الذي سيتم تشغيله بواسطة مهمة المؤقت.
    يجب أن يتوافق مع نقطة دخول ConversationHandler.
    """
    # الوصول إلى chat_id و user_id من job.data
    chat_id = context.job.data.get("chat_id")
    user_id = context.job.data.get("user_id")

    if not chat_id or not user_id:
        logger.error("Missing chat_id or user_id in job data for auto-album.")
        return ConversationHandler.END
    
    # للتأكد من تهيئة user_data بشكل صحيح في سياق المهمة
    await initialize_user_data(context, chat_id)

    # Check if there's enough media and if the conversation is not already active
    # For a timed auto-prompt, if there's 1 item, we can prompt, the system will later check for >= 2 for album creation.
    if context.user_data.get("media_queue") and len(context.user_data["media_queue"]) >= 1:
        # Access conversation state by its string name used in add_handler
        current_state = context.user_data.get("_conversation_state", {}).get("album_creation_conv")
        
        # إذا لم يكن المستخدم في محادثة إنشاء ألبوم حاليًا
        if current_state is None or current_state == ConversationHandler.END:
            logger.info(f"Timeout triggered for auto album creation in chat {chat_id}. Prompting for caption.")
            
            # إنشاء Update وهمي لتحقيق التوافق مع ConversationHandler
            dummy_update = Update(update_id=random.randint(100000, 999999))
            dummy_update._effective_chat = type('obj', (object,), {'id': chat_id, 'type': 'private'})()
            dummy_update._effective_user = type('obj', (object,), {'id': user_id, 'first_name': 'BotUser'})()
            
            # يرجى ملاحظة: هنا سنطلب التعليق، وسيعيد `prompt_for_album_caption` `ASKING_FOR_CAPTION`.
            # وظائف JobQueue لا تُعيد حاليًا للمحادثة، لذا سنضطر إلى التعديل يدويًا هنا.
            # لا يمكننا إعادة `ConversationHandler.END` أو أي حالة مباشرة من `JobQueue` Callback.
            # فقط استدعاء الدالة.

            # We need to manually set the conversation state for this chat_id before calling the prompt.
            # The ConversationHandler automatically manages it if the entry_point is called through the dispatcher.
            # But since JobQueue is calling it, we need to explicitly put the chat in the right state.
            
            # To enter the ConversationHandler properly from a Job,
            # it's usually cleaner to trigger one of its entry_points.
            # However, since `timeout_callback_auto_album_entry` is already the callback,
            # we'll adapt. The main challenge is setting the correct state.

            # We will rely on `prompt_for_album_caption` to handle the actual sending
            # and then `handle_caption_choice` will manage the state properly via callbacks.
            
            # The *most robust* way for this setup is to ensure that when `timeout_callback_auto_album_entry`
            # executes, it leads to `prompt_for_album_caption` being called, and then that the `ConversationHandler`
            # is aware of the state.

            # The current way: `prompt_for_album_caption` gets a `dummy_update`. It sends the message.
            # The user interacts with the message, triggering `handle_caption_choice`.
            # `handle_caption_choice` IS part of the conversation handler states.
            # So, the ConversationHandler will pick it up from there. This is viable.
            
            # A key point: `timeout_callback_auto_album_entry` must not return a state for the ConversationHandler.
            # It's a job, not a handler directly managing conversation flow for `ConversationHandler`.
            # We are *initiating* the conversation.

            # We need to ensure that the context for this specific chat_id is updated for the conversation.
            # Python-telegram-bot often uses `context.application.dispatcher.process_update(dummy_update)`
            # to make a dummy update enter the "normal" flow, which then triggers handlers including conv.
            # However, directly calling handlers that set state can work if handled carefully.

            try:
                # Manually set the state in user_data, mimicking ConversationHandler entry
                # This is an important step when triggering conversation states outside direct `Dispatcher` flow.
                # It tells the ConversationHandler where this user *is*.
                context.user_data["_conversation_state"]["album_creation_conv"] = ASKING_FOR_CAPTION
                
                await prompt_for_album_caption(dummy_update, context, auto_prompt=True)

            except Exception as e:
                logger.error(f"Error in auto-prompting for album: {e}")
                # Reset conversation state if something went wrong
                if context.user_data.get("_conversation_state", {}).get("album_creation_conv"):
                    context.user_data["_conversation_state"]["album_creation_conv"] = ConversationHandler.END

        else:
            logger.info(f"Auto-album job fired, but conversation for chat {chat_id} is already in state {current_state}. Skipping auto-prompt.")
    else:
        logger.info(f"Auto-album job fired for chat {chat_id}, but not enough media ({len(context.user_data.get('media_queue', []))} items) or queue cleared. Skipping auto-prompt.")
    
    # context.user_data["auto_album_timer"] = None # لم نعد بحاجة لهذا لـ JobQueue


# تشغيل البوت
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        return
    
    application = Application.builder().token(token).build()

    # محادثة لتغيير نمط التقسيم
    split_mode_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_split_mode'])}$"), prompt_for_split_mode_setting)],
        states={CHANGING_SPLIT_MODE: [CallbackQueryHandler(handle_split_mode_choice, pattern=f"^{SPLIT_SET_CB_PREFIX}.*|^{CANCEL_CB_DATA}$")]},
        fallbacks=[CommandHandler("cancel", cancel_operation_general)]
    )

    # محادثة لإنشاء الألبوم
    album_creation_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$"), start_album_creation_process),
            # NO new entry points for auto-prompt, as JobQueue directly calls the handler and we set state.
        ],
        states={
            ASKING_FOR_CAPTION: [CallbackQueryHandler(handle_caption_choice, pattern=f"^{CAPTION_CB_PREFIX}.*|^{CANCEL_CB_DATA}$")],
            ASKING_FOR_MANUAL_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption)],
        },
        fallbacks=[CommandHandler("cancel", cancel_album_creation)],
        name="album_creation_conv" # مهم لتتبع حالة المحادثة
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # إضافة المعالجات المتبقية
    application.add_handler(split_mode_conv)
    application.add_handler(album_creation_conv)
    
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$"), reset_album))
    
    # الآن سنقوم بربط معالجات الصور والفيديوهات بالدالة الموحدة الجديدة
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, lambda u, c: add_media_and_schedule_prompt(u, c, "photo")))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, lambda u, c: add_media_and_schedule_prompt(u, c, "video")))


    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

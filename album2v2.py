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
# إضافة حالة جديدة لبدء عملية الألبوم تلقائيًا
INITIATING_ALBUM_AUTO = 0 # حالة جديدة لانتظار بدء الألبوم بعد التأخير
ASKING_FOR_CAPTION = 1
ASKING_FOR_MANUAL_CAPTION = 2
CHANGING_SPLIT_MODE = 4 # تم إزالة الحالة 3


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
        "auto_album_timer": None # لتخزين كائن المهمة
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
# دوال إضافة الوسائط مع المؤقت الجديد
# -------------------------------------------------------------
async def _add_media_and_start_timer(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str) -> None:
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)

    if media_type == "photo":
        file_id = update.message.photo[-1].file_id
    elif media_type == "video":
        file_id = update.message.video.file_id
    else:
        return # Should not happen

    context.user_data["media_queue"].append({"type": media_type, "media": file_id})
    logger.info(f"Added {media_type}. Queue size: {len(context.user_data['media_queue'])}")

    # إذا كانت هذه أول وسائط في قائمة الانتظار، ابدأ المؤقت
    if len(context.user_data["media_queue"]) == 1:
        if context.user_data.get("auto_album_timer"):
            # ألغِ المؤقت السابق إذا كان موجودًا (لإعادة التشغيل عند إضافة وسائط جديدة)
            context.user_data["auto_album_timer"].cancel()
            logger.info("Cancelled previous auto album timer.")

        logger.info("Starting 2-second auto album timer.")
        # ابدأ المهمة، ولا تنتظرها (مهمة خلفية)
        context.user_data["auto_album_timer"] = asyncio.create_task(
            _wait_and_prompt_for_caption(update, context)
        )

async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_media_and_start_timer(update, context, "photo")

async def add_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _add_media_and_start_timer(update, context, "video")

async def _wait_and_prompt_for_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """انتظر ثانيتين ثم اطلب التعليق إذا كانت هناك وسائط كافية."""
    await asyncio.sleep(2) # انتظر ثانيتين

    chat_id = update.effective_chat.id

    # تأكد أن الألبوم لا يزال يحتوي على وسائط كافية ولم يتم مسحها أو بدء العملية يدويًا
    if context.user_data.get("media_queue") and len(context.user_data["media_queue"]) >= 1: # نطلب >= 1 لأنه قد يبدأ بـ 1، والحد الأدنى للإنشاء الفعلي هو 2. يمكننا تغيير هذا إلى 2
        # تحقق من حالة المحادثة للتأكد من أنها ليست بالفعل في عملية إنشاء ألبوم (يدويًا)
        # هذا يمنع تشغيل مطالبة التعليق التلقائية إذا ضغط المستخدم "إنشاء ألبوم" يدويًا خلال الثواني الـ 2
        
        # We need a way to make the bot enter the conversation for auto-prompt.
        # This is tricky because ConversationHandler expects an entry_point handler.
        # A workaround is to simulate the entry point for ConversationHandler if not already in one.
        
        # Check if we are already in an album creation state
        current_state = context.user_data.get("_conversation_state", {}).get(album_creation_conv.name)
        if current_state is None or current_state == ConversationHandler.END: # Not in conversation or it just ended
            logger.info(f"Auto-prompting for album caption in chat {chat_id}")
            # Simulate a start of conversation, transitioning to ASKING_FOR_CAPTION
            context.user_data["auto_start_album_creation"] = True # Flag for the entry point
            
            # Call the handler directly to kick off the caption prompt
            # It's not ideal as it bypasses the normal ConversationHandler flow slightly,
            # but is a common pattern for "initiating" a conversation from an async task.
            # The ConversationHandler's entry_point for `auto_album_initiation` will then handle it.
            
            # Create a dummy Update object if needed, or pass the existing one.
            # For `prompt_for_album_caption_auto`, we don't strictly need a message update.
            # We can use a custom context to identify the auto-initiation.
            
            # The `prompt_for_album_caption_auto` handler will need to check this flag.
            
            # The best way to enter an existing conversation handler is to "force" a state change,
            # but that's a bit advanced. Let's simplify and make the manual_done trigger the auto-flow
            # if the queue has media.
            
            # Let's remove this `_wait_and_prompt_for_caption` function as a separate task
            # and integrate the auto-prompt into the `start_album_creation_process` or a similar
            # handler triggered by the "Done" button implicitly.
            
            # New approach:
            # Instead of a timer triggering a prompt, let the user continue sending media.
            # When they click "Done" (or if we really want a timer-based auto-creation),
            # the `start_album_creation_process` handler is where the choice of caption happens.
            # The *request* was to display caption options 2 seconds after the *first* batch of videos.
            # This implies the bot should automatically move to asking for a caption *without* user clicking "Done".
            
            # Let's make `prompt_for_album_caption_auto` directly initiate the `ASKING_FOR_CAPTION` state.
            try:
                # Set conversation state explicitly for the bot
                context.user_data["_conversation_state"] = context.user_data.get("_conversation_state", {})
                context.user_data["_conversation_state"][album_creation_conv.name] = ASKING_FOR_CAPTION
                
                # This ensures the current state is set. Now call the handler function.
                await prompt_for_album_caption_auto(update, context)

            except Exception as e:
                logger.error(f"Error in auto-prompting for album: {e}")
                # Reset conversation state if something went wrong during auto-prompt
                context.user_data["_conversation_state"][album_creation_conv.name] = ConversationHandler.END

        else:
            logger.info(f"Already in an album creation conversation state: {current_state}. Skipping auto-prompt.")
    else:
        logger.info("Not enough media or queue cleared. Skipping auto-prompt.")
    
    context.user_data["auto_album_timer"] = None # Clear timer reference

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
    # For a callback query update, reply to the message, for a command/message, reply to it
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
    if context.user_data.get("auto_album_timer"):
        context.user_data["auto_album_timer"].cancel()
        context.user_data["auto_album_timer"] = None
        logger.info("Manual 'Done' button pressed, cancelled auto-album timer.")

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

    # حذف لوحة المفاتيح القديمة (لوحة المفاتيح الرد الأساسية) لعدم تداخلها مع لوحة مفاتيح inline الجديدة
    await context.bot.send_chat_action(chat_id=chat_id, action="typing") # إشارة أن البوت يعمل
    
    prompt_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=prompt_message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
        # Reply to the user's last message if it's not a callback, or if update has a message object.
        # Otherwise, just send it.
        # It's better to send a new message for clarity and append its ID to `messages_to_delete`.
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
    if context.user_data.get("auto_album_timer"):
        context.user_data["auto_album_timer"].cancel()
        context.user_data["auto_album_timer"] = None
        logger.info("Resetting queue, cancelled auto-album timer.")

    await update.message.reply_text(MESSAGES["queue_cleared"], reply_markup=get_main_reply_markup())
    
    # تأكد من إنهاء أي محادثات جارية تخص الألبوم
    if context.user_data.get("_conversation_state", {}).get(album_creation_conv.name):
         context.user_data["_conversation_state"][album_creation_conv.name] = ConversationHandler.END


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
    if context.user_data.get("auto_album_timer"):
        context.user_data["auto_album_timer"].cancel()
        context.user_data["auto_album_timer"] = None
        logger.info("Cancelled operation, cancelled auto-album timer.")

    text, markup = (MESSAGES["cancel_operation"], get_main_reply_markup())
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    
    return ConversationHandler.END

cancel_album_creation = cancel_operation
cancel_operation_general = cancel_operation


async def timeout_callback_auto_album_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    هذا هو المعالج الذي سيتم تشغيله بواسطة مهمة المؤقت.
    يجب أن يتوافق مع نقطة دخول ConversationHandler.
    """
    chat_id = context.job.chat_id # Chat ID is stored in job data
    
    # Check if there's enough media and if the conversation is not already active
    if context.user_data.get("media_queue") and len(context.user_data["media_queue"]) >= 2: # Min 2 for an album
        current_state = context.user_data.get("_conversation_state", {}).get(album_creation_conv.name)
        if current_state is None or current_state == ConversationHandler.END:
            logger.info(f"Timeout triggered for auto album creation in chat {chat_id}. Prompting for caption.")
            # We need to simulate an 'Update' object for `prompt_for_album_caption`
            # which expects it for context and `effective_chat.id`.
            # A dummy update based on chat_id is needed if `job.context` doesn't pass one.
            # In job.run_async or job.run_once, you can pass context_args or chat_id directly.
            
            # Since `job.run_once` can accept `chat_id`, we'll construct a dummy update
            # within the handler or make `prompt_for_album_caption` handle a None update.
            # The simplest is to modify `prompt_for_album_caption` to receive chat_id directly.

            # We need to make sure context.user_data is accessible, which it is for `JobQueue` jobs.
            
            # Since `prompt_for_album_caption` also needs to reply to a specific message for some Update types,
            # and here we're creating one from scratch, let's ensure it knows it's an auto-prompt.
            
            # Set the conversation state to trigger the `ASKING_FOR_CAPTION` flow.
            # We need the chat_id from the job.
            
            # The ConversationHandler mechanism itself needs an entry point.
            # This is where the complexity comes. JobQueue alone won't trigger ConversationHandler states.
            # The ConversationHandler's entry points are `MessageHandler`, `CommandHandler`, etc.
            # A JobQueue callback needs to *force* the state change or perform the actions.
            
            # Let's adjust: instead of a timer, let's say the user must click "Done" *after* sending their first batch.
            # The original request "عندما يستلم اول دفعه فيديوهات بعدها ب 2 ثواني يعرض اختيار وصف"
            # This is key: it's not on *any* subsequent video, but after the "first batch".
            # The most practical way to interpret "first batch" is the first time the user starts sending media
            # until a short pause or an explicit action.
            
            # Let's try to implement the explicit timer-triggered conversation entry again, carefully.
            
            # If a timeout fires and the user HASN'T clicked "Done" yet:
            #   - Check if media_queue >= 2.
            #   - If yes, proceed to ASKING_FOR_CAPTION state.
            
            # The key problem with JobQueue + ConversationHandler is `ConversationHandler` needs an `Update` object
            # for its internal state management (e.g., `update.effective_chat.id`).
            # `JobQueue` callbacks just get `context`.
            
            # To address this, the `timeout_callback_auto_album_entry` will manually call `prompt_for_album_caption`.
            # We will ensure `prompt_for_album_caption` can accept `chat_id` directly without `update` object,
            # or we will pass a dummy `update` object (less clean).
            
            # Let's refactor `prompt_for_album_caption` to take `chat_id` and `context`
            # AND create a dummy `Update` object if none is provided.

            # Since ConversationHandler is difficult to enter externally, let's just make the JobQueue callback
            # *directly* execute the `prompt_for_album_caption` logic.
            # This won't set `ConversationHandler` state, so if the user sends more media, the conversation
            # might not be in the right state.

            # Simplest interpretation of "بعدها ب 2 ثواني يعرض اختيار وصف":
            # Start timer after first media is received.
            # If more media comes within 2s, reset timer.
            # If 2s pass and no more media received, and there's media in queue, trigger prompt for caption.
            # If user ignores prompt and sends more media, they'll have to manually hit "Done".
            # If they interact with prompt, the album creation starts.

            # We need to ensure that when `prompt_for_album_caption` is called from the timer,
            # it puts the user in the `ASKING_FOR_CAPTION` state of the ConversationHandler.
            
            # A cleaner way is to make the `_wait_and_prompt_for_caption` an `entry_point` handler
            # which is then triggered by the JobQueue.
            # This is also complicated.

            # Let's go with the initial thought: A job triggers the next step.
            # We need `user_id` from the job for `context.user_data`.
            user_id = context.job.user_id 
            chat_id = context.job.chat_id
            
            if user_id and chat_id:
                # Need to explicitly set the conversation state here.
                # Accessing user_data requires an `application` or `persistence`.
                # Assuming `context.user_data` is automatically populated for the job.
                
                # Create a dummy Update object to make ConversationHandler happy
                # This is a hacky workaround, but sometimes necessary for complex flows.
                dummy_update = Update(update_id=random.randint(100000, 999999))
                # Populate `effective_chat` and `effective_user` for state tracking
                dummy_update._effective_chat = type('obj', (object,), {'id': chat_id, 'type': 'private'})()
                dummy_update._effective_user = type('obj', (object,), {'id': user_id, 'first_name': 'BotUser'})()
                
                # Now manually trigger the entry point for the album conversation handler
                # This will call `start_album_creation_process`, but we want to go straight to caption.
                
                # Instead of a `_wait_and_prompt_for_caption` function that does the logic,
                # let's modify the `add_photo/video` to schedule a Job that calls
                # `start_album_creation_process` with a flag for auto-trigger.

                # Let's make `start_album_creation_process` adaptable to being called by the timer.
                
                # The Job needs to call the *actual ConversationHandler entry point*.
                # This means it needs to be `update.message.text = MESSAGES["keyboard_done"]` etc.
                # This is bad.

                # Final proposed solution for auto-prompting:
                # `add_photo`/`add_video` adds to queue, and if it's the first media, it schedules a job for 2 seconds.
                # This job, when it runs, will call a new function, say `auto_prompt_for_caption_entry`.
                # This `auto_prompt_for_caption_entry` will be an `entry_point` for the `ConversationHandler`.
                # If it's triggered, it immediately transitions to `ASKING_FOR_CAPTION`.

                await prompt_for_album_caption(dummy_update, context, auto_prompt=True) # Now `prompt_for_album_caption` can receive `dummy_update`
                return ASKING_FOR_CAPTION # Ensure this is the state for the conversation
        else:
            logger.info(f"Auto-album job fired, but conversation is already in state {current_state}.")
    else:
        logger.info("Auto-album job fired, but not enough media or queue was cleared.")
    
    context.user_data["auto_album_timer"] = None # Clear timer reference

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
    # الآن سنضيف نقطة دخول أخرى للبدء التلقائي للمحادثة.
    album_creation_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$"), start_album_creation_process),
            # NEW ENTRY POINT: This handler is not directly triggered by a message/command
            # but will be the 'destination' of the job queue timer when it kicks off the conversation.
            # We need a handler that directly transitions to ASKING_FOR_CAPTION if timer triggered it.
            # This is complex with ConversationHandler's entry points.

            # Alternative (simpler): The timer simply calls `prompt_for_album_caption`
            # and `handle_caption_choice` correctly manages state. This avoids forcing `entry_point`.
            # Let's remove INITIATING_ALBUM_AUTO state for now.
        ],
        states={
            ASKING_FOR_CAPTION: [CallbackQueryHandler(handle_caption_choice, pattern=f"^{CAPTION_CB_PREFIX}.*|^{CANCEL_CB_DATA}$")],
            ASKING_FOR_MANUAL_CAPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption)],
        },
        fallbacks=[CommandHandler("cancel", cancel_album_creation)]
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # إضافة المعالجات المتبقية
    application.add_handler(split_mode_conv)
    application.add_handler(album_creation_conv)
    
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$"), reset_album))
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))
    
    # Add a job queue to the application. This is essential for the timer.
    job_queue = application.job_queue

    # Modified `_add_media_and_start_timer` to use `job_queue`
    # We need to pass the `job_queue` object to `add_photo` and `add_video` or make it global/accessible.
    # It's better to refactor `_add_media_and_start_timer` to directly use `context.job_queue`.

    # Let's re-think `_add_media_and_start_timer`. It needs to cancel existing jobs if a new one is started.
    # Each user should have their own timer job.
    # We can use `context.job_queue.run_once` and pass `chat_id` and `user_id` as context.

    # Modify `_add_media_and_start_timer` to manage the job queue.
    # To do this, `add_photo` and `add_video` handlers must receive `context.job_queue` implicitly.
    # When `application.run_polling()` is called, the `context` passed to handlers contains `job_queue`.

    async def _add_media_and_start_timer_with_job(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type: str) -> None:
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

        # Always cancel any pending auto-album job for this user/chat
        job_name = f"auto_album_prompt_{chat_id}"
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()
            logger.info(f"Cancelled existing auto album job for {chat_id}.")
        
        # If there's at least one media (photo or video), schedule the auto-prompt.
        # The logic for `not_enough_media_items` will be handled later when the album is actually created.
        if len(context.user_data["media_queue"]) >= 1: # We want to prompt even if only 1 item initially, then notify if < 2
            context.job_queue.run_once(
                callback=timeout_callback_auto_album_entry,
                when=3,  # 2 seconds
                name=job_name,
                chat_id=chat_id,
                user_id=user_id,
                data={"chat_id": chat_id, "user_id": user_id}, # Pass data for callback
                # context.user_data is accessible in the job context, but explicit `data` is cleaner for chat/user IDs.
            )
            logger.info(f"Scheduled new auto album prompt job for chat {chat_id} in 2 seconds.")

    # Re-map add_photo and add_video to the new function that handles the job queue.
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, lambda u, c: _add_media_and_start_timer_with_job(u, c, "photo")))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, lambda u, c: _add_media_and_start_timer_with_job(u, c, "video")))


    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

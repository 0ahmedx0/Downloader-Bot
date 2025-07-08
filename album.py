import os
import asyncio
import logging
import random
import math
import re

from telegram import (
    Update,
    InlineKeyboardButton, # Changed to InlineKeyboardButton
    InlineKeyboardMarkup,  # Changed to InlineKeyboardMarkup
    ReplyKeyboardRemove,   # Still useful for final removal of ReplyKeyboard if any
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler, # Added CallbackQueryHandler
    ContextTypes,
    filters,
    ConversationHandler,
)
from telegram.error import RetryAfter, TelegramError
from telegram.constants import ParseMode


# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحالات للمحادثة
ASKING_FOR_CAPTION = 1 # لطلب اختيار التعليق
ASKING_FOR_SEND_LOCATION = 2 # لطلب مكان الإرسال

# Callbacks prefixes
CAPTION_CB_PREFIX = "cap_"
SEND_LOC_CB_PREFIX = "sendloc_"
CANCEL_CB_DATA = "cancel_op" # For a cancel button in Inline Keyboard


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
    "keyboard_done": "إنشاء ألبوم", # This remains a ReplyKeyboard Button for entry point
    "keyboard_clear": "إعادة تعيين الألبوم", # This remains a ReplyKeyboard Button for general use
    "not_enough_media_items": "📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.",
    "queue_cleared": "لقد نسيت كل الصور والفيديوهات التي أرسلتها لي. لديك فرصة جديدة.",
    "album_caption_prompt": "الرجاء اختيار تعليق للألبوم من الأزرار أدناه:",
    "album_caption_manual_prompt_text_input": "الرجاء إدخال التعليق الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`", # Text input is now a special case or removed.
    "album_caption_confirm": "👍 حسناً! التعليق الذي اخترته هو: `{caption}`.\n", 
    "album_caption_confirm_no_caption": "👍 حسناً! لن يكون هناك تعليق للألبوم.\n", 
    "processing_album_start": "⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...\n\n",
    "progress_update": "جاري إرسال الألبوم: *{processed_albums}/{total_albums}*\nالوقت المتبقي المقدر: *{time_remaining_str}*.",
    "album_creation_success": "✅ تم إنشاء جميع الألبومات بنجاح!", 
    "album_creation_error": "❌ حدث خطأ أثناء إرسال الألبوم. يرجى المحاولة لاحقاً.",
    "album_chunk_fail": "⚠️ فشل إرسال جزء من الألبوم ({index}/{total_albums}). سأحاول الاستمرار مع البقية.",
    "cancel_caption": "لقد ألغيت عملية إنشاء الألبوم. يمكنك البدء من جديد.",
    "album_comment_option_manual": "إدخال تعليق يدوي (نص)", # This option is now purely a textual prompt to use if desired.
    "ask_send_location": "أين تود إرسال الألبوم؟",
    "send_to_channel_button": "القناة 📢",
    "send_to_chat_button": "المحادثة معي 👤",
    "channel_id_missing": "❌ لم يتم ضبط معرف القناة (CHANNEL_ID) في بيئة البوت. لا يمكن الإرسال للقناة. الرجاء الاتصال بالمطور.",
    "invalid_callback_data": "بيانات زر غير صالحة. الرجاء المحاولة مرة أخرى أو البدء من جديد.",
    "album_action_confirm": "{caption_status}{ask_location_prompt}", 
    "success_message_permanent_prompt": "يمكنك الآن إرسال المزيد من الوسائط أو استخدام الأزرار أدناه.",
    "caption_cancelled_by_inline_btn": "تم إلغاء عملية اختيار التعليق." # New message for inline cancel
}

# التعليقات الجاهزة كأزرار (مفتاح-قيمة: النص الظاهر للزر - قيمة الـ Callback Data)
# لإعادة استخدام التعليق نفسه كنص للـ callback_data، سنقوم بتعديله ليكون صالحاً للـ URL.
# ولكن الأفضل هو استخدام مؤشر أو رقم للـ Callback Data لتجنب مشاكل الطول أو الأحرف الخاصة.
# سأستخدم index لتكون قصيرة ونظيفة.
PREDEFINED_CAPTION_OPTIONS = [
    "عرض ورعان اجانب 🌈💋",
    "🌈 🔥 .",
    "حصريات منوع🌈🔥.",
    "حصريات🌈",
    "عربي منوع🌈🔥.",
    "اجنبي منوع🌈🔥.",
    "عربي 🌈🔥.",
    "اجنبي 🌈🔥.",
    "منوعات 🌈🔥.",
    "حصريات عربي 🌈🔥.",
    "حصريات اجنبي 🌈🔥.",
    # الآن التعليق اليدوي هو مجرد خيار يُختاره المستخدم ليخبر البوت أنه يريد كتابة النص
    "لا يوجد تعليق", # New option for no caption explicitly
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
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    if "messages_to_delete" not in context.user_data:
        context.user_data["messages_to_delete"] = []
    if "temp_messages_to_clean" not in context.user_data:
        context.user_data["temp_messages_to_clean"] = []
    if "progress_message_id" not in context.user_data:
        context.user_data["progress_message_id"] = None

async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """يحذف جميع الرسائل المخزنة في قائمة messages_to_delete."""
    if "messages_to_delete" in context.user_data:
        message_ids = list(context.user_data["messages_to_delete"]) 
        for msg_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.debug(f"Deleted message with ID: {msg_id} in chat {chat_id} (from messages_to_delete).")
            except Exception as e:
                logger.debug(f"Could not delete message {msg_id} in chat {chat_id}: {e}")
        context.user_data["messages_to_delete"].clear()
    

# الأوامر الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context)
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)
    # استخدام ReplyKeyboardMarkup للأزرار الرئيسية
    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
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
async def send_media_group_with_backoff(context: ContextTypes.DEFAULT_TYPE, chat_id_to_send_to: int, input_media, chunk_index: int, user_chat_id: int):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            sent_messages = await context.bot.send_media_group(chat_id=chat_id_to_send_to, media=input_media)
            return True, sent_messages 
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.",
                           chunk_index + 1, attempt + 1, e.retry_after)
            if chat_id_to_send_to != user_chat_id: 
                await context.bot.send_message(chat_id=user_chat_id, text=f"⚠️ تجاوزت حد رسائل تليجرام للقناة. سأنتظر {e.retry_after} ثانية قبل إعادة المحاولة.")
            else:
                 await context.bot.send_message(chat_id=user_chat_id, text=f"⚠️ تجاوزت حد رسائل تليجرام. سأنتظر {e.retry_after} ثانية قبل إعادة المحاولة.")
            await asyncio.sleep(e.retry_after)
        except TelegramError as e: 
            logger.error("TelegramError sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            error_message = MESSAGES["album_creation_error"]
            if "Forbidden: bot was blocked by the user" in str(e) or "chat not found" in str(e).lower() or "bot is not a member" in str(e).lower() or "not a member of the channel" in str(e).lower() or "not enough rights" in str(e).lower() or "need to be admin" in str(e).lower():
                error_message = "❌ فشل إرسال الألبوم: البوت ليس لديه صلاحية الإرسال لهذه القناة/الدردشة أو غير موجود فيها. الرجاء التأكد من الأذونات الصحيحة (نشر، تثبيت)."
            await context.bot.send_message(chat_id=user_chat_id, text=error_message)
            return False, None
        except Exception as e:
            logger.error("Generic Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["album_creation_error"])
            return False, None
    return False, None 

# -------------------------------------------------------------
# دوال ConversationHandler
# -------------------------------------------------------------

async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الأولى: تطلب من المستخدم اختيار تعليق الألبوم باستخدام Inline Buttons.
    """
    await initialize_user_data(context)
    user_chat_id = update.effective_chat.id
    
    # حذف كل الرسائل المؤقتة من التفاعل السابق عند بدء عملية جديدة
    await delete_messages_from_queue(context, user_chat_id)
    context.user_data["temp_messages_to_clean"].clear()


    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)

    if total_media < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return ConversationHandler.END
    
    # بناء InlineKeyboard لأزرار التعليقات
    inline_keyboard_buttons = []
    for i, caption_text in enumerate(PREDEFINED_CAPTION_OPTIONS):
        inline_keyboard_buttons.append([InlineKeyboardButton(caption_text, callback_data=f"{CAPTION_CB_PREFIX}{i}")])
    
    # زر إلغاء للوحة المفاتيح المضمنة
    inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])


    inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)
    
    prompt_msg = await update.message.reply_text(
        MESSAGES["album_caption_prompt"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=inline_markup
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id) # هذا يتم حذفه بعد استجابة المستخدم

    return ASKING_FOR_CAPTION

async def handle_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل اختيار التعليق من Inline Buttons.
    """
    query = update.callback_query
    user_choice_data = query.data
    user_chat_id = query.message.chat_id
    
    # تأكيد الاستجابة على الـ callback_query لمنع ظهور أيقونة التحميل
    await query.answer()

    # حذف رسالة الأزرار المضمنة بمجرد اختيار المستخدم
    await context.bot.delete_message(chat_id=user_chat_id, message_id=query.message.message_id)

    if user_choice_data == CANCEL_CB_DATA:
        await cancel_album_creation(update, context) # Use the existing cancel flow
        return ConversationHandler.END
    
    if user_choice_data.startswith(CAPTION_CB_PREFIX):
        caption_index = int(user_choice_data.replace(CAPTION_CB_PREFIX, ""))
        
        if 0 <= caption_index < len(PREDEFINED_CAPTION_OPTIONS):
            user_caption = PREDEFINED_CAPTION_OPTIONS[caption_index]
            if user_caption == "لا يوجد تعليق": # Handle the "No caption" case
                user_caption = ""
            
            context.user_data["current_album_caption"] = user_caption
            caption_status_message = MESSAGES["album_caption_confirm"].format(caption=user_caption) if user_caption else MESSAGES["album_caption_confirm_no_caption"]
            context.user_data["caption_status_message"] = caption_status_message
            
            return await ask_for_send_location(update, context)
        else:
            await query.message.reply_text(MESSAGES["invalid_callback_data"])
            await cancel_album_creation(update, context) # Revert to main state
            return ConversationHandler.END
    else:
        await query.message.reply_text(MESSAGES["invalid_callback_data"])
        await cancel_album_creation(update, context) # Revert to main state
        return ConversationHandler.END


# No more receive_manual_album_caption if direct text input is removed after this.
# If you still want text input after a button "Manual caption", then ASK_FOR_MANUAL_CAPTION is still needed.
# For now, I'm removing the text input path by assuming users will use only predefined options.
# If "إدخال تعليق يدوي (نص)" is desired as a button, it means the next message is a text input,
# and we would keep ASKING_FOR_MANUAL_CAPTION. Let's keep "إدخال تعليق يدوي (نص)"
# as a normal REPLY button on the start. So this handler for ASKING_FOR_CAPTION is only for INLINE BUTTONS.

# For manual text input, the start button for "إنشاء ألبوم" will lead to a Reply Keyboard asking for text
# input as a special "manual" option button. This is where your previous `handle_caption_choice` logic comes from.
# The user clicked an Inline button so handle_caption_choice MUST be a CallbackQueryHandler
# The decision now is: are ALL caption selections Inline? Or some are Inline and some are Reply?
# The request stated "اريد استخدام تلك الازرار حتى وان حذفنا امكانيه اضافه تعليق من قبل المستخدم" (referring to Inline buttons)

# To remove "إدخال تعليق يدوي" but keep the ability for user to type directly as fallback,
# it requires a complex flow with `filters.TEXT` and `filters.COMMAND`.

# My current understanding for *this* round of changes:
# 1. Start is still `ReplyKeyboard`
# 2. Caption selection is fully `InlineKeyboard`
# 3. No direct "text input" option is provided by a button; if they want text they send it instead of a photo/video (or you re-enable "إدخال تعليق يدوي (نص)" as a ReplyButton)

# If the "إدخال تعليق يدوي" *inline* option should exist, and lead to asking for a text input,
# ASKING_FOR_MANUAL_CAPTION state needs to be reused and its entry handler will be from here.

# Let's adjust, keeping only INLINE for captions and assuming "إدخال تعليق يدوي (نص)" will not exist as a button.
# "لا يوجد تعليق" inline button will serve the purpose of empty caption.

# If you really need "إدخال تعليق يدوي" as an inline button, it would behave like:
# Inline Button: "ادخل تعليق يدوي" -> Transition to ASKING_FOR_MANUAL_CAPTION
# ASKING_FOR_MANUAL_CAPTION: expects text from user, stores it, then goes to ASKING_FOR_SEND_LOCATION

# FOR THIS REVISION, I will make all captions INLINE buttons and REMOVE the manual text input flow
# provided by a button (i.e. no `ASKING_FOR_MANUAL_CAPTION` state).
# The "لا يوجد تعليق" will be an INLINE button for an empty caption.

# IF you want manual text input option through a button, the ConversationHandler logic gets complex
# mixing MessageHandlers and CallbackQueryHandlers within a state, OR you revert "manual text input"
# back to ReplyKeyboard (as it was) for the Caption step while keeping others as Inline.

# Let's assume you want all interaction WITHIN the flow to be Inline.


async def ask_for_send_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تطلب من المستخدم اختيار مكان إرسال الألبوم باستخدام Inline Buttons.
    """
    # Note: query is not passed to this function directly if called from handle_caption_choice
    # so we rely on `update.message.chat_id` or similar if called from other handlers.
    # In CallbackQueryHandler, query.message refers to the message *with the inline keyboard*.
    # So using update.callback_query.message.chat_id if called from callback, or update.message.chat_id otherwise.
    user_chat_id = update.effective_chat.id # Use effective_chat for robustness

    # If called from a CallbackQueryHandler, original message with buttons needs to be deleted/edited
    # We should have already deleted the previous message containing inline buttons in `handle_caption_choice`.

    inline_keyboard_buttons = [
        [InlineKeyboardButton(MESSAGES["send_to_channel_button"], callback_data=f"{SEND_LOC_CB_PREFIX}channel")],
        [InlineKeyboardButton(MESSAGES["send_to_chat_button"], callback_data=f"{SEND_LOC_CB_PREFIX}chat")]
    ]
    inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])
    
    inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)
    
    caption_status = context.user_data.get("caption_status_message", "")
    message_text = MESSAGES["album_action_confirm"].format(caption_status=caption_status, ask_location_prompt=MESSAGES["ask_send_location"])

    prompt_msg = await update.effective_chat.send_message( # Use send_message for new message with inline keyboard
        message_text,
        reply_markup=inline_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id) 
    
    return ASKING_FOR_SEND_LOCATION

async def handle_send_location_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل اختيار المستخدم لمكان إرسال الألبوم من Inline Buttons وتنفذه.
    """
    query = update.callback_query
    send_location_choice_data = query.data
    user_caption = context.user_data.get("current_album_caption", "")
    user_chat_id = query.message.chat_id
    
    await query.answer() # Acknowledge callback query
    await context.bot.delete_message(chat_id=user_chat_id, message_id=query.message.message_id) # Delete the message with buttons


    if send_location_choice_data == CANCEL_CB_DATA:
        await cancel_album_creation(update, context) # Use the existing cancel flow
        return ConversationHandler.END


    send_chat_id = None
    if send_location_choice_data == f"{SEND_LOC_CB_PREFIX}channel":
        send_chat_id_env = os.getenv("CHANNEL_ID")
        if not send_chat_id_env:
            error_msg = await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["channel_id_missing"])
            context.user_data["messages_to_delete"].append(error_msg.message_id)
            return await ask_for_send_location(update, context) # Stay in same state for re-prompt with new inline buttons
        try: 
            send_chat_id = int(send_chat_id_env)
        except ValueError:
            error_msg = await context.bot.send_message(chat_id=user_chat_id, text="❌ معرف القناة (CHANNEL_ID) في إعدادات البوت ليس رقماً صحيحاً.")
            context.user_data["messages_to_delete"].append(error_msg.message_id)
            return await ask_for_send_location(update, context)
    elif send_location_choice_data == f"{SEND_LOC_CB_PREFIX}chat":
        send_chat_id = user_chat_id
    else:
        await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["invalid_callback_data"])
        await cancel_album_creation(update, context)
        return ConversationHandler.END

    # إرسال رسالة "جاري إنشاء الألبوم" وتخزين معرفها
    progress_msg = await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["processing_album_start"] + MESSAGES["progress_update"].format(processed_albums=0, total_albums="؟", time_remaining_str="...") ,
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["progress_message_id"] = progress_msg.message_id # Save specific ID for editing/later deletion
    context.user_data["temp_messages_to_clean"].append(progress_msg.message_id) # Also add to cleanup list

    # تشغيل مهمة إنشاء الألبوم
    await execute_album_creation(update, context, user_caption, send_chat_id)

    # بعد الانتهاء من execute_album_creation
    final_feedback_msg = await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["album_creation_success"], 
    )
    context.user_data["temp_messages_to_clean"].append(final_feedback_msg.message_id) # Add final feedback to cleanup list


    # إرسال لوحة المفاتيح الرئيسية الدائمة (ReplyKeyboardMarkup)
    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)
    permanent_prompt_msg = await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["success_message_permanent_prompt"], 
        reply_markup=reply_markup_main
    )

    # البدء بمهمة خلفية لحذف جميع الرسائل المؤقتة بعد تأخير (5 ثواني)
    context.application.create_task(
        clear_all_temp_messages_after_delay(
            bot=context.bot,
            chat_id=user_chat_id,
            delay=5, # 5 ثواني كما طلبت
            context_user_data=context.user_data # pass user_data to access temp_messages_to_clean
        )
    )

    # مسح البيانات ذات الصلة بمسار الألبوم الحالي من user_data
    context.user_data.pop("current_album_caption", None)
    context.user_data.pop("caption_status_message", None)
    context.user_data.pop("progress_message_id", None) 

    return ConversationHandler.END

async def clear_all_temp_messages_after_delay(bot, chat_id, delay, context_user_data):
    """
    حذف كل الرسائل المؤقتة المخزنة في temp_messages_to_clean بعد تأخير زمني.
    """
    await asyncio.sleep(delay)
    
    if "temp_messages_to_clean" in context_user_data:
        messages_to_delete_ids = list(context_user_data["temp_messages_to_clean"]) # Make a copy
        for msg_id in messages_to_delete_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.debug(f"Deleted temporary message with ID: {msg_id} after delay.")
            except Exception as e:
                logger.debug(f"Could not delete temporary message {msg_id} in chat {chat_id} after delay: {e}")
        context_user_data["temp_messages_to_clean"].clear()
    else:
        logger.warning("temp_messages_to_clean not found in user_data during delayed deletion.")


async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    إعادة ضبط قائمة الوسائط، ومسح جميع الرسائل المؤقتة والعودة للقائمة الرئيسية.
    """
    chat_id = update.effective_chat.id
    
    # حذف جميع الرسائل التي يطلب البوت حذفها فوراً (prompt, manual prompt, etc.)
    await delete_messages_from_queue(context, chat_id)
    
    # محاولة حذف أي رسائل مؤقتة أخرى قد تكون ما زالت معلقة
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data) # delay 0 for immediate clear
    context.user_data["temp_messages_to_clean"].clear() # Ensure list is empty

    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)
    context.user_data.pop("caption_status_message", None)
    context.user_data.pop("progress_message_id", None) # Ensure this is also cleared

    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    await update.message.reply_text(
        MESSAGES["queue_cleared"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def cancel_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي محادثة التعليق ويعيد لوحة المفاتيح الرئيسية.
    """
    chat_id = update.effective_chat.id # Use update.effective_chat.id if called from ReplyKeyboard Button

    # If called from a CallbackQuery, update is a CallbackQuery object
    if update.callback_query:
        query = update.callback_query
        await query.answer() # Acknowledge the query
        chat_id = query.message.chat_id # Get chat_id from query.message
        # Attempt to delete the message that contained the inline keyboard
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
        except Exception as e:
            logger.debug(f"Could not delete query message in cancel_album_creation: {e}")

    # Delete all prompt messages
    await delete_messages_from_queue(context, chat_id)
    
    # Clear any pending temporary messages (from progress, final feedback)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data) # delay 0 for immediate clear
    context.user_data["temp_messages_to_clean"].clear() # Ensure list is empty


    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)
    context.user_data.pop("caption_status_message", None)
    context.user_data.pop("progress_message_id", None) # Ensure this is also cleared

    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    await context.bot.send_message( # Use send_message as the original message might be deleted
        chat_id=chat_id,
        text=MESSAGES["cancel_caption"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, album_caption: str, target_chat_id: int) -> None:
    """
    يقوم بإنشاء وإرسال الألبوم بناءً على الوسائط المخزنة والتعليق المحدد.
    """
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    user_chat_id = update.effective_chat.id # دردشة المستخدم لإرسال التحديثات له

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
    
    progress_message_id = context.user_data.get("progress_message_id")

    for index, chunk in enumerate(chunks):
        input_media = []
        for i, item in enumerate(chunk):
            caption = album_caption if i == 0 else None 
            if item["type"] == "photo":
                input_media.append(InputMediaPhoto(media=item["media"], caption=caption))
            elif item["type"] == "video":
                input_media.append(InputMediaVideo(media=item["media"], caption=caption))

        success, sent_messages = await send_media_group_with_backoff(
            context=context,
            chat_id_to_send_to=target_chat_id,
            input_media=input_media,
            chunk_index=index,
            user_chat_id=user_chat_id 
        )

        if not success:
            logger.error(f"Failed to send chunk {index + 1} to {target_chat_id}. Skipping to next.")
            continue

        logger.info(f"تم إرسال الدفعة {index + 1} إلى {target_chat_id}.")

        if str(target_chat_id) == os.getenv("CHANNEL_ID") and sent_messages and index == 0:
            try:
                await context.bot.pin_chat_message(chat_id=target_chat_id, message_id=sent_messages[0].message_id, disable_notification=True)
                logger.info("تم تثبيت الرسالة الأولى من الألبوم في القناة.")
            except Exception as pin_err:
                logger.warning(f"فشل في تثبيت الرسالة في القناة: {pin_err}. يرجى التأكد من أن البوت مشرف ولديه أذن التثبيت.")
                if user_chat_id != target_chat_id: 
                    await context.bot.send_message(chat_id=user_chat_id, text="⚠️ تم إرسال الألبوم للقناة ولكن تعذر تثبيت الرسالة الأولى. يرجى التأكد من أذونات البوت (نشر وتثبيت).")

        processed_albums += 1
        
        time_remaining_str = "جاري الحساب..."
        if total_albums > 1:
            remaining_albums = total_albums - processed_albums
            avg_delay_per_album = (get_random_delay(min_delay=5, max_delay=30, min_diff=7) + 5)
            estimated_time_remaining = remaining_albums * avg_delay_per_album
            minutes, seconds = divmod(int(estimated_time_remaining), 60)
            time_remaining_str = f"{minutes} دقيقة و {seconds} ثانية" if minutes > 0 else f"{seconds} ثانية"
        else:
            time_remaining_str = "الآن!" 

        current_progress_text = MESSAGES["processing_album_start"] + MESSAGES["progress_update"].format(
            processed_albums=processed_albums,
            total_albums=total_albums,
            time_remaining_str=time_remaining_str
        )

        try:
            if progress_message_id: 
                await context.bot.edit_message_text(
                    chat_id=user_chat_id,
                    message_id=progress_message_id,
                    text=current_progress_text,
                    parse_mode=ParseMode.MARKDOWN
                )
        except TelegramError as e: 
            logger.error(f"فشل في تحديث رسالة التقدم (ID: {progress_message_id}) في الدردشة {user_chat_id}: {e}")
            context.user_data["progress_message_id"] = None 
        except Exception as e:
            logger.error(f"حدث خطأ غير متوقع أثناء تحديث رسالة التقدم: {e}")

        if index < len(chunks) - 1:
            await asyncio.sleep(get_random_delay())

    context.user_data["media_queue"] = []


# تشغيل البوت
def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables. Please set it.")
        return

    channel_id_env = os.getenv("CHANNEL_ID")
    if not channel_id_env:
        logger.warning("CHANNEL_ID environment variable is not set. Channel posting feature will not work.")
    else:
        if not (channel_id_env.startswith("-100") and channel_id_env[1:].isdigit()):
            logger.error(f"Invalid CHANNEL_ID format: {channel_id_env}. It should start with '-100' followed by digits. Channel posting will not work.")


    application = Application.builder().token(token).build()

    caption_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$") & ~filters.COMMAND, start_album_creation_process)
        ],
        states={
            ASKING_FOR_CAPTION: [
                CallbackQueryHandler(handle_caption_choice, pattern=f"^{CAPTION_CB_PREFIX}|^({CANCEL_CB_DATA})$"), # Expect Inline Button callback
                # أي رسائل نصية هنا (إذا أرسلها المستخدم بالخطأ) ستؤدي لرسالة خطأ
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.effective_message.reply_text(MESSAGES["invalid_send_location_choice"])),
            ],
            # ASK_FOR_MANUAL_CAPTION is removed from this Inline-only flow (within conversation)
            ASKING_FOR_SEND_LOCATION: [
                CallbackQueryHandler(handle_send_location_choice, pattern=f"^{SEND_LOC_CB_PREFIX}|^({CANCEL_CB_DATA})$"), # Expect Inline Button callback
                # أي رسائل نصية هنا ستؤدي لرسالة خطأ
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.effective_message.reply_text(MESSAGES["invalid_send_location_choice"])),
            ],
        },
        fallbacks=[
            # Note: For fallbacks initiated by a CallbackQuery, you often need to get chat_id from query.message
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_album),
            CommandHandler("cancel", cancel_album_creation), # Handle /cancel for direct commands
            CommandHandler("start", cancel_album_creation), 
            CommandHandler("help", cancel_album_creation), 
            CommandHandler("settings", cancel_album_creation), 
            CommandHandler("source", cancel_album_creation),
            # This last fallback catches any non-callback_query text or media during a conversation
            # and triggers cancel. This is good for robustness.
            MessageHandler(filters.ALL & ~filters.COMMAND, cancel_album_creation) 
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

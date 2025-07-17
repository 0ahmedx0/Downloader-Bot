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
SETTING_GLOBAL_DESTINATION = 3

# Callbacks prefixes
CAPTION_CB_PREFIX = "cap_"
SEND_LOC_CB_PREFIX = "sendloc_"
CANCEL_CB_DATA = "cancel_op"

# الرسائل المستخدمة (سنتركها كما هي لسهولة فهم المعاني، لكننا لن نستخدم بعضها)
MESSAGES = {
    "greeting": (
        "مرحباً {username}! هل سبق أن وجدت صوراً رائعة على تيليجرام "
        "وأردت تجميعها في ألبوم، لكن لم ترغب في تنزيلها ثم إعادة رفعها؟ "
        "دعني أقوم بذلك بسرعة!\n\n"
        "أرسل لي أي صور أو فيديوهات وسأقوم بإنشاء ألبومات منها!\n\n"
    ),
    "initial_setup_prompt": (
        "قبل البدء، الرجاء تحديد وجهة إرسال الألبومات بشكل دائم.\n"
        "يمكنك تغيير هذا الخيار في أي وقت لاحقاً باستخدام زر 'تغيير وجهة الألبوم'."
    ),
    "destination_set_success": "👍 تم تعيين وجهة الألبوم الخاصة بك إلى: *{destination_name}*.",
    "destination_not_set_error": "لم يتم تحديد وجهة إرسال الألبوم بعد. الرجاء الضغط على زر '*تغيير وجهة الألبوم*' لتحديدها أولاً.",
    "help": (
        'فقط قم بتحويل أو إرسال صور وفيديوهات متعددة. عندما تنتهي، اضغط على زر "إنشاء ألبوم" '
        'وستحصل على جميع ملفاتك التي أرسلتها مسبقاً مجمعة كألبومات. إذا أخطأت، انقر على "إعادة تعيين الألبوم" للبدء من جديد.\n\n'
        "هذا العمل تم بواسطة @wjclub."
    ),
    "settings": "لا توجد إعدادات لتغييرها هنا.",
    "source": "https://github.com/wjclub/telegram-bot-album-creator",
    "keyboard_done": "إنشاء ألبوم",
    "keyboard_clear": "إعادة تعيين الألبوم",
    "keyboard_change_destination": "تغيير وجهة الألبوم 🔄",
    "not_enough_media_items": "📦 تحتاج إلى إرسال صورتين أو أكثر لتكوين ألبوم.",
    "queue_cleared": "لقد نسيت كل الصور والفيديوهات التي أرسلتها لي. لديك فرصة جديدة.",
    "album_caption_prompt": "الرجاء اختيار تعليق للألبوم من الأزرار أدناه:",
    "album_caption_manual_prompt": "الرجاء إدخال التعليق الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`",
    "album_caption_confirm": "👍 حسناً! التعليق الذي اخترته هو: `{caption}`.\n",
    "album_caption_confirm_no_caption": "👍 حسناً! لن يكون هناك تعليق للألبوم.\n",
    "processing_album_start": "⏳ جاري إنشاء الألبوم. قد يستغرق هذا بعض الوقت...\n\n",
    "progress_update": "جاري إرسال الألبوم: *{processed_albums}/{total_albums}*\nالوقت المتبقي المقدر: *{time_remaining_str}*.",
    # هذه الرسائل لن تظهر للمستخدم الآن
    "album_creation_success": "✅ تم إنشاء جميع الألبومات بنجاح!",
    "album_creation_error": "❌ حدث خطأ أثناء إرسال الألبوم. يرجى المحاولة لاحقاً.",
    "album_chunk_fail": "⚠️ فشل إرسال جزء من الألبوم ({index}/{total_albums}). سأحاول الاستمرار مع البقية.",
    "cancel_caption": "لقد ألغيت عملية إنشاء الألبوم. يمكنك البدء من جديد.",
    "cancel_operation": "تم إلغاء العملية.",
    "album_comment_option_manual": "إدخال تعليق يدوي",
    "ask_send_location": "أين تود إرسال الألبوم؟",
    "send_to_channel_button": "القناة 📢",
    "send_to_chat_button": "المحادثة معي 👤",
    "channel_id_missing": "❌ لم يتم ضبط معرف القناة (CHANNEL_ID) في بيئة البوت. لا يمكن الإرسال للقناة. الرجاء الاتصال بالمطور.",
    "invalid_input_choice": "خيار غير صالح أو إدخال غير متوقع. الرجاء الاختيار من الأزرار أو إلغاء العملية.",
    "album_action_confirm": "{caption_status}",
    "success_message_permanent_prompt": "يمكنك الآن إرسال المزيد من الوسائط أو استخدام الأزرار أدناه.", # لن تظهر هذه الرسالة بعد الألبوم
    "caption_cancelled_by_inline_btn": "تم إلغاء عملية اختيار التعليق."
}

# التعليقات الجاهزة كأزرار (القائمة الفعلية للتعليقات التي يمكن اختيارها)
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
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE):
    """يضمن تهيئة context.user_data وقائمة الوسائط."""
    if "media_queue" not in context.user_data:
        context.user_data["media_queue"] = []
    if "messages_to_delete" not in context.user_data:
        context.user_data["messages_to_delete"] = []
    if "temp_messages_to_clean" not in context.user_data:
        context.user_data["temp_messages_to_clean"] = []
    if "progress_message_id" not in context.user_data:
        context.user_data["progress_message_id"] = None
    if "album_destination_chat_id" not in context.user_data:
        context.user_data["album_destination_chat_id"] = None
    if "album_destination_name" not in context.user_data:
        context.user_data["album_destination_name"] = None


async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """يحذف جميع الرسائل المخزنة في قائمة messages_to_delete."""
    if "messages_to_delete" in context.user_data:
        message_ids = list(context.user_data["messages_to_delete"])
        for msg_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.debug(f"Deleted message with ID: {msg_id} in chat {chat_id} (from messages_to_delete).")
            except BadRequest as e:
                if "Message to delete not found" in str(e):
                    logger.debug(f"Message {msg_id} not found when trying to delete (already deleted?).")
                else:
                    logger.warning(f"Could not delete message {msg_id} in chat {chat_id}: {e}")
            except Exception as e:
                logger.warning(f"Could not delete message {msg_id} in chat {chat_id}: {e}")
        context.user_data["messages_to_delete"].clear()

# الأوامر الأساسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await initialize_user_data(context)
    username = update.effective_user.username or "human"
    message = MESSAGES["greeting"].format(username=username)

    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"]), KeyboardButton(MESSAGES["keyboard_change_destination"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(message, reply_markup=reply_markup)

    if context.user_data["album_destination_chat_id"] is None:
        await prompt_for_destination_setting(update, context, initial_setup=True)


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
            # تم حذف إرسال رسالة للمستخدم هنا لتلبية طلب "لا أريد أي رسالة تحذيرية"
            # await context.bot.send_message(chat_id=user_chat_id, text=f"⚠️ تجاوزت حد رسائل تيليجرام لـ{target_name}. سأنتظر {e.retry_after} ثانية قبل إعادة المحاولة.")
            await asyncio.sleep(e.retry_after)
        except TelegramError as e:
            logger.error("TelegramError sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            # تم حذف إرسال رسالة للمستخدم هنا
            # await context.bot.send_message(chat_id=user_chat_id, text=error_message)
            return False, None
        except Exception as e:
            logger.error("Generic Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            # تم حذف إرسال رسالة للمستخدم هنا
            # await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["album_creation_error"])
            return False, None
    return False, None

# -------------------------------------------------------------
# دوال ConversationHandler
# -------------------------------------------------------------

async def prompt_for_destination_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, initial_setup: bool = False) -> int:
    """
    تطلب من المستخدم اختيار وجهة إرسال الألبوم (مرة واحدة أو عند تغيير الوجهة).
    """
    user_chat_id = update.effective_chat.id
    await delete_messages_from_queue(context, user_chat_id)

    inline_keyboard_buttons = [
        [InlineKeyboardButton(MESSAGES["send_to_channel_button"], callback_data=f"{SEND_LOC_CB_PREFIX}channel")],
        [InlineKeyboardButton(MESSAGES["send_to_chat_button"], callback_data=f"{SEND_LOC_CB_PREFIX}chat")]
    ]
    inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])

    inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)

    if initial_setup:
        message_text = MESSAGES["initial_setup_prompt"] + "\n\n" + MESSAGES["ask_send_location"]
    else:
        message_text = MESSAGES["ask_send_location"]

    prompt_msg = await update.effective_chat.send_message(
        message_text,
        reply_markup=inline_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)

    return SETTING_GLOBAL_DESTINATION


async def handle_global_destination_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل اختيار المستخدم لوجهة الإرسال وتخزنها.
    """
    query = update.callback_query
    destination_choice_data = query.data
    user_chat_id = query.message.chat_id

    await query.answer()

    try:
        await context.bot.delete_message(chat_id=user_chat_id, message_id=query.message.message_id)
    except BadRequest as e:
        logger.debug(f"Could not delete message {query.message.message_id} with inline buttons: {e}")
    except Exception as e:
        logger.warning(f"Error deleting inline button message: {e}")

    if destination_choice_data == CANCEL_CB_DATA:
        await cancel_operation_general(update, context)
        return ConversationHandler.END

    send_chat_id = None
    destination_name = None

    if destination_choice_data == f"{SEND_LOC_CB_PREFIX}channel":
        send_chat_id_env = os.getenv("CHANNEL_ID")
        if not send_chat_id_env:
            # رسالة خطأ حول CHANNEL_ID لا تزال ضرورية هنا كإعداد
            error_msg = await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["channel_id_missing"])
            context.user_data["messages_to_delete"].append(error_msg.message_id)
            return await prompt_for_destination_setting(update, context)
        try:
            send_chat_id = int(send_chat_id_env)
            destination_name = MESSAGES["send_to_channel_button"]
        except ValueError:
            error_msg = await context.bot.send_message(chat_id=user_chat_id, text="❌ معرف القناة (CHANNEL_ID) في إعدادات البوت ليس رقماً صحيحاً.")
            context.user_data["messages_to_delete"].append(error_msg.message_id)
            return await prompt_for_destination_setting(update, context)

    elif destination_choice_data == f"{SEND_LOC_CB_PREFIX}chat":
        send_chat_id = user_chat_id
        destination_name = MESSAGES["send_to_chat_button"]
    else:
        await context.bot.send_message(chat_id=user_chat_id, text=MESSAGES["invalid_input_choice"])
        await cancel_operation_general(update, context)
        return ConversationHandler.END

    context.user_data["album_destination_chat_id"] = send_chat_id
    context.user_data["album_destination_name"] = destination_name

    feedback_msg = await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["destination_set_success"].format(destination_name=destination_name),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["messages_to_delete"].append(feedback_msg.message_id)

    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"]), KeyboardButton(MESSAGES["keyboard_change_destination"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["success_message_permanent_prompt"], # هذه الرسالة لا علاقة لها بإرسال الألبوم بل بإعداد الوجهة
        reply_markup=reply_markup
    )
    return ConversationHandler.END


async def start_album_creation_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الخطوة الأولى لإنشاء الألبوم: تتحقق من الوجهة وتطلب التعليق.
    """
    await initialize_user_data(context)
    user_chat_id = update.effective_chat.id

    await delete_messages_from_queue(context, user_chat_id)
    context.user_data["temp_messages_to_clean"].clear()

    if context.user_data["album_destination_chat_id"] is None:
        await update.message.reply_text(MESSAGES["destination_not_set_error"])
        return ConversationHandler.END

    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)

    if total_media < 2:
        await update.message.reply_text(MESSAGES["not_enough_media_items"])
        return ConversationHandler.END

    inline_keyboard_buttons = []
    for i, caption_text in enumerate(PREDEFINED_CAPTION_OPTIONS):
        inline_keyboard_buttons.append([InlineKeyboardButton(caption_text, callback_data=f"{CAPTION_CB_PREFIX}{i}")])

    inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])

    inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)

    prompt_msg = await update.message.reply_text(
        MESSAGES["album_caption_prompt"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=inline_markup
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)

    return ASKING_FOR_CAPTION

async def handle_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل اختيار التعليق من Inline Buttons.
    """
    query = update.callback_query
    user_choice_data = query.data
    user_chat_id = query.message.chat_id

    await query.answer()

    try:
        await context.bot.delete_message(chat_id=user_chat_id, message_id=query.message.message_id)
    except BadRequest as e:
        logger.debug(f"Could not delete message {query.message.message_id} with inline buttons: {e}")
    except Exception as e:
        logger.warning(f"Error deleting inline button message: {e}")

    if user_choice_data == CANCEL_CB_DATA:
        await cancel_album_creation(update, context)
        return ConversationHandler.END

    if user_choice_data.startswith(CAPTION_CB_PREFIX):
        caption_index = int(user_choice_data.replace(CAPTION_CB_PREFIX, ""))

        if 0 <= caption_index < len(PREDEFINED_CAPTION_OPTIONS):
            selected_option_text = PREDEFINED_CAPTION_OPTIONS[caption_index]

            if selected_option_text == MESSAGES["album_comment_option_manual"]:
                manual_prompt_msg = await context.bot.send_message(
                    chat_id=user_chat_id,
                    text=MESSAGES["album_caption_manual_prompt"],
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=ReplyKeyboardRemove()
                )
                context.user_data["messages_to_delete"].append(manual_prompt_msg.message_id)
                return ASKING_FOR_MANUAL_CAPTION
            elif selected_option_text == "لا يوجد تعليق":
                user_caption = ""
                context.user_data["current_album_caption"] = user_caption
                context.user_data["caption_status_message"] = MESSAGES["album_caption_confirm_no_caption"]
                return await finalize_album_action(update, context)
            else:
                user_caption = selected_option_text
                context.user_data["current_album_caption"] = user_caption
                context.user_data["caption_status_message"] = MESSAGES["album_caption_confirm"].format(caption=user_caption)
                return await finalize_album_action(update, context)
        else:
            await query.message.reply_text(MESSAGES["invalid_input_choice"])
            await cancel_album_creation(update, context)
            return ConversationHandler.END
    else:
        await query.message.reply_text(MESSAGES["invalid_input_choice"])
        await cancel_album_creation(update, context)
        return ConversationHandler.END


async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل التعليق اليدوي وتنتقل لتنفيذ إنشاء الألبوم.
    """
    user_caption = update.message.text
    user_chat_id = update.effective_chat.id

    if user_caption == '.':
        user_caption = ""

    context.user_data["current_album_caption"] = user_caption
    context.user_data["caption_status_message"] = MESSAGES["album_caption_confirm"].format(caption=user_caption) if user_caption else MESSAGES["album_caption_confirm_no_caption"]

    return await finalize_album_action(update, context)


async def finalize_album_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    الدالة التي تنفذ إرسال الألبوم بعد تحديد التعليق والوجهة.
    """
    user_chat_id = update.effective_chat.id
    await delete_messages_from_queue(context, user_chat_id)

    album_caption = context.user_data.get("current_album_caption", "")
    target_chat_id = context.user_data.get("album_destination_chat_id")

    # إرسال رسالة "جاري إنشاء الألبوم" وتخزين معرفها للتعديل
    # هذه الرسالة ستظل ظاهرة ليعلم المستخدم أن البوت يعمل
    progress_msg = await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["processing_album_start"] + MESSAGES["progress_update"].format(processed_albums=0, total_albums="؟", time_remaining_str="...") ,
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["progress_message_id"] = progress_msg.message_id
    context.user_data["temp_messages_to_clean"].append(progress_msg.message_id)

    # تشغيل مهمة إنشاء الألبوم
    await execute_album_creation(update, context, album_caption, target_chat_id)

    # جميع الرسائل بعد إرسال الألبوم تم إزالتها هنا بناءً على طلبك
    # لا توجد رسالة نجاح، لا رسالة خطأ عامة، ولا إعادة إرسال لوحة المفاتيح هنا.

    # البدء بمهمة خلفية لحذف جميع الرسائل المؤقتة بعد تأخير (فقط رسائل البوت التي تم إرسالها خلال العملية)
    context.application.create_task(
        clear_all_temp_messages_after_delay(
            bot=context.bot,
            chat_id=user_chat_id,
            delay=5,
            context_user_data=context.user_data
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
        messages_to_delete_ids = list(context_user_data["temp_messages_to_clean"])
        for msg_id in messages_to_delete_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.debug(f"Deleted temporary message with ID: {msg_id} after delay.")
            except BadRequest as e:
                if "Message to delete not found" in str(e):
                    logger.debug(f"Message {msg_id} not found when trying to delete (already deleted?).")
                else:
                    logger.warning(f"Could not delete temporary message {msg_id} in chat {chat_id} after delay: {e}")
            except Exception as e:
                logger.error(f"Error during delayed temporary message deletion for {msg_id}: {e}")
        context_user_data["temp_messages_to_clean"].clear()
    else:
        logger.debug("temp_messages_to_clean not found in user_data during delayed deletion (likely already cleared).")


async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    إعادة ضبط قائمة الوسائط، ومسح جميع الرسائل المؤقتة والعودة للقائمة الرئيسية.
    """
    chat_id = update.effective_chat.id

    await delete_messages_from_queue(context, chat_id)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data)
    context.user_data["temp_messages_to_clean"].clear()

    context.user_data["media_queue"] = []
    context.user_data.pop("current_album_caption", None)
    context.user_data.pop("caption_status_message", None)
    context.user_data.pop("progress_message_id", None)


    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"]), KeyboardButton(MESSAGES["keyboard_change_destination"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    # هذه الرسالة تبقى لإعادة تعيين قائمة المحفوظات وإعادة لوحة المفاتيح
    await update.message.reply_text(
        MESSAGES["queue_cleared"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def cancel_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي محادثة التعليق ويعيد لوحة المفاتيح الرئيسية.
    """
    chat_id = update.effective_chat.id

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
        except BadRequest as e:
            if "Message to delete not found" in str(e):
                logger.debug(f"Message {query.message.message_id} not found when trying to delete.")
            else:
                logger.warning(f"Could not delete query message in cancel_album_creation: {e}")
        except Exception as e:
            logger.warning(f"Error deleting query message in cancel_album_creation: {e}")

    await delete_messages_from_queue(context, chat_id)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data)
    context.user_data["temp_messages_to_clean"].clear()

    context.user_data.pop("current_album_caption", None)
    context.user_data.pop("caption_status_message", None)
    context.user_data.pop("progress_message_id", None)
    context.user_data["media_queue"] = []


    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"]), KeyboardButton(MESSAGES["keyboard_change_destination"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    # هذه الرسالة تبقى لتأكيد الإلغاء وإعادة لوحة المفاتيح
    await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["cancel_caption"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def cancel_operation_general(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي أي عملية عامة ويعيد لوحة المفاتيح الرئيسية.
    """
    chat_id = update.effective_chat.id

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
        except BadRequest as e:
            logger.debug(f"Message {query.message.message_id} not found when trying to delete.")
        except Exception as e:
            logger.warning(f"Error deleting query message in cancel_operation_general: {e}")

    await delete_messages_from_queue(context, chat_id)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data)
    context.user_data["temp_messages_to_clean"].clear()

    context.user_data.pop("current_album_caption", None)
    context.user_data.pop("caption_status_message", None)
    context.user_data.pop("progress_message_id", None)

    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_done"])],
        [KeyboardButton(MESSAGES["keyboard_clear"]), KeyboardButton(MESSAGES["keyboard_change_destination"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    # هذه الرسالة تبقى لتأكيد الإلغاء وإعادة لوحة المفاتيح
    await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["cancel_operation"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def execute_album_creation(update: Update, context: ContextTypes.DEFAULT_TYPE, album_caption: str, target_chat_id: int) -> None:
    """
    يقوم بإنشاء وإرسال الألبوم بناءً على الوسائط المخزنة والتعليق والوجهة الثابتة.
    """
    media_queue = context.user_data.get("media_queue", [])
    total_media = len(media_queue)
    user_chat_id = update.effective_chat.id

    destination_name = context.user_data.get("album_destination_name", "الوجهة المختارة")
    logger.info("بدء تحويل الألبوم. عدد الوسائط: %d. الهدف: %s (%s)", total_media, target_chat_id, destination_name)

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

        if str(target_chat_id).startswith("-100") and sent_messages:
            try:
                await context.bot.pin_chat_message(chat_id=target_chat_id, message_id=sent_messages[0].message_id, disable_notification=True)
                logger.info(f"تم تثبيت الرسالة الأولى من الألبوم الدفعة {index + 1} في القناة {target_chat_id}.")
            except Exception as pin_err:
                logger.warning(f"فشل في تثبيت الرسالة (دفعة {index + 1}) في القناة: {pin_err}. يرجى التأكد من أن البوت مشرف ولديه أذن التثبيت.")
                # تم حذف إرسال رسالة تحذيرية هنا بناءً على طلبك
                # if user_chat_id != target_chat_id:
                #    await context.bot.send_message(chat_id=user_chat_id, text=f"⚠️ تم إرسال الألبوم الدفعة {index+1} لـ{destination_name} ولكن تعذر تثبيت الرسالة الأولى. يرجى التأكد من أذونات البوت (نشر وتثبيت).")

        processed_albums += 1

        if total_albums > 1:
            time_remaining_str = "جاري الحساب..."
            remaining_albums = total_albums - processed_albums
            avg_delay_per_album = (get_random_delay(min_delay=5, max_delay=30, min_diff=7) + 5)
            estimated_time_remaining = remaining_albums * avg_delay_per_album
            minutes, seconds = divmod(int(estimated_time_remaining), 60)
            time_remaining_str = f"{minutes} دقيقة و {seconds} ثانية" if minutes > 0 else f"{seconds} ثانية"
            if processed_albums == total_albums:
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
        else:
            logger.debug("Skipping progress message update for single album.")

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
        logger.warning("CHANNEL_ID environment variable is not set. Channel posting feature will not work unless configured.")
    else:
        if not (channel_id_env.startswith("-100") and channel_id_env[1:].isdigit()):
            logger.error(f"Invalid CHANNEL_ID format: {channel_id_env}. It should start with '-100' followed by digits. Channel posting may not work correctly.")


    application = Application.builder().token(token).build()

    destination_setting_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_destination'])}$") & ~filters.COMMAND, prompt_for_destination_setting),
            CommandHandler("start", start)
        ],
        states={
            SETTING_GLOBAL_DESTINATION: [
                CallbackQueryHandler(handle_global_destination_choice, pattern=f"^{SEND_LOC_CB_PREFIX}.*|^({CANCEL_CB_DATA})$"),
                MessageHandler(filters.ALL & ~filters.COMMAND, lambda u,c: u.effective_message.reply_text(MESSAGES["invalid_input_choice"])),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_operation_general),
            MessageHandler(filters.ALL & ~filters.COMMAND, cancel_operation_general)
        ],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )

    album_creation_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$") & ~filters.COMMAND, start_album_creation_process)
        ],
        states={
            ASKING_FOR_CAPTION: [
                CallbackQueryHandler(handle_caption_choice, pattern=f"^{CAPTION_CB_PREFIX}.*|^({CANCEL_CB_DATA})$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.effective_message.reply_text(MESSAGES["invalid_input_choice"])),
            ],
            ASKING_FOR_MANUAL_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption),
            ],
        },
        fallbacks=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_album),
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_destination'])}$") & ~filters.COMMAND, cancel_album_creation),
            CommandHandler("cancel", cancel_album_creation),
            CommandHandler("start", cancel_album_creation),
            CommandHandler("help", cancel_album_creation),
            CommandHandler("settings", cancel_album_creation),
            CommandHandler("source", cancel_album_creation),
            MessageHandler(filters.ALL & ~filters.COMMAND, cancel_album_creation)
        ]
    )

    application.add_handler(destination_setting_conversation_handler)

    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, add_photo))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, add_video))

    application.add_handler(album_creation_conversation_handler)

    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_album))


    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

import os
import asyncio
import logging
import time
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
    JobQueue
)
from telegram.error import RetryAfter, TelegramError, BadRequest
from telegram.constants import ParseMode


# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحالات للمحادثة
SETTING_GLOBAL_DESTINATION = 1
COLLECTING_MEDIA_GROUP = 2 # حالة جديدة لجمع أجزاء الألبوم
ASKING_FOR_CAPTION = 3
ASKING_FOR_MANUAL_CAPTION = 4

# Callbacks prefixes
SEND_LOC_CB_PREFIX = "sendloc_"
CAPTION_CB_PREFIX = "cap_"
CANCEL_CB_DATA = "cancel_op"

# الثوابت
FIXED_ALBUM_DELAY = 10 # التأخير الثابت بين كل ألبوم (مجموعة وسائط) يتم إرساله بالثواني.
MEDIA_GROUP_COLLECTION_TIMEOUT = 1.0 # الوقت اللازم لجمع جميع أجزاء الألبوم قبل معالجتها

# لضمان عدم تداخل إرسال الألبومات (حماية للوظائف المتزامنة)
_forward_lock = asyncio.Lock()


# الرسائل المستخدمة
MESSAGES = {
    "greeting": (
        "مرحباً {username}! أرسل لي أي ألبوم صور أو فيديوهات (مجموعة وسائط) "
        "وسأقوم بتحويلها مباشرة إلى الوجهة المحددة.\n\n"
        "لتغيير الوجهة، استخدم زر 'تغيير وجهة الألبوم'.\n"
        "لإعادة تعيين قائمة الانتظار (إذا كنت ترسل مجموعات بسرعة)، استخدم 'إعادة تعيين البوت'."
    ),
    "initial_setup_prompt": (
        "قبل البدء، الرجاء تحديد وجهة إرسال الألبومات بشكل دائم.\n"
        "يمكنك تغيير هذا الخيار في أي وقت لاحقاً باستخدام زر 'تغيير وجهة الألبوم'."
    ),
    "destination_set_success": "👍 تم تعيين وجهة الألبوم الخاصة بك إلى: *{destination_name}*.",
    "destination_not_set_error": "لم يتم تحديد وجهة إرسال الألبوم بعد. الرجاء الضغط على زر '*تغيير وجهة الألبوم*' لتحديدها أولاً.",
    "help": (
        'فقط أرسل لي ألبومات (مجموعات صور وفيديوهات) مباشرة.\n'
        'البوت سيقوم بتحويلها إلى الوجهة التي قمت بتحديدها مسبقاً (قناة أو محادثة خاصة).\n'
        'الرسالة الأولى من كل ألبوم يتم إرساله للقناة سيتم تثبيتها تلقائياً.\n'
        'سيتم تطبيق تأخير {delay} ثوانٍ بين كل ألبوم والآخر.\n\n'
        'استخدم "تغيير وجهة الألبوم" لتغيير الوجهة، و"إعادة تعيين البوت" لمسح أي مهام معلقة.\n\n'
        "هذا العمل تم بواسطة @wjclub."
    ),
    "settings": "لا توجد إعدادات لتغييرها هنا.",
    "source": "https://github.com/wjclub/telegram-bot-album-creator",
    "keyboard_clear": "إعادة تعيين البوت",
    "keyboard_change_destination": "تغيير وجهة الألبوم 🔄",
    "queue_cleared": "تم مسح قائمة التحويلات المعلقة.",
    "cancel_operation": "تم إلغاء العملية.",
    "album_caption_prompt": "الرجاء اختيار تعليق للألبوم من الأزرار أدناه:",
    "album_caption_manual_prompt": "الرجاء إدخال التعليق الذي تريده للألبوم. (سيكون هذا هو التعليق فقط لأول وسائط في كل ألبوم إذا كان هناك ألبومات متعددة).\n\nإذا كنت لا تريد أي تعليق، فقط أرسل لي نقطة `.`",
    "album_caption_confirm": "👍 حسناً! التعليق الذي اخترته هو: `{caption}`.\n",
    "album_caption_confirm_no_caption": "👍 حسناً! لن يكون هناك تعليق للألبوم.\n",
    "album_comment_option_manual": "إدخال تعليق يدوي",
    "ask_send_location": "أين تود إرسال الألبومات؟",
    "send_to_channel_button": "القناة 📢",
    "send_to_chat_button": "المحادثة معي 👤",
    "channel_id_missing": "❌ لم يتم ضبط معرف القناة (CHANNEL_ID) في بيئة البوت. لا يمكن الإرسال للقناة. الرجاء الاتصال بالمطور.",
    "invalid_input_choice": "خيار غير صالح أو إدخال غير متوقع. الرجاء الاختيار من الأزرار أو إلغاء العملية.",
    "success_message_permanent_prompt": "يمكنك الآن إرسال المزيد من الألبومات أو استخدام الأزرار أدناه.",
}

# التعليقات الجاهزة كأزرار
PREDEFINED_CAPTION_OPTIONS = [
    "عرض ورعان اجانب 🌈💋",
    "🌈 🔥 .",
    "حصريات منوع🌈🔥.",
    " حصريات🌈", # هناك مسافة زائدة هنا، يمكن تصحيحها إذا أردت
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


# تهيئة بيانات المستخدم
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE):
    """يضمن تهيئة context.user_data والمتغيرات الضرورية."""
    if "messages_to_delete" not in context.user_data:
        context.user_data["messages_to_delete"] = []
    if "temp_messages_to_clean" not in context.user_data:
        context.user_data["temp_messages_to_clean"] = []
    if "album_destination_chat_id" not in context.user_data:
        context.user_data["album_destination_chat_id"] = None
    if "album_destination_name" not in context.user_data:
        context.user_data["album_destination_name"] = None
    if '_last_forward_timestamp' not in context.user_data:
        context.user_data['_last_forward_timestamp'] = 0
    # لتخزين بيانات كل وسيط (file_id, type, original_caption)
    if 'current_album_raw_media_data' not in context.user_data:
        context.user_data['current_album_raw_media_data'] = []
    if 'current_media_group_id' not in context.user_data:
        context.user_data['current_media_group_id'] = None
    if 'chosen_album_caption' not in context.user_data:
        context.user_data['chosen_album_caption'] = ""


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
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    await update.message.reply_text(message, reply_markup=reply_markup)

    if context.user_data["album_destination_chat_id"] is None:
        await prompt_for_destination_setting(update, context, initial_setup=True)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["help"].format(delay=FIXED_ALBUM_DELAY))

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["settings"])

async def source_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(MESSAGES["source"])


# دالة إرسال مجموعة الوسائط مع Backoff (لا رسائل تحذيرية للمستخدم)
async def send_media_group_with_backoff(bot_instance, chat_id_to_send_to: int, input_media_list, user_chat_id: int):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            sent_messages = await bot_instance.send_media_group(chat_id=chat_id_to_send_to, media=input_media_list)
            return True, sent_messages
        except RetryAfter as e:
            logger.warning(f"RetryAfter (attempt {attempt+1}/{max_retries}): Waiting for {e.retry_after} seconds for user {user_chat_id}.")
            await asyncio.sleep(e.retry_after)
        except TelegramError as e:
            logger.error(f"TelegramError (attempt {attempt+1}/{max_retries}) sending album for user {user_chat_id}: {e}")
            return False, None
        except Exception as e:
            logger.error(f"Generic Error (attempt {attempt+1}/{max_retries}) sending album for user {user_chat_id}: {e}")
            return False, None
    return False, None

# -------------------------------------------------------------
# دوال ConversationHandler لضبط الوجهة
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
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    await context.bot.send_message(
        chat_id=user_chat_id,
        text=MESSAGES["success_message_permanent_prompt"],
        reply_markup=reply_markup
    )
    return ConversationHandler.END

# -------------------------------------------------------------
# دوال معالجة الوسائط والألبومات
# -------------------------------------------------------------

async def handle_incoming_media_and_start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تُستدعى كـ entry point لـ ConversationHandler بعد استلام وسائط.
    تُخزن الوسائط، وتُحدد ما إذا كانت جزءًا من مجموعة، وتُبادر بعملية طلب التعليق.
    """
    user_chat_id = update.effective_chat.id
    await initialize_user_data(context) # تأكد أن بيانات المستخدم مهيأة

    # تحقق من ضبط الوجهة أولاً
    if context.user_data.get("album_destination_chat_id") is None:
        await update.message.reply_text(MESSAGES["destination_not_set_error"])
        reply_keyboard = [
            [KeyboardButton(MESSAGES["keyboard_change_destination"])],
            [KeyboardButton(MESSAGES["keyboard_clear"])]
        ]
        reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
        await context.bot.send_message(
            chat_id=user_chat_id,
            text=MESSAGES["success_message_permanent_prompt"],
            reply_markup=reply_markup
        )
        return ConversationHandler.END


    # مسح أي رسائل سابقة من البوت تتعلق بالعملية السابقة (لإعداد رسائل الأزرار الجديدة)
    await delete_messages_from_queue(context, user_chat_id)

    message = update.message
    media_group_id = message.media_group_id
    current_album_identifier = media_group_id if media_group_id else f"single_media_{message.id}"
    
    file_id = None
    media_type = None
    caption_raw = message.caption # الكابتشن الأصلي للرسالة

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    else:
        logger.debug(f"Received non-photo/video message from user {user_chat_id} - exiting handle_incoming_media_and_start_flow.")
        return ConversationHandler.END


    if media_type:
        # تخزين البيانات الخام للوسيط بدلاً من كائن InputMedia (لإعادة بنائه لاحقًا مع الكابتشن المختار)
        media_data = {'file_id': file_id, 'type': media_type, 'original_caption': caption_raw}

        # تهيئة قائمة الوسائط للألبوم الحالي في context.user_data
        # هذه قائمة بيانات خام، لا InputMedia objects
        if 'current_media_group_id' not in context.user_data or context.user_data['current_media_group_id'] != current_album_identifier:
            context.user_data['current_album_raw_media_data'] = []
            context.user_data['current_media_group_id'] = current_album_identifier
        
        context.user_data['current_album_raw_media_data'].append(media_data)

        # إذا كانت مجموعة وسائط, ننتقل لحالة الجمع COLLECTING_MEDIA_GROUP
        if media_group_id:
            job_name = f"final_collect_job_{media_group_id}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal() # إلغاء المهام السابقة لنفس المجموعة لتحديث المؤقت

            context.job_queue.run_once(
                _send_caption_prompt_after_collection_job, # هذا Job الآن سيرسل أزرار التعليق
                MEDIA_GROUP_COLLECTION_TIMEOUT,
                data={"media_group_id": media_group_id, "user_chat_id": user_chat_id},
                name=job_name
            )
            return COLLECTING_MEDIA_GROUP # البقاء في هذه الحالة لجمع باقي الأجزاء
        else:
            # إذا كانت وسائط مفردة، نسأل عن التعليق مباشرةً
            return await ask_for_caption_and_send_prompt(update, context)
    else:
        logger.warning(f"No media type detected for message from user {user_chat_id}.")
        return ConversationHandler.END


async def handle_collecting_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    معالج يستقبل الرسائل اللاحقة (ضمن نفس مجموعة الوسائط).
    يُضاف الرسالة إلى current_album_raw_media_data ويُعاد جدولة JobQueue.
    """
    message = update.message
    media_group_id = message.media_group_id
    user_chat_id = update.effective_chat.id

    # تأكد أنها نفس مجموعة الوسائط التي نجمعها حالياً
    if media_group_id and context.user_data.get('current_media_group_id') == media_group_id:
        file_id = None
        media_type = None
        caption_raw = message.caption

        if message.photo:
            file_id = message.photo[-1].file_id
            media_type = "photo"
        elif message.video:
            file_id = message.video.file_id
            media_type = "video"
        
        if media_type:
            media_data = {'file_id': file_id, 'type': media_type, 'original_caption': caption_raw}
            context.user_data['current_album_raw_media_data'].append(media_data)

            # إعادة جدولة وظيفة تأكيد الاكتمال لتمديد الوقت
            job_name = f"final_collect_job_{media_group_id}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()
            
            context.job_queue.run_once(
                _send_caption_prompt_after_collection_job,
                MEDIA_GROUP_COLLECTION_TIMEOUT, # تمديد التأخير
                data={"media_group_id": media_group_id, "user_chat_id": user_chat_id},
                name=job_name
            )
            return COLLECTING_MEDIA_GROUP # البقاء في نفس الحالة لجمع المزيد
        logger.debug(f"Collected additional media for group {media_group_id}.")
    else:
        # إذا وصلت رسالة وسائط ولكنها ليست جزءًا من الألبوم النشط (إما ألبوم جديد أو مفرد)
        # هذا يعني أن المحادثة الحالية قد انتهت (إرسال الألبوم)، والرسالة الجديدة يجب أن تبدأ محادثة جديدة
        return ConversationHandler.END # إنهاء المحادثة الحالية للسماح لل ConversationHandler ببدء واحدة جديدة

    return COLLECTING_MEDIA_GROUP # البقاء في هذه الحالة طالما الرسائل متتالية لنفس المجموعة

async def _send_caption_prompt_after_collection_job(context: ContextTypes.DEFAULT_TYPE):
    """
    يُستدعى بواسطة JobQueue بعد مرور وقت `MEDIA_GROUP_COLLECTION_TIMEOUT` من استقبال آخر جزء من مجموعة وسائط.
    وظيفتها التأكد من اكتمال المجموعة، ثم إظهار prompt التعليق.
    """
    job_data = context.job.data
    media_group_id = job_data["media_group_id"]
    user_chat_id = job_data["user_chat_id"]
    
    user_data_for_job = context.application.user_data.get(user_chat_id)
    if not user_data_for_job:
        logger.warning(f"user_data not found for chat_id {user_chat_id} in Job {media_group_id}. Skipping caption prompt.")
        return

    # التأكد من أن هذه هي المجموعة التي يجمعها المستخدم حالياً
    if user_data_for_job.get('current_media_group_id') == media_group_id:
        logger.info(f"Media group {media_group_id} collected for user {user_chat_id}. Prompting for caption.")

        inline_keyboard_buttons = []
        for i, caption_text in enumerate(PREDEFINED_CAPTION_OPTIONS):
            inline_keyboard_buttons.append([InlineKeyboardButton(caption_text, callback_data=f"{CAPTION_CB_PREFIX}{i}")])
        inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])
        inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)
        
        prompt_msg = await context.bot.send_message(
            chat_id=user_chat_id,
            text=MESSAGES["album_caption_prompt"],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=inline_markup
        )
        user_data_for_job["messages_to_delete"].append(prompt_msg.message_id)
    else:
        logger.debug(f"Job triggered for {media_group_id} but it's not the current active media group or already handled for user {user_chat_id}.")


async def ask_for_caption_and_send_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    وظيفة مساعدة لطباعة أزرار اختيار التعليق للوسائط المفردة (أو بعد اكتمال تجميع المجموعة).
    """
    inline_keyboard_buttons = []
    for i, caption_text in enumerate(PREDEFINED_CAPTION_OPTIONS):
        inline_keyboard_buttons.append([InlineKeyboardButton(caption_text, callback_data=f"{CAPTION_CB_PREFIX}{i}")])
    inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])
    inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)

    # هنا يمكن أن تكون update قادمة من MessageHandler (وسائط مفردة) أو CalllbackQuery (زر).
    # للتأكد من أنها ترسل الرسالة إلى الدردشة الصحيحة.
    chat_id_to_send_to = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat_id

    prompt_msg = await context.bot.send_message(
        chat_id=chat_id_to_send_to,
        text=MESSAGES["album_caption_prompt"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=inline_markup
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)

    return ASKING_FOR_CAPTION # ننتقل إلى هذه الحالة عند إرسال prompt التعليق

async def handle_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل اختيار التعليق من Inline Buttons.
    """
    query = update.callback_query
    user_choice_data = query.data
    user_chat_id = query.message.chat_id

    # Log current state to debug
    current_state = context.dispatcher.user_data[user_chat_id].get('_conversation_state', 'UNKNOWN')
    logger.info(f"handle_caption_choice triggered in state {current_state} by user {user_chat_id} with data {user_choice_data}")


    await query.answer()

    # حذف رسالة الأزرار المضمنة بمجرد اختيار المستخدم
    try:
        await context.bot.delete_message(chat_id=user_chat_id, message_id=query.message.message_id)
    except BadRequest as e:
        logger.debug(f"Could not delete message {query.message.message_id} with inline buttons: {e}")
    except Exception as e:
        logger.warning(f"Error deleting inline button message: {e}")

    if user_choice_data == CANCEL_CB_DATA:
        await cancel_current_album_process(update, context)
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
                    reply_markup=ReplyKeyboardRemove() # يزيل لوحة المفاتيح
                )
                context.user_data["messages_to_delete"].append(manual_prompt_msg.message_id)
                return ASKING_FOR_MANUAL_CAPTION
            elif selected_option_text == "لا يوجد تعليق":
                context.user_data["chosen_album_caption"] = ""
                await _trigger_album_forward(update, context) # Pass update
                return ConversationHandler.END
            else:
                context.user_data["chosen_album_caption"] = selected_option_text
                await _trigger_album_forward(update, context) # Pass update
                return ConversationHandler.END
        else:
            await query.message.reply_text(MESSAGES["invalid_input_choice"])
            await cancel_current_album_process(update, context)
            return ConversationHandler.END
    else:
        await query.message.reply_text(MESSAGES["invalid_input_choice"])
        await cancel_current_album_process(update, context)
        return ConversationHandler.END


async def receive_manual_album_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تستقبل التعليق اليدوي وتنتقل لتنفيذ إنشاء الألبوم.
    """
    user_caption = update.message.text
    user_chat_id = update.effective_chat.id

    if user_caption == '.':
        user_caption = ""

    context.user_data["chosen_album_caption"] = user_caption

    await _trigger_album_forward(update, context)

    return ConversationHandler.END


async def _trigger_album_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    وظيفة مساعدة لجدولة مهمة تحويل الألبوم بعد تحديد التعليق.
    تستقبل Update لتتمكن من استخراج effective_chat.id.
    """
    user_chat_id = update.effective_chat.id
    
    album_identifier = context.user_data.get('current_media_group_id')
    raw_media_data = context.user_data.get('current_album_raw_media_data', [])
    album_caption = context.user_data.get('chosen_album_caption', "")

    if not raw_media_data or album_identifier is None:
        logger.error(f"No raw media data or identifier found for user {user_chat_id} when attempting to trigger album forward.")
        await context.bot.send_message(chat_id=user_chat_id, text=".", reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton(MESSAGES["keyboard_change_destination"])],
            [KeyboardButton(MESSAGES["keyboard_clear"])]
        ], resize_keyboard=True, one_time_keyboard=False))
        return

    # بناء قائمة InputMedia objects هنا، قبل إرسالها للـ Job
    input_media_list = []
    # يجب التأكد من أن original_caption سيتم أخذه من العنصر الأول فقط
    original_first_caption = raw_media_data[0]['original_caption'] if raw_media_data else None

    for idx, media_data in enumerate(raw_media_data):
        file_id = media_data['file_id']
        media_type = media_data['type']
        
        current_caption = None
        if idx == 0: # التعليق يطبق على أول عنصر فقط
             current_caption = album_caption # نستخدم التعليق الذي اختاره المستخدم
        
        if media_type == "photo":
            input_media_list.append(InputMediaPhoto(media=file_id, caption=current_caption, parse_mode=ParseMode.HTML))
        elif media_type == "video":
            input_media_list.append(InputMediaVideo(media=file_id, caption=current_caption, supports_streaming=True, parse_mode=ParseMode.HTML))


    job_name = f"forward_album_{album_identifier}"

    context.job_queue.run_once(
        _process_and_forward_album_job,
        0, # إرسال فوري، التأخير يتم معالجته داخل _process_and_forward_album
        data={
            "input_media_list": input_media_list, # نمرر InputMedia Objects الآن
            "user_chat_id": user_chat_id,
            "user_data_ref": context.user_data,
            "bot_instance": context.bot
        },
        name=job_name
    )

    # تنظيف البيانات المؤقتة بعد جدولة المهمة
    context.user_data.pop('current_album_raw_media_data', None) # تغيير هنا
    context.user_data.pop('current_media_group_id', None)
    context.user_data.pop('chosen_album_caption', None)


async def _process_and_forward_album_job(context: ContextTypes.DEFAULT_TYPE):
    """
    مهمة JobQueue لتحويل الألبوم فعلياً.
    تُستدعى من JobQueue، لذلك تمرير البيانات يكون عبر context.job.data.
    """
    job_data = context.job.data
    input_media_list = job_data["input_media_list"] # هذا الآن InputMedia list
    user_chat_id_for_job = job_data["user_chat_id"]
    user_data_ref = job_data["user_data_ref"]
    bot_instance = job_data["bot_instance"]

    async with _forward_lock:
        await _process_and_forward_album(
            input_media_list,
            user_chat_id_for_job,
            user_data_ref,
            bot_instance
        )


async def _process_and_forward_album(input_media_list: list, user_chat_id: int, user_data: dict, bot_instance):
    """
    وظيفة مساعدة لمعالجة وإرسال ألبوم (سواء كان مجموعة وسائط أو وسائط فردية).
    تستقبل البوت و user_data كوسائط.
    """
    target_chat_id = user_data.get("album_destination_chat_id")

    if not input_media_list:
        logger.warning(f"No input media items to forward for user {user_chat_id}, skipping album process.")
        return
    
    if target_chat_id is None:
        logger.error(f"Cannot forward album for user {user_chat_id}: Destination not set.")
        return

    # تطبيق التأخير الثابت بين إرسال الألبومات
    current_time = time.time()
    last_forward_time = user_data.get('_last_forward_timestamp', 0)
    time_since_last_forward = current_time - last_forward_time
    
    if last_forward_time != 0 and time_since_last_forward < FIXED_ALBUM_DELAY:
        delay_needed = FIXED_ALBUM_DELAY - time_since_last_forward
        logger.info(f"Delaying next album forwarding for {delay_needed:.2f} seconds for user {user_chat_id}.")
        await asyncio.sleep(delay_needed)

    user_data['_last_forward_timestamp'] = time.time()

    logger.info(f"Forwarding album ({len(input_media_list)} items) to {target_chat_id}.")

    success, sent_messages = await send_media_group_with_backoff(
        bot_instance=bot_instance,
        chat_id_to_send_to=target_chat_id,
        input_media_list=input_media_list, # هنا نمرر input_media_list مباشرة
        user_chat_id=user_chat_id
    )

    if success and sent_messages:
        if str(target_chat_id).startswith("-100"):
            try:
                if sent_messages and len(sent_messages) > 0:
                    await bot_instance.pin_chat_message(chat_id=target_chat_id, message_id=sent_messages[0].message_id, disable_notification=True)
                    logger.info(f"Pinned first message of album for user {user_chat_id} in channel {target_chat_id}.")
                else:
                    logger.warning(f"No messages were returned by send_media_group for user {user_chat_id}, cannot pin.")
            except TelegramError as e:
                logger.warning(f"Failed to pin message for user {user_chat_id} in channel {target_chat_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error during pinning for user {user_chat_id}: {e}")
    else:
        logger.error(f"Failed to forward album for user {user_chat_id}. No success message sent to user.")

    # إعادة لوحة المفاتيح الرئيسية برسالة نقطة بسيطة جداً
    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    await bot_instance.send_message(
        chat_id=user_chat_id,
        text=".",
        reply_markup=reply_markup
    )


# -------------------------------------------------------------
# دوال التنظيف وإعادة الضبط
# -------------------------------------------------------------

async def clear_all_temp_messages_after_delay(bot, chat_id, delay, context_user_data):
    """
    حذف كل الرسائل المؤقتة المخزنة في temp_messages_to_clean بعد تأخير زمني.
    """
    await asyncio.sleep(delay)

    if "temp_messages_to_clean" in context_user_data:
        message_ids = list(context_user_data["temp_messages_to_clean"])
        for msg_id in message_ids:
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


async def reset_bot_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    إعادة ضبط أي قوائم انتظار للوسائط أو مجموعات الوسائط المعلقة، وتنظيف البيانات.
    """
    chat_id = update.effective_chat.id

    await delete_messages_from_queue(context, chat_id)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data)
    context.user_data["temp_messages_to_clean"].clear()

    # مسح جميع البيانات المؤقتة المتعلقة بالتحويل النشط أو المعلق
    context.user_data.pop('current_album_raw_media_data', None)
    context.user_data.pop('current_media_group_id', None)
    context.user_data.pop('chosen_album_caption', None)
    context.user_data.pop('_last_forward_timestamp', None)


    if hasattr(context.application, 'job_queue') and context.application.job_queue is not None:
        jobs_to_cancel = [
            job for job in context.application.job_queue.get_jobs_by_name(f"forward_album_.*")
            if job.data and job.data.get("user_chat_id") == chat_id
        ]
        jobs_to_cancel.extend([
            job for job in context.application.job_queue.get_jobs_by_name(f"final_collect_job_.*")
            if job.data and job.data.get("user_chat_id") == chat_id
        ])
        
        for job in jobs_to_cancel:
            job.schedule_removal()
            logger.info(f"Cancelled job {job.name} for user {chat_id} during bot reset.")
        logger.info(f"Cancelled related jobs for user {chat_id} during bot reset.")

    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    await update.message.reply_text(
        MESSAGES["queue_cleared"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def cancel_current_album_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي محادثة التعليق الحالية وينظف البيانات ويعيد لوحة المفاتيح الرئيسية.
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
            logger.warning(f"Error deleting query message in cancel_current_album_process: {e}")

    await delete_messages_from_queue(context, chat_id)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data)
    context.user_data["temp_messages_to_clean"].clear()

    # مسح البيانات المؤقتة الخاصة بالتحويل الحالي
    context.user_data.pop('current_album_raw_media_data', None)
    context.user_data.pop('current_media_group_id', None)
    context.user_data.pop('chosen_album_caption', None)

    # إلغاء أي مهام JobQueue مرتبطة بهذا الألبوم الذي تم إلغاؤه
    if hasattr(context.application, 'job_queue') and context.application.job_queue is not None:
        jobs_to_cancel = [
            job for job in context.application.job_queue.get_jobs_by_name(f"forward_album_.*")
            if job.data and job.data.get("user_chat_id") == chat_id
        ]
        jobs_to_cancel.extend([
            job for job in context.application.job_queue.get_jobs_by_name(f"final_collect_job_.*")
            if job.data and job.data.get("user_chat_id") == chat_id
        ])
        for job in jobs_to_cancel:
            job.schedule_removal()
            logger.info(f"Cancelled job {job.name} for user {chat_id} during album process cancel.")

    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["cancel_operation"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


async def cancel_operation_general(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    يلغي أي عملية عامة (غير معالجة الألبومات) ويعيد لوحة المفاتيح الرئيسية.
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

    main_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup_main = ReplyKeyboardMarkup(main_keyboard, resize_keyboard=True, one_time_keyboard=False)

    await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["cancel_operation"],
        reply_markup=reply_markup_main
    )
    return ConversationHandler.END


# -------------------------------------------------------------
# دالة تشغيل البوت الرئيسية (main)
# -------------------------------------------------------------
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

    job_queue = JobQueue()
    application = Application.builder().token(token).job_queue(job_queue).build()

    # 1. ConversationHandler لضبط الوجهة الأولية أو لتغييرها
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

    # 2. ConversationHandler لعملية استقبال الألبوم واختيار التعليق ثم إرساله
    album_forwarding_with_caption_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.PHOTO | filters.VIDEO, handle_incoming_media_and_start_flow),
        ],
        states={
            COLLECTING_MEDIA_GROUP: [
                MessageHandler(filters.PHOTO | filters.VIDEO, handle_collecting_media_group),
                # IMPOR

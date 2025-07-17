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
    format="%(asctime)s - %(name)s - %(levelname)s - "%(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحالات للمحادثة
SETTING_GLOBAL_DESTINATION = 1
ASKING_FOR_CAPTION = 2
ASKING_FOR_MANUAL_CAPTION = 3

# Callbacks prefixes
SEND_LOC_CB_PREFIX = "sendloc_"
CAPTION_CB_PREFIX = "cap_"
CANCEL_CB_DATA = "cancel_op"

# الثوابت
FIXED_ALBUM_DELAY = 10 # التأخير الثابت بين كل ألبوم (مجموعة وسائط) يتم إرساله بالثواني.

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
    # لجمع أجزاء مجموعة الوسائط: مفتاح media_group_id، قيمة قاموس {media_items: [], user_chat_id: int}
    # هذه القائمة تستخدمها _final_media_group_collection_job لتأكيد الاكتمال
    if '_media_groups_pending' not in context.user_data:
        context.user_data['_media_groups_pending'] = {}
    if '_last_forward_timestamp' not in context.user_data:
        context.user_data['_last_forward_timestamp'] = 0
    # لتخزين الـ media_items و ID الخاص بالألبوم الذي تتم معالجته حالياً
    if 'current_album_media_items' not in context.user_data:
        context.user_data['current_album_media_items'] = []
    if 'current_media_group_id' not in context.user_data: # هذا سيتتبع الـ ID الفريد (media_group_id أو single_media_id)
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
async def send_media_group_with_backoff(bot_instance, chat_id_to_send_to: int, input_media, user_chat_id: int):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            sent_messages = await bot_instance.send_media_group(chat_id=chat_id_to_send_to, media=input_media)
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
# دوال معالجة الوسائط والألبومات (تضمنت الآن طلب التعليق)
# -------------------------------------------------------------

async def _start_caption_conversation_for_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    تُستدعى كـ entry point لـ ConversationHandler بعد استلام وسائط.
    تخزن الوسائط وتبدأ عملية اختيار التعليق.
    """
    user_chat_id = update.effective_chat.id
    await initialize_user_data(context) # تأكد أن بيانات المستخدم مهيأة

    # تحقق من ضبط الوجهة أولاً
    if context.user_data.get("album_destination_chat_id") is None:
        await update.message.reply_text(MESSAGES["destination_not_set_error"])
        # إعادة لوحة المفاتيح
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
        return ConversationHandler.END # إنهاء المحادثة الحالية (التي بدأها إرسال الوسائط)


    # مسح أي رسائل سابقة
    await delete_messages_from_queue(context, user_chat_id)

    message = update.message
    media_group_id = message.media_group_id
    current_album_identifier = media_group_id if media_group_id else f"single_media_{message.id}"
    
    file_id = None
    media_type = None
    caption = message.caption # الكابتشن الأصلي للرسالة

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    else:
        logger.debug(f"Received non-photo/video message from user {user_chat_id} - exiting _start_caption_conversation_for_media.")
        return ConversationHandler.END # ليس صورة ولا فيديو، تجاهل وإنهاء المحادثة


    if media_type:
        input_media_item = None
        if media_type == "photo":
            # نحافظ على الكابتشن الأصلي الذي أرسله المستخدم، وسيتم تطبيقه على أول عنصر
            input_media_item = InputMediaPhoto(media=file_id, caption=caption, parse_mode=ParseMode.HTML)
        elif media_type == "video":
            input_media_item = InputMediaVideo(media=file_id, caption=caption, supports_streaming=True, parse_mode=ParseMode.HTML)

        if input_media_item:
            # تحديث current_album_media_items لجمع جميع الوسائط الجديدة
            if 'current_media_group_id' not in context.user_data or context.user_data['current_media_group_id'] != current_album_identifier:
                # إذا كانت هذه بداية ألبوم جديد أو وسيطة فردية
                context.user_data['current_album_media_items'] = []
                context.user_data['current_media_group_id'] = current_album_identifier
            
            context.user_data['current_album_media_items'].append(input_media_item)

            # اذا كان جزء من مجموعة وسائط, ننتظر لاستلام جميع الاجزاء
            if media_group_id:
                job_name = f"final_collect_job_{media_group_id}"
                current_jobs = context.job_queue.get_jobs_by_name(job_name)
                for job in current_jobs:
                    job.schedule_removal() # إلغاء المهام السابقة لتلك المجموعة لتحديث الوقت

                # جدولة مهمة نهائية لضمان تجميع كل الأجزاء
                # هذه المهمة ستُرسل أزرار التعليق عندما تتأكد من اكتمال المجموعة
                context.job_queue.run_once(
                    _final_media_group_collection_job,
                    1, # تأخير كافٍ لجمع جميع الأجزاء المتتالية
                    data={"media_group_id": media_group_id, "user_chat_id": user_chat_id, "user_data_ref": context.user_data},
                    name=job_name
                )
                # ننتقل إلى حالة انتظار التعليق هنا، الـ Job سيكمل إرسال الأزرار
                return ASKING_FOR_CAPTION 
            else:
                # إذا كانت وسائط مفردة، نسأل عن التعليق مباشرة لأنها لا تنتظر المزيد من الأجزاء
                return await ask_for_caption_and_send_prompt(update, context)
        else:
            logger.warning(f"Failed to create input_media_item for received media from user {user_chat_id}.")
            return ConversationHandler.END
    else:
        logger.warning(f"No media type detected for message from user {user_chat_id}.")
        return ConversationHandler.END


async def _final_media_group_collection_job(context: ContextTypes.DEFAULT_TYPE):
    """
    يُستدعى بواسطة JobQueue بعد مرور وقت قصير من استقبال آخر جزء من مجموعة وسائط.
    وظيفتها التأكد من اكتمال المجموعة، ثم إظهار prompt التعليق.
    """
    job_data = context.job.data
    media_group_id = job_data["media_group_id"]
    user_chat_id = job_data["user_chat_id"]
    user_data_ref = job_data["user_data_ref"]

    # يجب أن نتأكد أن هذه هي المجموعة التي يعمل عليها ConversationHandler
    # ونجنب إرسال رسالة تعليق إذا لم تكن هذه هي المجموعة النشطة حاليا.
    if user_data_ref.get('current_media_group_id') == media_group_id:
        # هنا تم تجميع المجموعة بالكامل، الآن نرسل أزرار اختيار التعليق.
        logger.info(f"Media group {media_group_id} collected for user {user_chat_id}. Prompting for caption.")

        inline_keyboard_buttons = []
        for i, caption_text in enumerate(PREDEFINED_CAPTION_OPTIONS):
            inline_keyboard_buttons.append([InlineKeyboardButton(caption_text, callback_data=f"{CAPTION_CB_PREFIX}{i}")])
        inline_keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])
        inline_markup = InlineKeyboardMarkup(inline_keyboard_buttons)
        
        prompt_msg = await context.bot.send_message(
            chat_id=user_chat_id, # chat_id للمستخدم الذي أرسل الألبوم
            text=MESSAGES["album_caption_prompt"],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=inline_markup
        )
        user_data_ref["messages_to_delete"].append(prompt_msg.message_id)
        # Note: A Job cannot change the state of an active ConversationHandler directly.
        # The ConversationHandler's state is managed by its own `handle_update` loop.
        # However, _start_caption_conversation_for_media already set the state to ASKING_FOR_CAPTION,
        # so when the user clicks a button, it will be caught by CallbackQueryHandler for that state.
    else:
        logger.debug(f"Job triggered for {media_group_id} but it's not the current active media group or already handled for user {user_chat_id}.")


async def ask_for_caption_and_send_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    وظيفة مساعدة لطباعة أزرار اختيار التعليق للوسائط المفردة.
    """
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
                # البدء في تحويل الألبوم مباشرة
                await _trigger_album_forward(update, context) # Pass update
                return ConversationHandler.END
            else:
                context.user_data["chosen_album_caption"] = selected_option_text
                # البدء في تحويل الألبوم مباشرة
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

    # البدء في تحويل الألبوم مباشرة
    await _trigger_album_forward(update, context) # Pass update

    return ConversationHandler.END


async def _trigger_album_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    وظيفة مساعدة لجدولة مهمة تحويل الألبوم بعد تحديد التعليق.
    تستقبل Update لتتمكن من استخراج effective_chat.id.
    """
    user_chat_id = update.effective_chat.id # استخدام update.effective_chat.id للحصول على chat ID
    
    # Fetch collected media and caption from user_data
    album_identifier = context.user_data.get('current_media_group_id')
    media_items_to_send = context.user_data.get('current_album_media_items', [])
    album_caption = context.user_data.get('chosen_album_caption', "")

    if not media_items_to_send or album_identifier is None:
        logger.error(f"No media items or identifier found for user {user_chat_id} when attempting to trigger album forward.")
        # يمكن إرسال رسالة خطأ صامتة (نقطة) لإعادة لوحة المفاتيح
        await context.bot.send_message(chat_id=user_chat_id, text=".", reply_markup=ReplyKeyboardMarkup([
            [KeyboardButton(MESSAGES["keyboard_change_destination"])],
            [KeyboardButton(MESSAGES["keyboard_clear"])]
        ], resize_keyboard=True, one_time_keyboard=False))
        return

    # جدولة مهمة تحويل الألبوم الفعلي عبر JobQueue
    job_name = f"forward_album_{album_identifier}"

    # نُمرر مرجع لـ user_data لضمان الوصول الصحيح من Job
    # ونُمرر كائن الـ bot أيضاً حيث أن الـ Job ليس له 'bot' مباشرة
    context.job_queue.run_once(
        _process_and_forward_album_job,
        0, # إرسال فوري، التأخير يتم معالجته داخل _process_and_forward_album
        data={
            "album_media_items": media_items_to_send,
            "album_caption": album_caption,
            "user_chat_id": user_chat_id,
            "user_data_ref": context.user_data,
            "bot_instance": context.bot # تمرير كائن البوت
        },
        name=job_name
    )

    # تنظيف البيانات المؤقتة بعد جدولة المهمة
    context.user_data.pop('current_album_media_items', None)
    context.user_data.pop('current_media_group_id', None)
    context.user_data.pop('chosen_album_caption', None)


async def _process_and_forward_album_job(context: ContextTypes.DEFAULT_TYPE):
    """
    مهمة JobQueue لتحويل الألبوم فعلياً.
    تُستدعى من JobQueue، لذلك تمرير البيانات يكون عبر context.job.data.
    """
    job_data = context.job.data
    media_items_to_send = job_data["album_media_items"]
    album_caption = job_data["album_caption"]
    user_chat_id_for_job = job_data["user_chat_id"]
    user_data_ref = job_data["user_data_ref"]
    bot_instance = job_data["bot_instance"] # استعادة كائن البوت

    async with _forward_lock:
        # تمرير الكائنات الضرورية (bot, user_data) إلى وظيفة المساعدة
        await _process_and_forward_album(
            media_items_to_send,
            album_caption,
            user_chat_id_for_job,
            user_data_ref,
            bot_instance
        )


async def _process_and_forward_album(media_items: list, album_caption: str, user_chat_id: int, user_data: dict, bot_instance):
    """
    وظيفة مساعدة لمعالجة وإرسال ألبوم (سواء كان مجموعة وسائط أو وسائط فردية).
    تستقبل البوت و user_data كوسائط.
    """
    target_chat_id = user_data.get("album_destination_chat_id")

    if not media_items:
        logger.warning(f"No media items to forward for user {user_chat_id}, skipping album process.")
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

    # تطبيق التعليق على العنصر الأول في الألبوم
    if media_items and album_caption is not None:
        media_items[0].caption = album_caption

    logger.info(f"Forwarding album ({len(media_items)} items) with caption '{album_caption[:50]}...' for user {user_chat_id} to {target_chat_id}.")

    # استخدام دالة send_media_group_with_backoff للتحويل
    success, sent_messages = await send_media_group_with_backoff(
        bot_instance=bot_instance,
        chat_id_to_send_to=target_chat_id,
        input_media=media_items,
        user_chat_id=user_chat_id
    )

    if success and sent_messages:
        # التثبيت يتم فقط في القنوات (معرفها يبدأ بـ -100)
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
        text=".", # رسالة قصيرة جداً لإعادة الكيبورد
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
    context.user_data.pop('current_album_media_items', None)
    context.user_data.pop('current_media_group_id', None)
    context.user_data.pop('chosen_album_caption', None)

    if hasattr(context.application, 'job_queue') and context.application.job_queue is not None:
        # إلغاء مهام تجميع الوسائط ومها التوجيه المعلقة لهذا المستخدم
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

    context.user_data['_last_forward_timestamp'] = 0 # إعادة تعيين العداد الزمني

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
    context.user_data.pop('current_album_media_items', None)
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
            CommandHandler("start", start) # /start هو نقطة دخول لبدء التفاعل وإظهار الكيبورد
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
            MessageHandler(filters.PHOTO | filters.VIDEO, _start_caption_conversation_for_media),
        ],
        states={
            ASKING_FOR_CAPTION: [
                CallbackQueryHandler(handle_caption_choice, pattern=f"^{CAPTION_CB_PREFIX}.*|^({CANCEL_CB_DATA})$"),
                # لا نضيف هنا MessageHandler لـ filters.TEXT لأننا نريد أن يلتقط receive_manual_album_caption فقط النص
                # MessageHandler(filters.TEXT & ~filters.COMMAND, ... invalid input)
            ],
            ASKING_FOR_MANUAL_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_album_caption),
            ],
        },
        fallbacks=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_bot_state),
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_destination'])}$") & ~filters.COMMAND, cancel_current_album_process),
            CommandHandler("cancel", cancel_current_album_process),
            CommandHandler("start", cancel_current_album_process),
            CommandHandler("help", cancel_current_album_process),
            CommandHandler("settings", cancel_current_album_process),
            CommandHandler("source", cancel_current_album_process),
            MessageHandler(filters.ALL & ~filters.COMMAND, cancel_current_album_process)
        ],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )


    # إضافة Handlers إلى الـ Application
    application.add_handler(destination_setting_conversation_handler) # معالج ضبط الوجهة يأتي أولاً
    application.add_handler(album_forwarding_with_caption_conversation_handler) # معالج الألبومات يأتي ثانياً ليلتقط الصور/الفيديوهات


    # الأوامر الرئيسية التي تعمل خارج المحادثات
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    # زر "إعادة تعيين البوت" خارج المحادثة أيضاً
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_bot_state))

    # معالج أي رسائل نصية أخرى (لا تتعلق بالأوامر أو أزرار لوحة المفاتيح أو المحادثات الجارية)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text(MESSAGES["success_message_permanent_prompt"], reply_markup=ReplyKeyboardMarkup([[KeyboardButton(MESSAGES["keyboard_change_destination"])],[KeyboardButton(MESSAGES["keyboard_clear"])]], resize_keyboard=True, one_time_keyboard=False))))


    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C).")
    except Exception as e:
        logger.exception("An unhandled exception occurred in the bot:")

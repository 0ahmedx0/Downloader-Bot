import os
import asyncio
import logging
import random
import math
import re
import time # سنحتاج إلى time لتتبع أوقات التأخير

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
    JobQueue # لاستقبال مجموعات الوسائط بشكل صحيح
)
from telegram.error import RetryAfter, TelegramError, BadRequest
from telegram.constants import ParseMode


# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# الحالات للمحادثة (سنبقي حالة واحدة لضبط الوجهة)
SETTING_GLOBAL_DESTINATION = 1

# Callbacks prefixes
SEND_LOC_CB_PREFIX = "sendloc_"
CANCEL_CB_DATA = "cancel_op"

# الرسائل المستخدمة (تم تعديل بعضها ليعكس الوظيفة الجديدة)
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
        'سيتم تطبيق تأخير 10 ثوانٍ بين كل ألبوم والآخر.\n\n'
        'استخدم "تغيير وجهة الألبوم" لتغيير الوجهة، و"إعادة تعيين البوت" لمسح أي مهام معلقة.\n\n'
        "هذا العمل تم بواسطة @wjclub."
    ),
    "settings": "لا توجد إعدادات لتغييرها هنا.",
    "source": "https://github.com/wjclub/telegram-bot-album-creator",
    "keyboard_process": "جلب الوسائط وتحويلها", # تغير اسم الزر "إنشاء ألبوم"
    "keyboard_clear": "إعادة تعيين البوت", # تغير اسم الزر "إعادة تعيين الألبوم"
    "keyboard_change_destination": "تغيير وجهة الألبوم 🔄",
    "queue_cleared": "تم مسح قائمة التحويلات المعلقة.",
    "album_forward_started": "⏳ تم استقبال الألبوم وجاري التحضير لإعادة التوجيه...",
    "progress_update": "جاري إرسال الألبوم: *{processed_albums}/{total_albums}*\nالوقت المتبقي المقدر: *{time_remaining_str}*.",
    "cancel_operation": "تم إلغاء العملية.",
    "ask_send_location": "أين تود إرسال الألبومات؟",
    "send_to_channel_button": "القناة 📢",
    "send_to_chat_button": "المحادثة معي 👤",
    "channel_id_missing": "❌ لم يتم ضبط معرف القناة (CHANNEL_ID) في بيئة البوت. لا يمكن الإرسال للقناة. الرجاء الاتصال بالمطور.",
    "invalid_input_choice": "خيار غير صالح أو إدخال غير متوقع. الرجاء الاختيار من الأزرار أو إلغاء العملية.",
    "success_message_permanent_prompt": "يمكنك الآن إرسال المزيد من الألبومات أو استخدام الأزرار أدناه.",
}

# دالة التأخير
async def get_fixed_delay(delay=10):
    """تؤخر التنفيذ بمقدار ثابت."""
    await asyncio.sleep(delay)

# لضمان عدم تداخل إرسال الألبومات
_forward_lock = asyncio.Lock()

# تهيئة بيانات المستخدم
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE):
    """يضمن تهيئة context.user_data والمتغيرات الضرورية."""
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
    # لجمع أجزاء مجموعة الوسائط
    if '_media_groups_pending' not in context.user_data:
        context.user_data['_media_groups_pending'] = {}
    # لتطبيق التأخير بين كل ألبوم
    if '_last_forward_timestamp' not in context.user_data:
        context.user_data['_last_forward_timestamp'] = 0

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

    # أزرار لوحة المفاتيح الرئيسية
    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
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


# إرسال الوسائط مع التعامل مع فيضانات تيليجرام (بدون رسائل للمستخدم)
async def send_media_group_with_backoff(context: ContextTypes.DEFAULT_TYPE, chat_id_to_send_to: int, input_media, chunk_index: int, user_chat_id: int):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            sent_messages = await context.bot.send_media_group(chat_id=chat_id_to_send_to, media=input_media)
            return True, sent_messages
        except RetryAfter as e:
            logger.warning("RetryAfter: chunk %d, attempt %d. Waiting for %s seconds.",
                           chunk_index + 1, attempt + 1, e.retry_after)
            await asyncio.sleep(e.retry_after)
        except TelegramError as e:
            logger.error("TelegramError sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
            return False, None
        except Exception as e:
            logger.error("Generic Error sending album chunk %d on attempt %d: %s",
                         chunk_index + 1, attempt + 1, e)
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

async def handle_incoming_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    يجمع أجزاء مجموعة الوسائط أو يعالج وسائط مفردة.
    """
    await initialize_user_data(context)
    user_chat_id = update.effective_chat.id
    target_chat_id = context.user_data.get("album_destination_chat_id")

    if target_chat_id is None:
        await update.message.reply_text(MESSAGES["destination_not_set_error"])
        # لإخفاء الكيبورد اليدوية التي قد تظهر بسبب عدم وجود ConversationHandler للميديا
        # و لإرشاد المستخدم لضبط الوجهة، نلجأ لإعادة الـ ReplyKeyboardMarkup.
        reply_keyboard = [
            [KeyboardButton(MESSAGES["keyboard_change_destination"])],
            [KeyboardButton(MESSAGES["keyboard_clear"])]
        ]
        reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
        await context.bot.send_message(
            chat_id=user_chat_id,
            text=MESSAGES["success_message_permanent_prompt"], # استخدام نفس الرسالة لإعادة لوحة المفاتيح
            reply_markup=reply_markup
        )
        return

    message = update.message
    media_group_id = message.media_group_id
    file_id = None
    media_type = None

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    else:
        # لن يحدث هذا عمليًا إذا كان الفلتر filters.PHOTO | filters.VIDEO
        return

    input_media_item = None
    if media_type == "photo":
        input_media_item = InputMediaPhoto(media=file_id, caption=None) # لا يوجد تعليق مخصص الآن
    elif media_type == "video":
        input_media_item = InputMediaVideo(media=file_id, caption=None)

    if input_media_item:
        if media_group_id:
            # جزء من مجموعة وسائط
            if media_group_id not in context.user_data['_media_groups_pending']:
                context.user_data['_media_groups_pending'][media_group_id] = {
                    'media_items': [],
                    'user_chat_id': user_chat_id
                }
            context.user_data['_media_groups_pending'][media_group_id]['media_items'].append(input_media_item)

            # جدولة مهمة لمعالجة المجموعة بعد تأخير قصير لضمان وصول جميع الأجزاء
            # إلغاء أي مهمة سابقة لنفس media_group_id لعدم المعالجة المبكرة
            job_name = f"process_media_group_{media_group_id}"
            current_jobs = context.job_queue.get_jobs_by_name(job_name)
            for job in current_jobs:
                job.schedule_removal()
            context.job_queue.run_once(_process_and_forward_album_job, 0.5, # تأخير 0.5 ثانية لجمع باقي الأجزاء
                                       context={"media_group_id": media_group_id, "user_chat_id": user_chat_id},
                                       name=job_name)
        else:
            # وسائط مفردة (ليست جزءاً من مجموعة)
            # تعامل معها كألبوم مكون من عنصر واحد
            await _process_and_forward_album([input_media_item], user_chat_id, context)


async def _process_and_forward_album_job(context: ContextTypes.DEFAULT_TYPE):
    """
    مهمة JobQueue لمعالجة وإعادة توجيه مجموعة وسائط مكتملة.
    """
    job_context_data = context.job.context
    media_group_id = job_context_data["media_group_id"]
    user_chat_id_for_data = job_context_data["user_chat_id"] # المستخدم الذي أرسل الألبوم

    async with _forward_lock: # تأكد من أن ألبومًا واحدًا فقط يتم معالجته في كل مرة
        if media_group_id not in context.user_data.get('_media_groups_pending', {}):
            # تم معالجة المجموعة بالفعل أو مسحها (ربما عن طريق زر إعادة تعيين)
            return

        album_data = context.user_data['_media_groups_pending'].pop(media_group_id)
        media_items_to_send = album_data['media_items']
        
        await _process_and_forward_album(media_items_to_send, user_chat_id_for_data, context)

async def _process_and_forward_album(media_items: list, user_chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    وظيفة مساعدة لمعالجة وإرسال ألبوم (سواء كان مجموعة وسائط أو وسائط فردية).
    """
    target_chat_id = context.user_data.get("album_destination_chat_id")

    if not media_items:
        logger.warning(f"No media items to forward for user {user_chat_id}, skipping.")
        return

    # تطبيق 10 ثوانٍ تأخير بين إرسال الألبومات
    current_time = time.time()
    last_forward_time = context.user_data.get('_last_forward_timestamp', 0)
    time_since_last_forward = current_time - last_forward_time
    if time_since_last_forward < 10:
        delay_needed = 10 - time_since_last_forward
        logger.info(f"Delaying next album forwarding for {delay_needed:.2f} seconds.")
        # هذه رسالة التقدم فقط في الخلفية، لا تظهر للمستخدم
        await asyncio.sleep(delay_needed)
        context.user_data['_last_forward_timestamp'] = time.time()
    else:
        context.user_data['_last_forward_timestamp'] = current_time

    # لا توجد رسالة "جاري الإعداد" للمستخدم الآن، فقط الأداء في الخلفية
    logger.info(f"Forwarding album ({len(media_items)} items) for user {user_chat_id} to {target_chat_id}.")

    # الآن نرسل الألبوم الفعلي
    success, sent_messages = await send_media_group_with_backoff(
        context=context,
        chat_id_to_send_to=target_chat_id,
        input_media=media_items,
        chunk_index=0,
        user_chat_id=user_chat_id # يستخدم في تسجيل الأخطاء فقط، وليس لإرسال رسائل للمستخدم
    )

    if success and sent_messages:
        if str(target_chat_id).startswith("-100"): # فقط في القنوات
            try:
                await context.bot.pin_chat_message(chat_id=target_chat_id, message_id=sent_messages[0].message_id, disable_notification=True)
                logger.info(f"Pinned first message of album in channel {target_chat_id}.")
            except Exception as pin_err:
                logger.warning(f"Failed to pin first message of album in channel {target_chat_id}: {pin_err}.")
    else:
        logger.error(f"Failed to forward album for user {user_chat_id}. No success message sent to user.")

    # مسح قائمة الانتظار للمستخدم هذا
    # ليس هناك media_queue بمعناها السابق، ولكن نضمن تنظيف المتغيرات المؤقتة
    # context.user_data["media_queue"] = [] # لم نعد نستخدمها بهذه الطريقة

    # لا رسالة للمستخدم هنا بعد الانتهاء من الإرسال (نجاح أو فشل)
    # ولكن لوحة المفاتيح يجب أن تعود للظهور إذا أزيلت مسبقًا
    reply_keyboard = [
        [KeyboardButton(MESSAGES["keyboard_change_destination"])],
        [KeyboardButton(MESSAGES["keyboard_clear"])]
    ]
    reply_markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, one_time_keyboard=False)
    # نرسل رسالة تذكيرية عامة مع الكيبورد لضمان ظهورها مرة أخرى
    await context.bot.send_message(
        chat_id=user_chat_id,
        text="...", # رسالة قصيرة جدًا أو نقطة
        reply_markup=reply_markup
    )


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


async def reset_album_and_pending_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    إعادة ضبط أي قوائم انتظار للوسائط أو مجموعات الوسائط المعلقة.
    """
    chat_id = update.effective_chat.id

    await delete_messages_from_queue(context, chat_id)
    await clear_all_temp_messages_after_delay(context.bot, chat_id, 0, context.user_data)
    context.user_data["temp_messages_to_clean"].clear()

    # إلغاء أي مهام معلقة لتحويل مجموعات الوسائط لهذا المستخدم
    if '_media_groups_pending' in context.user_data:
        context.user_data['_media_groups_pending'] = {}
        for job in context.job_queue.get_jobs_by_name(f"process_media_group_.*"): # استخدم النمط لمسح مهام المستخدم
            # تحقق من أن المهمة تعود لهذا المستخدم المحدد
            if job.context and job.context.get("user_chat_id") == chat_id:
                job.schedule_removal()
                logger.info(f"Cancelled job {job.name} for user {chat_id}.")
        logger.info(f"Cleared pending media groups and cancelled related jobs for user {chat_id}.")


    context.user_data.pop("progress_message_id", None)
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

    # عند إلغاء عملية (مثل ضبط الوجهة)، نلغي المهام المعلقة لذلك المستخدم
    if '_media_groups_pending' in context.user_data:
        # ليس بالضرورة مسح _media_groups_pending إذا لم تكن هي الهدف الرئيسي للإلغاء
        # لكن إلغاء أي مهام متعلقة بمجموعة وسائط لضمان عدم حدوث معالجة غير مرغوبة
        for job in context.job_queue.get_jobs_by_name(f"process_media_group_.*"):
            if job.context and job.context.get("user_chat_id") == chat_id:
                job.schedule_removal()
                logger.info(f"Cancelled job {job.name} for user {chat_id} during general cancel.")


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

    # JobQueue للاستخدام مع مجموعات الوسائط
    job_queue: JobQueue = application.job_queue

    # ConversationHandler لضبط الوجهة
    destination_setting_conversation_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_change_destination'])}$") & ~filters.COMMAND, prompt_for_destination_setting),
            CommandHandler("start", start) # /start هو نقطة دخول أيضًا لضمان السؤال الأول
        ],
        states={
            SETTING_GLOBAL_DESTINATION: [
                CallbackQueryHandler(handle_global_destination_choice, pattern=f"^{SEND_LOC_CB_PREFIX}.*|^({CANCEL_CB_DATA})$"),
                MessageHandler(filters.ALL & ~filters.COMMAND, lambda u,c: u.effective_message.reply_text(MESSAGES["invalid_input_choice"])),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_operation_general),
            # أي رسائل أخرى أثناء المحادثة يتم إلغاؤها بشكل عام
            MessageHandler(filters.ALL & ~filters.COMMAND, cancel_operation_general)
        ],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END # If the sub-conversation ends, the main bot loop resumes
        }
    )

    application.add_handler(destination_setting_conversation_handler)

    # الأوامر الأساسية الأخرى
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("source", source_command))

    # معالج الرسائل التي تحتوي على صور أو فيديوهات (بما في ذلك مجموعات الوسائط)
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_incoming_media))

    # معالج زر "إعادة تعيين البوت"
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$") & ~filters.COMMAND, reset_album_and_pending_groups))

    # إذا أرسل المستخدم رسالة نصية لا تتعلق بأمر أو زر وهي ليست جزءًا من محادثة
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda update, context: update.message.reply_text(MESSAGES["success_message_permanent_prompt"])))


    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

import os
import asyncio
import logging
import re

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
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
from telegram.error import TelegramError, BadRequest
from telegram.constants import ParseMode

# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# حالات المحادثة
ASKING_FOR_MANUAL_CAPTION = 2

# Callbacks prefixes
MANUAL_CAPTION_CB_DATA = "cap_manual"
NO_CAPTION_CB_DATA = "cap_none"
CANCEL_CB_DATA = "cancel_op"

# الرسائل المستخدمة
MESSAGES = {
    "greeting": (
        "مرحباً {username}! \n\n"
        "هذا البوت يقوم بعكس وظيفة التجميع: "
        "أرسل لي ألبوم صور أو فيديوهات من تيليجرام، وسأقوم بتفكيكه وإعادة إرساله "
        "على شكل رسائل منفصلة.\n\n"
        "الآن يمكنك إرسال أكثر من ألبوم، والبوت سيقوم بتفكيكهم كلهم.\n\n"
        "لإظهار الأزرار الرئيسية، استخدم الأمر /keyboard."
    ),
    "help": (
        "طريقة الاستخدام:\n\n"
        "1) أرسل ألبوماً أو عدة ألبومات من تيليجرام.\n"
        "2) بعد اكتمال استلام الألبومات، سيظهر لك اختيار التعليق.\n"
        "3) يمكنك اختيار تعليق جاهز، أو إدخال تعليق يدوي، أو الإرسال بدون تعليق.\n"
        "4) سيقوم البوت بإعادة إرسال كل عنصر من جميع الألبومات كرسائل مستقلة.\n\n"
        "ملاحظة: يعمل هذا فقط مع الألبومات، وليس مع صورة واحدة منفصلة.\n\n"
        "هذا العمل تم بواسطة @wjclub.\n\n"
        "لإظهار الأزرار الرئيسية، يمكنك استخدام الأمر /keyboard."
    ),
    "keyboard_done": "تفكيك الألبومات",
    "keyboard_clear": "إعادة تعيين",
    "keyboard_shown": "هذه هي الأزرار الرئيسية:",
    "keyboard_hidden": "تم إخفاء لوحة المفاتيح. لإظهارها مرة أخرى، استخدم الأمر /keyboard.",
    "no_album_detected": "⚠️ لم يتم العثور على ألبومات صالحة بعد. أرسل ألبوماً أولاً.",
    "album_caption_prompt": "الرجاء اختيار تعليق للعناصر بعد تفكيك جميع الألبومات الجاهزة:",
    "album_caption_manual_prompt": (
        "الرجاء إدخال التعليق الذي تريده للعناصر المفككة.\n\n"
        "إذا كنت لا تريد أي تعليق، فقط أرسل نقطة `.`"
    ),
    "processing_start": "⏳ جاري تفكيك جميع الألبومات وإعادة إرسال العناصر بشكل منفصل...",
    "not_album_item": "⚠️ هذا البوت يعالج الألبومات فقط. أرسل ألبوماً وليس ملفاً منفرداً.",
    "queue_cleared": "تمت إعادة التعيين وحذف جميع الألبومات المخزنة مؤقتاً.",
    "cancel_operation": "تم إلغاء العملية.",
    "album_comment_option_manual": "إدخال تعليق يدوي ✍️",
    "done_success": "✅ تم تفكيك جميع الألبومات الجاهزة وإرسال العناصر كرسائل منفصلة.",
}

PREDEFINED_CAPTION_OPTIONS = {
    "cap_1": "حصريات عربي 🌈🔥.",
    "cap_2": "حصريات اجنبي 🌈🔥.",
    "cap_3": "صيني منوع فخم🌈💫",
    "cap_4": "اجنبي منوع فخم🌈🦋",
    "cap_5": "عربي منوع فخم🌈👅",
}


# --- دوال لوحة المفاتيح الرئيسية ---
def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [
            KeyboardButton(MESSAGES["keyboard_done"]),
            KeyboardButton(MESSAGES["keyboard_clear"]),
        ]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)


async def show_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        MESSAGES["keyboard_shown"],
        reply_markup=get_main_keyboard()
    )


async def hide_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        MESSAGES["keyboard_hidden"],
        reply_markup=ReplyKeyboardRemove()
    )


# --- تهيئة بيانات المستخدم ---
async def initialize_user_data(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    user_data = context.user_data

    if "album_buffer" not in user_data:
        user_data["album_buffer"] = {}  # media_group_id -> items
    if "messages_to_delete" not in user_data:
        user_data["messages_to_delete"] = []
    if "processing_started" not in user_data:
        user_data["processing_started"] = False
    if "processing_tasks" not in user_data:
        user_data["processing_tasks"] = {}  # media_group_id -> task
    if "ready_album_ids" not in user_data:
        user_data["ready_album_ids"] = []  # كل الألبومات الجاهزة للتفكيك
    if "caption_prompt_shown" not in user_data:
        user_data["caption_prompt_shown"] = False

    user_data["album_destination_chat_id"] = chat_id


# --- تجميع الألبوم الوارد ---
async def handle_album_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await initialize_user_data(context, update.effective_chat.id)

    message = update.message
    if not message:
        return

    if not message.media_group_id:
        await update.message.reply_text(MESSAGES["not_album_item"])
        return

    media_group_id = str(message.media_group_id)
    album_buffer = context.user_data["album_buffer"]
    processing_tasks = context.user_data["processing_tasks"]

    if media_group_id not in album_buffer:
        album_buffer[media_group_id] = []

    item = None
    if message.photo:
        item = {
            "type": "photo",
            "file_id": message.photo[-1].file_id,
            "original_caption": message.caption or "",
        }
    elif message.video:
        item = {
            "type": "video",
            "file_id": message.video.file_id,
            "original_caption": message.caption or "",
        }

    if not item:
        return

    album_buffer[media_group_id].append(item)

    logger.info(f"Added media item to album {media_group_id}. Total: {len(album_buffer[media_group_id])}")

    # إعادة جدولة التأكد من اكتمال هذا الألبوم فقط
    old_task = processing_tasks.get(media_group_id)
    if old_task and not old_task.done():
        old_task.cancel()

    processing_tasks[media_group_id] = asyncio.create_task(
        mark_album_ready_after_delay(update, context, media_group_id)
    )


async def mark_album_ready_after_delay(update: Update, context: ContextTypes.DEFAULT_TYPE, media_group_id: str):
    try:
        await asyncio.sleep(5)  # انتظار وصول بقية عناصر الألبوم
    except asyncio.CancelledError:
        return

    album_items = context.user_data.get("album_buffer", {}).get(media_group_id, [])
    ready_album_ids = context.user_data.get("ready_album_ids", [])

    if len(album_items) < 2:
        return

    if media_group_id not in ready_album_ids:
        ready_album_ids.append(media_group_id)
        logger.info(f"Album {media_group_id} marked as ready. Ready albums: {ready_album_ids}")

    # إذا لم يكن هناك نافذة اختيار ظاهرة حالياً، اعرضها تلقائياً
    if not context.user_data.get("processing_started", False) and not context.user_data.get("caption_prompt_shown", False):
        await start_album_split_process(update, context, is_auto_trigger=True)

# --- بدء عملية التفكيك: اختيار التعليق ---
async def start_album_split_process(update: Update, context: ContextTypes.DEFAULT_TYPE, is_auto_trigger: bool = False):
    chat_id = update.effective_chat.id
    await initialize_user_data(context, chat_id)

    if context.user_data.get("processing_started", False):
        return

    ready_album_ids = context.user_data.get("ready_album_ids", [])

    if not ready_album_ids:
        if not is_auto_trigger:
            await context.bot.send_message(
                chat_id=chat_id,
                text=MESSAGES["no_album_detected"],
                reply_markup=ReplyKeyboardRemove()
            )
        return

    if context.user_data.get("caption_prompt_shown", False):
        return

    context.user_data["processing_started"] = True
    context.user_data["caption_prompt_shown"] = True

    keyboard = []
    for key, text in PREDEFINED_CAPTION_OPTIONS.items():
        keyboard.append([InlineKeyboardButton(text, callback_data=key)])

    keyboard.append([InlineKeyboardButton(MESSAGES["album_comment_option_manual"], callback_data=MANUAL_CAPTION_CB_DATA)])
    keyboard.append([InlineKeyboardButton("لا يوجد تعليق", callback_data=NO_CAPTION_CB_DATA)])
    keyboard.append([InlineKeyboardButton("❌ إلغاء", callback_data=CANCEL_CB_DATA)])

    prompt_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["album_caption_prompt"],
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.user_data["messages_to_delete"].append(prompt_msg.message_id)


# --- معالجات اختيار التعليق ---
async def handle_predefined_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    caption_key = query.data
    user_caption = PREDEFINED_CAPTION_OPTIONS.get(caption_key, "")
    context.user_data["current_caption"] = user_caption

    await finalize_split_action(update, context)


async def handle_no_caption_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    context.user_data["current_caption"] = ""
    await finalize_split_action(update, context)


# --- إدخال التعليق اليدوي ---
async def prompt_for_manual_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    try:
        await query.delete_message()
    except BadRequest:
        pass

    prompt_msg = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=MESSAGES["album_caption_manual_prompt"],
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    context.user_data["messages_to_delete"].append(prompt_msg.message_id)
    return ASKING_FOR_MANUAL_CAPTION


async def receive_manual_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_caption = update.message.text
    context.user_data["current_caption"] = "" if user_caption == "." else user_caption

    try:
        await update.message.delete()
    except BadRequest as e:
        logger.warning(f"Failed to delete user's manual caption message: {e}")

    await finalize_split_action(update, context)
    return ConversationHandler.END


# --- تنفيذ التفكيك فعليًا ---
async def finalize_split_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except BadRequest:
            pass

    await delete_messages_from_queue(context, chat_id)

    progress_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["processing_start"],
    )

    await execute_all_ready_albums(update, context)

    context.user_data["processing_started"] = False
    context.user_data["caption_prompt_shown"] = False
    context.user_data.pop("current_caption", None)

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
    except Exception as e:
        logger.warning(f"Failed to delete progress message: {e}")


async def execute_all_ready_albums(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    target_chat_id = context.user_data["album_destination_chat_id"]
    album_buffer = context.user_data.get("album_buffer", {})
    ready_album_ids = list(context.user_data.get("ready_album_ids", []))
    custom_caption = context.user_data.get("current_caption", "")

    if not ready_album_ids:
        await context.bot.send_message(chat_id=chat_id, text=MESSAGES["no_album_detected"])
        return

    total_items_sent = 0

    for album_id in ready_album_ids:
        items = album_buffer.get(album_id, [])

        if len(items) < 2:
            continue

        for item in items:
            try:
                caption_to_send = custom_caption

                if item["type"] == "photo":
                    await context.bot.send_photo(
                        chat_id=target_chat_id,
                        photo=item["file_id"],
                        caption=caption_to_send if caption_to_send else None
                    )
                elif item["type"] == "video":
                    await context.bot.send_video(
                        chat_id=target_chat_id,
                        video=item["file_id"],
                        caption=caption_to_send if caption_to_send else None
                    )

                total_items_sent += 1

            except TelegramError as e:
                logger.error(f"Failed to send split item from album {album_id}: {e}")
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=f"⚠️ حدث خطأ أثناء إرسال عنصر من الألبوم {album_id}: {e}"
                )

            await asyncio.sleep(0.4)

        # تنظيف هذا الألبوم بعد الانتهاء منه
        album_buffer.pop(album_id, None)

        processing_task = context.user_data.get("processing_tasks", {}).pop(album_id, None)
        if processing_task and not processing_task.done():
            processing_task.cancel()

        logger.info(f"Successfully split album {album_id} into {len(items)} separate messages.")

    # حذف كل الألبومات الجاهزة التي تم معالجتها
    context.user_data["ready_album_ids"] = []

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{MESSAGES['done_success']}\n\n📦 عدد العناصر المرسلة: {total_items_sent}"
    )


# --- دوال مساعدة ---
async def delete_messages_from_queue(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    message_ids = context.user_data.get("messages_to_delete", [])
    ids_to_delete = list(message_ids)

    for msg_id in ids_to_delete:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except BadRequest as e:
            logger.debug(f"Could not delete message {msg_id}: {e}")
        except Exception as e:
            logger.warning(f"Failed to delete message {msg_id}: {e}")

    context.user_data["messages_to_delete"] = []


# --- الإلغاء وإعادة التعيين ---
async def reset_album(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await initialize_user_data(context, update.effective_chat.id)

    for task in context.user_data.get("processing_tasks", {}).values():
        if task and not task.done():
            task.cancel()

    context.user_data["album_buffer"] = {}
    context.user_data["processing_started"] = False
    context.user_data["processing_tasks"] = {}
    context.user_data["ready_album_ids"] = []
    context.user_data["caption_prompt_shown"] = False
    context.user_data.pop("current_caption", None)

    await update.message.reply_text(MESSAGES["queue_cleared"])
    await delete_messages_from_queue(context, update.effective_chat.id)


async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    chat_id = update.effective_chat.id

    if query:
        await query.answer()
        try:
            await query.message.delete()
        except BadRequest:
            pass

    for task in context.user_data.get("processing_tasks", {}).values():
        if task and not task.done():
            task.cancel()

    context.user_data["album_buffer"] = {}
    context.user_data["processing_started"] = False
    context.user_data["processing_tasks"] = {}
    context.user_data["ready_album_ids"] = []
    context.user_data["caption_prompt_shown"] = False
    context.user_data.pop("current_caption", None)

    await delete_messages_from_queue(context, chat_id)

    await context.bot.send_message(
        chat_id=chat_id,
        text=MESSAGES["cancel_operation"],
    )
    return ConversationHandler.END


# --- الأوامر الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        MESSAGES["greeting"].format(username=update.effective_user.username or "صديقي"),
        reply_markup=ReplyKeyboardRemove()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        MESSAGES["help"],
        reply_markup=ReplyKeyboardRemove()
    )


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set in environment variables.")
        raise ValueError("BOT_TOKEN environment variable not set.")

    application = Application.builder().token(token).build()

    manual_caption_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(prompt_for_manual_caption, pattern=f"^{MANUAL_CAPTION_CB_DATA}$")
        ],
        states={
            ASKING_FOR_MANUAL_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_manual_caption)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"),
            CommandHandler("cancel", cancel_operation)
        ],
        allow_reentry=True
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("keyboard", show_keyboard))
    application.add_handler(CommandHandler("hidekeyboard", hide_keyboard))

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_done'])}$"),
            start_album_split_process
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(f"^{re.escape(MESSAGES['keyboard_clear'])}$"),
            reset_album
        )
    )

    application.add_handler(manual_caption_conv)

    application.add_handler(CallbackQueryHandler(handle_predefined_caption_choice, pattern=r"^cap_\d+$"))
    application.add_handler(CallbackQueryHandler(handle_no_caption_choice, pattern=f"^{NO_CAPTION_CB_DATA}$"))
    application.add_handler(CallbackQueryHandler(cancel_operation, pattern=f"^{CANCEL_CB_DATA}$"))

    # استقبال الألبومات
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_album_media))
    application.add_handler(MessageHandler(filters.VIDEO & ~filters.COMMAND, handle_album_media))

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()

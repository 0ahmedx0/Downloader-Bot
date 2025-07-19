import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# إعداد التسجيل (logging) لعرض معلومات مفيدة
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- قراءة الإعدادات من متغيرات البيئة ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("PYRO_SESSION_STRING")
BOT_ID_STR = os.getenv("BOT_ID")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")

# --- التحقق من وجود الإعدادات ---
if not all([API_ID, API_HASH, SESSION_STRING, BOT_ID_STR, TARGET_CHANNEL_ID_STR]):
    logger.critical("❌ خطأ فادح: أحد المتغيرات المطلوبة (API_ID, API_HASH, PYRO_SESSION_STRING, BOT_ID, TARGET_CHANNEL_ID) غير موجود. أوقف التشغيل.")
    exit(1)

try:
    # تحويل المعرفات إلى أرقام صحيحة
    BOT_ID = int(BOT_ID_STR)
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)
except ValueError:
    logger.critical(f"❌ خطأ فادح: تأكد من أن BOT_ID ({BOT_ID_STR}) و TARGET_CHANNEL_ID ({TARGET_CHANNEL_ID_STR}) هي أرقام صحيحة.")
    exit(1)


# تهيئة عميل Pyrogram
app = Client(
    "user_album_forwarder",
    api_id=int(API_ID),
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# مجموعة لتخزين ID الألبومات التي تم التعامل معها
processed_media_groups = set()

# تعريف المستمع (Handler) للرسائل
@app.on_message(filters.media_group & filters.private & filters.user(BOT_ID))
async def forward_album_handler(client, message):
    media_group_id = message.media_group_id

    if media_group_id in processed_media_groups:
        return

    processed_media_groups.add(media_group_id)
    logger.info(f"🆕 تم اكتشاف ألبوم جديد (ID: {media_group_id}).")

    try:
        # ننتظر قليلاً لضمان وصول كل أجزاء الألبوم
        await asyncio.sleep(2)
        
        media_group_messages = await client.get_media_group(
            chat_id=message.chat.id,
            message_id=message.id
        )
        
        message_ids = [msg.id for msg in media_group_messages]

        await client.forward_messages(
            chat_id=TARGET_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message_ids
        )
        logger.info(f"✅ تم إعادة توجيه الألبوم (ID: {media_group_id}) بنجاح إلى القناة {TARGET_CHANNEL_ID}.")

    except FloodWait as e:
        logger.warning(f"⚠️ خطأ ضغط من تيليجرام. سيتم الانتظار لمدة {e.value} ثانية...")
        await asyncio.sleep(e.value)
        # إعادة المحاولة
        await client.forward_messages(
            chat_id=TARGET_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message_ids
        )
        logger.info(f"✅ تمت إعادة المحاولة بنجاح بعد انتظار FloodWait.")
    except Exception as e:
        logger.error(f"❌ حدث خطأ غير متوقع أثناء معالجة الألبوم {media_group_id}: {e}", exc_info=True)
    finally:
        await asyncio.sleep(60)
        processed_media_groups.discard(media_group_id)

# الدالة الرئيسية لتشغيل البوت
async def main():
    logger.info("جاري تشغيل الـ Userbot...")
    await app.start()
    me = await app.get_me()
    logger.info("=============================================")
    logger.info(f"👤 تم تسجيل الدخول بنجاح إلى حساب: {me.first_name} (@{me.username})")
    logger.info(f"👂 الحساب الآن يستمع للرسائل من البوت: {BOT_ID}")
    logger.info(f"🎯 سيتم إعادة توجيه أي ألبوم إلى القناة: {TARGET_CHANNEL_ID}")
    logger.info("🚀 السكربت يعمل الآن... لا تغلق هذه النافذة.")
    logger.info("=============================================")
    
    await asyncio.Event().wait()

# تشغيل السكربت
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n🛑 تم إيقاف السكربت يدويًا.")

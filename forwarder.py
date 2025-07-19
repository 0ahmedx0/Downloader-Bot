import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# إعداد التسجيل (logging)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- قراءة الإعدادات ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("PYRO_SESSION_STRING")
BOT_ID_STR = os.getenv("BOT_ID")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")

if not all([API_ID, API_HASH, SESSION_STRING, BOT_ID_STR, TARGET_CHANNEL_ID_STR]):
    logger.critical("❌ خطأ فادح: أحد المتغيرات المطلوبة غير موجود.")
    exit(1)

try:
    BOT_ID = int(BOT_ID_STR)
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)
except ValueError:
    logger.critical("❌ خطأ فادح: BOT_ID أو TARGET_CHANNEL_ID ليست أرقامًا صحيحة.")
    exit(1)

app = Client(
    "user_album_forwarder",
    api_id=int(API_ID),
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

processed_media_groups = set()

# ==================== كود التشخيص - لا تحذفه ====================
# هذا المستمع سيتم تفعيله لأي رسالة تأتي من البوت المحدد في محادثة خاصة
@app.on_message(filters.private & filters.user(BOT_ID))
async def diagnose_message(client, message):
    logger.info("🕵️‍♂️ تم استلام رسالة من البوت! جاري تحليلها...")
    # طباعة الكائن `message` بالكامل للحصول على كل التفاصيل
    print("--- محتوى الرسالة الكامل (للتشخيص) ---")
    print(message)
    print("---------------------------------------")
    
    is_media_group = "نعم" if message.media_group_id else "لا"
    logger.info(f"    - هل هي جزء من ألبوم (media_group)? -> {is_media_group}")
    if message.media_group_id:
        logger.info(f"    - Media Group ID: {message.media_group_id}")

# ==================== نهاية كود التشخيص ====================


# المستمع الأساسي لإعادة التوجيه (يبقى كما هو)
@app.on_message(filters.media_group & filters.private & filters.user(BOT_ID))
async def forward_album_handler(client, message):
    media_group_id = message.media_group_id
    if media_group_id in processed_media_groups:
        return
    processed_media_groups.add(media_group_id)
    logger.info(f"🆕 تم اكتشاف ألبوم (ID: {media_group_id}). جاري إعادة التوجيه...")
    try:
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
    except Exception as e:
        logger.error(f"❌ حدث خطأ أثناء معالجة الألبوم {media_group_id}: {e}", exc_info=True)
    finally:
        await asyncio.sleep(60)
        processed_media_groups.discard(media_group_id)


async def main():
    await app.start()
    me = await app.get_me()
    logger.info("=============================================")
    logger.info(f"👤 تم تسجيل الدخول بنجاح إلى حساب: {me.first_name} (@{me.username})")
    logger.info(f"👂 يستمع الآن للرسائل من البوت: {BOT_ID}")
    logger.info("🕵️‍♂️ [وضع التشخيص مفعل] سيتم طباعة تفاصيل أي رسالة من البوت.")
    logger.info("🚀 السكربت يعمل...")
    logger.info("=============================================")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n🛑 تم إيقاف السكربت.")

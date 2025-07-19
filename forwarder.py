import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# إعداد التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- قراءة الإعدادات من متغيرات البيئة ---
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("PYRO_SESSION_STRING")
SOURCE_BOT_ID_STR = os.getenv("BOT_ID")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")

# --- التحقق من وجود الإعدادات ---
if not all([API_ID, API_HASH, SESSION_STRING, SOURCE_BOT_ID_STR, TARGET_CHANNEL_ID_STR]):
    logger.critical("❌ خطأ: متغيرات البيئة غير مكتملة.")
    exit(1)

# تحويل المعرفات إلى أرقام صحيحة
SOURCE_BOT_ID = int(SOURCE_BOT_ID_STR)
TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR)

# تهيئة عميل Pyrogram
app = Client(
    "personal_userbot_forwarder",
    api_id=int(API_ID),
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# مجموعة لتخزين ID الألبومات التي تم التعامل معها
processed_media_groups = set()

# --- المستمع الرئيسي ---
# يستمع لجميع الرسائل في الخاص التي تحتوي على وسائط
@app.on_message(filters.private & filters.media)
async def private_media_handler(client, message):
    
    # التحقق من هوية المرسل يدويًا لضمان الموثوقية
    if not message.from_user or message.from_user.id != SOURCE_BOT_ID:
        return # إذا لم يكن المرسل هو البوت المستهدف، تجاهل الرسالة

    # إذا كانت الرسالة ليست جزءًا من ألبوم، تجاهلها
    if not message.media_group_id:
        logger.info(f"تم استلام رسالة فردية (ليست ألبومًا) من البوت. سيتم تجاهلها.")
        return

    # الآن نحن متأكدون أنها رسالة من البوت وهي جزء من ألبوم
    media_group_id = message.media_group_id
    if media_group_id in processed_media_groups:
        return

    processed_media_groups.add(media_group_id)
    logger.info(f"🆕 تم اكتشاف ألبوم من البوت المستهدف (ID: {media_group_id}).")

    try:
        # الانتظار قليلاً لضمان وصول جميع أجزاء الألبوم
        await asyncio.sleep(2.5) 
        
        # استخدام get_media_group للحصول على الألبوم كاملاً
        media_group_messages = await client.get_media_group(
            chat_id=message.chat.id, 
            message_id=message.id
        )
        
        message_ids = [msg.id for msg in media_group_messages]

        # إعادة توجيه الألبوم باستخدام حسابك الشخصي
        await client.forward_messages(
            chat_id=TARGET_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message_ids
        )
        logger.info(f"✅ تم إعادة توجيه الألبوم بنجاح بواسطة حسابك إلى القناة {TARGET_CHANNEL_ID}.")

    except FloodWait as e:
        logger.warning(f"⚠️ خطأ ضغط من تيليجرام. سيتم الانتظار لمدة {e.value} ثانية...")
        await asyncio.sleep(e.value)
        await client.forward_messages(
            chat_id=TARGET_CHANNEL_ID, from_chat_id=message.chat.id, message_ids=message_ids
        )
    except Exception as e:
        logger.error(f"❌ حدث خطأ أثناء إعادة توجيه الألبوم {media_group_id}: {e}", exc_info=True)
    finally:
        await asyncio.sleep(60)
        processed_media_groups.discard(media_group_id)


# الدالة الرئيسية للتشغيل
async def main():
    await app.start()
    me = await app.get_me()
    logger.info("======================================================")
    logger.info(f"👤 حساب المستخدم: {me.first_name} (@{me.username}) يعمل الآن.")
    logger.info(f"👂 يراقب الرسائل الواردة من البوت: {SOURCE_BOT_ID}")
    logger.info(f"🎯 سيقوم بإعادة توجيه أي ألبوم إلى القناة: {TARGET_CHANNEL_ID}")
    logger.info("🚀 الـ Userbot يعمل...")
    logger.info("======================================================")
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n🛑 تم إيقاف الـ Userbot.")

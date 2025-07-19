import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InputMediaPhoto, InputMediaVideo  # ✅ تم الاستيراد الآن

# ----- إعدادات من البيئة -----
API_ID = os.getenv("API_ID")  # ← من my.telegram.org
API_HASH = os.getenv("API_HASH")  # ← من my.telegram.org
SESSION_STRING = os.getenv("PYRO_SESSION_STRING")  # ← Session String من حسابك
BOT_ID = int(os.getenv("BOT_ID"))  # ← معرف البوت الذي يرسل الألبومات
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")  # ← معرف القناة التي تريد إعادة التوجيه إليها

# ----- التأكد من توفر البيانات -----
if not all([API_ID, API_HASH, SESSION_STRING, BOT_ID, TARGET_CHANNEL_ID]):
    raise ValueError("❌ بعض المتغيرات المطلوبة غير مُعدة في المحيط (os.environ)")

# ----- إنشاء العميل -----
app = Client("album_forwarder", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# ----- تخزين الألبومات التي تمت معالجتها -----
seen_media_groups = set()

@app.on_message(filters.media & filters.user(BOT_ID))
async def handle_album(client: Client, message: Message):
    global seen_media_groups

    # إذا كانت الرسالة جزء من ألبوم
    if message.media_group_id:
        if message.media_group_id in seen_media_groups:
            return  # تجنب إعادة توجيه الألبوم مرتين
        seen_media_groups.add(message.media_group_id)

        # جلب الألبوم كاملاً
        try:
            media_group = await app.get_media_group(BOT_ID, message.id)

            # جمع الوسائط
            input_media = []
            for msg in media_group:
                if msg.photo:
                    input_media.append(InputMediaPhoto(msg.photo.file_id))
                elif msg.video:
                    input_media.append(InputMediaVideo(msg.video.file_id))

            # ⏳ تأخير 3 ثوانٍ قبل الإرسال
            print(f"⏳ انتظر 3 ثوانٍ قبل إرسال الألبوم: {message.media_group_id}")
            await asyncio.sleep(3)

            # إرسال الألبوم كرسالة جديدة (بدون إعادة توجيه)
            if input_media:
                await app.send_media_group(TARGET_CHANNEL_ID, input_media)
                print(f"✅ تم إرسال الألبوم كاملاً بدون إظهار المرسل: {message.media_group_id}")

        except Exception as e:
            print(f"❌ فشل في إرسال الألبوم: {e}")
    else:
        # إذا لم يكن ألبومًا، أرسل كرسالة جديدة
        try:
            if message.photo:
                await app.send_photo(TARGET_CHANNEL_ID, message.photo.file_id)
            elif message.video:
                await app.send_video(TARGET_CHANNEL_ID, message.video.file_id)
            print(f"✅ تم إرسال الرسالة الفردية بدون إظهار المرسل: {message.id}")
        except Exception as e:
            print(f"❌ فشل في إرسال الرسالة: {e}")

# ----- تشغيل العميل -----
print("📡 البدء: جاري مراقبة الألبومات القادمة من البوت...")
app.run()

import os
from pyrogram import Client, filters
from pyrogram.types import Message

# ----- إعدادات من البيئة -----
API_ID = os.getenv("API_ID")  # ← من my.telegram.org
API_HASH = os.getenv("API_HASH")  # ← من my.telegram.org
SESSION_STRING = os.getenv("PYRO_SESSION_STRING")  # ← Session String من حسابك
BOT_ID = os.getenv("BOT_ID")  # ← معرف البوت الذي يرسل الألبومات
TARGET_CHANNEL_ID = os.getenv("TARGET_CHANNEL_ID")  # ← معرف القناة التي تريد إعادة التوجيه إليها

# ----- التأكد من توفر البيانات -----
if not all([API_ID, API_HASH, SESSION_STRING, BOT_ID, TARGET_CHANNEL_ID]):
    raise ValueError("❌ بعض المتغيرات المطلوبة غير مُعدة في المحيط (os.environ)")

# ----- إنشاء العميل -----
app = Client("album_forwarder", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# ----- تخزين الألبومات التي تمت معالجتها -----
seen_media_groups = set()

@app.on_message(filters.media & filters.user(int(BOT_ID)))
async def handle_album(client: Client, message: Message):
    global seen_media_groups

    # إذا كانت الرسالة جزء من ألبوم
    if message.media_group_id:
        if message.media_group_id in seen_media_groups:
            return  # تجنب إعادة توجيه الألبوم مرتين
        seen_media_groups.add(message.media_group_id)

    try:
        # إعادة توجيه الرسالة (أو الألبوم كاملاً) إلى القناة
        await message.forward(TARGET_CHANNEL_ID)
        print(f"✅ تم إعادة توجيه الألبوم: {message.media_group_id or message.id}")
    except Exception as e:
        print(f"❌ فشل في إعادة توجيه الرسالة: {e}")

# ----- تشغيل العميل -----
print("📡 البدء: جاري مراقبة الألبومات القادمة من البوت...")
app.run()

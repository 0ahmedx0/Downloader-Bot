import os
import asyncio
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# تحميل الإعدادات من ملف .env (يتطلب `pip install python-dotenv`)
# إذا لم ترغب بتثبيت المكتبة، يمكنك إزالة هذه الأسطر ووضع القيم مباشرة
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("تم تحميل الإعدادات من ملف .env")
except ImportError:
    print("تحذير: مكتبة python-dotenv غير مثبتة. سيتم الاعتماد على متغيرات البيئة فقط.")

# قراءة الإعدادات
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
BOT_ID_STR = os.getenv("BOT_ID")
TARGET_CHANNEL_ID_STR = os.getenv("TARGET_CHANNEL_ID")

# التحقق من وجود جميع الإعدادات المطلوبة
if not all([API_ID, API_HASH, SESSION_STRING, BOT_ID_STR, TARGET_CHANNEL_ID_STR]):
    print("خطأ: يرجى التأكد من تعيين جميع المتغيرات المطلوبة في ملف .env")
    exit(1)

# تحويل الـ ID إلى أرقام
try:
    BOT_ID = int(BOT_ID_STR)
    # القناة يمكن أن تكون رقمًا أو نصًا (مثل @username)
    TARGET_CHANNEL_ID = int(TARGET_CHANNEL_ID_STR) if TARGET_CHANNEL_ID_STR.lstrip('-').isdigit() else TARGET_CHANNEL_ID_STR
except ValueError:
    print("خطأ: تأكد من أن BOT_ID و TARGET_CHANNEL_ID (إذا كان رقميًا) هي أرقام صحيحة.")
    exit(1)

# تهيئة عميل Pyrogram باستخدام جلسة المستخدم
app = Client(
    "userbot_forwarder",
    api_id=int(API_ID),
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# مجموعة لتخزين ID الألبومات التي تم التعامل معها لمنع التكرار
processed_media_groups = set()

# تعريف المستمع (Handler) للرسائل
# سيتم تفعيله فقط للرسائل التي هي:
# 1. من بوت الألبومات المحدد (filters.user(BOT_ID))
# 2. في محادثة خاصة (filters.private)
# 3. جزء من ألبوم/مجموعة وسائط (filters.media_group)
@app.on_message(filters.media_group & filters.private & filters.user(BOT_ID))
async def forward_album(client, message):
    """هذه الدالة تقوم بإعادة توجيه الألبوم بالكامل عند استلامه."""
    
    # كل رسالة في الألبوم لها نفس media_group_id
    media_group_id = message.media_group_id

    # تحقق مما إذا كنا قد قمنا بمعالجة هذا الألبوم من قبل
    if media_group_id in processed_media_groups:
        # إذا نعم، تجاهل الرسالة الحالية لأن الألبوم بأكمله قيد المعالجة أو تمت معالجته
        return
    
    # أضف ID الألبوم إلى المجموعة لمنع إعادة معالجته
    processed_media_groups.add(media_group_id)
    
    print(f"تم اكتشاف ألبوم جديد (ID: {media_group_id}). جاري إعادة التوجيه إلى {TARGET_CHANNEL_ID}...")

    try:
        # الحصول على جميع الرسائل في الألبوم باستخدام `get_media_group`
        # هذه الطريقة مضمونة للحصول على الألبوم كاملاً
        media_group_messages = await client.get_media_group(
            chat_id=message.chat.id, 
            message_id=message.id
        )
        
        # استخراج ID الرسائل من قائمة الرسائل
        message_ids = [msg.id for msg in media_group_messages]

        # إعادة توجيه الألبوم بالكامل دفعة واحدة
        await client.forward_messages(
            chat_id=TARGET_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message_ids
        )
        print(f"✅ تم إعادة توجيه الألبوم (ID: {media_group_id}) بنجاح.")

    except FloodWait as e:
        # في حالة حدوث ضغط على خوادم تيليجرام، انتظر المدة المطلوبة ثم حاول مرة أخرى
        print(f"⚠️ واجهنا خطأ FloodWait. سننتظر لمدة {e.value} ثانية...")
        await asyncio.sleep(e.value)
        # يمكنك هنا إعادة محاولة التوجيه إذا أردت
        await client.forward_messages(
            chat_id=TARGET_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_ids=message_ids
        )

    except Exception as e:
        print(f"❌ حدث خطأ غير متوقع أثناء إعادة توجيه الألبوم: {e}")
    
    # بعد فترة قصيرة، قم بإزالة ID الألبوم من المجموعة للسماح بمعالجة ألبوم جديد بنفس الـ ID (نادر جدًا)
    await asyncio.sleep(30)
    if media_group_id in processed_media_groups:
        processed_media_groups.remove(media_group_id)


# الدالة الرئيسية لتشغيل البوت
async def main():
    """الدالة الرئيسية لتشغيل الـ Userbot."""
    async with app:
        me = await app.get_me()
        print(f"تم تسجيل الدخول بنجاح باسم: {me.first_name} (@{me.username})")
        print(f"البوت الآن يستمع للرسائل من البوت صاحب الـ ID: {BOT_ID}")
        print(f"سيتم إعادة توجيه أي ألبوم إلى القناة: {TARGET_CHANNEL_ID}")
        print("اضغط على CTRL+C لإيقاف السكربت.")
        
        # هذه السطر يبقي السكربت يعمل إلى الأبد
        await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nتم إيقاف السكربت.")

import os
import asyncio
from pyrogram import Client, filters
from collections import defaultdict

# إعدادات البيئة
os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-user'
bot_token = os.environ.get("TOKEN", "")
api_hash = os.environ.get("HASH", "") 
api_id = os.environ.get("ID", "")

app = Client("video_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# هياكل البيانات
user_queues = defaultdict(asyncio.Queue)  # طوابير المستخدمين
processing = defaultdict(bool)  # حالة المعالجة

async def process_video(user_id, message):
    try:
        # تنزيل الفيديو
        file = await app.download_media(message)
        
        # إنشاء الثمبنيل (استبدل هذا بالدالة الحقيقية)
        thumb = "thumbnail.jpg"
        
        # استخراج الميتاداتا (استبدل هذا بالدالة الحقيقية)
        metadata = {
            'duration': 60,
            'width': 1280,
            'height': 720
        }
        
        # إرسال الفيديو
        await app.send_video(
            chat_id=user_id,
            video=file,
            duration=metadata['duration'],
            width=metadata['width'],
            height=metadata['height'],
            thumb=thumb,
            caption="تمت المعالجة ✅",
            reply_to_message_id=message.id
        )
        
    except Exception as e:
        await app.send_message(user_id, f"❌ خطأ: {str(e)}")
    finally:
        processing[user_id] = False
        if os.path.exists(file):
            os.remove(file)

async def queue_worker(user_id):
    while not user_queues[user_id].empty():
        message = await user_queues[user_id].get()
        await process_video(user_id, message)
        user_queues[user_id].task_done()

@app.on_message(filters.video | filters.document)
async def handle_video(client, message):
    user_id = message.from_user.id
    await user_queues[user_id].put(message)
    
    if not processing[user_id]:
        processing[user_id] = True
        await queue_worker(user_id)

@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply("""
مرحبًا! أرسل لي الفيديوهات وسأعالجها واحدة تلو الأخرى
الميزات المدعومة:
- معالجة الفيديو مع الثمبنيل
- الحفاظ على الميتاداتا
- نظام طابور ذكي
""")

if __name__ == "__main__":
    print("✅ البوت يعمل الآن...")
    app.run()

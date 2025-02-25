import os
import asyncio
import logging
import tempfile
import time
from collections import defaultdict
from pyrogram import Client, filters, enums
from moviepy.editor import VideoFileClip

# ---------- إعدادات البيئة ---------- #
runtime_dir = '/tmp/runtime-user'
if not os.path.exists(runtime_dir):
    os.makedirs(runtime_dir, mode=0o700)
os.environ['XDG_RUNTIME_DIR'] = runtime_dir

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ---------- تهيئة البوت ---------- #
app = Client(
    "advanced_video_bot",
    api_id=os.environ.get("ID"),
    api_hash=os.environ.get("HASH"),
    bot_token=os.environ.get("TOKEN"),
    parse_mode=enums.ParseMode.MARKDOWN
)

# ---------- هياكل البيانات ---------- #
class UserQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active = False
        self.retry_count = 3  # عدد محاولات إعادة المحاولة

user_queues = defaultdict(UserQueue)
TEMP_DIR = tempfile.TemporaryDirectory()  # مجلد مؤقت يتم تنظيفه تلقائيًا

# ---------- وظائف أساسية ---------- #
async def handle_errors(func, *args, **kwargs):
    """معالجة الأخطاء مع إعادة المحاولة"""
    for attempt in range(1, 4):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logging.error(f"محاولة {attempt} فشلت: {str(e)}")
            if attempt == 3:
                raise
            await asyncio.sleep(2 ** attempt)

async def generate_thumbnail(video_path):
    """إنشاء صورة مصغرة مع إدارة الأخطاء"""
    output_path = os.path.join(TEMP_DIR.name, f"thumb_{os.path.basename(video_path)}.jpg")
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-i', video_path, '-ss', '00:00:01',
        '-vframes', '1', '-vf', 'scale=320:-1',
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.wait()
    return output_path if os.path.exists(output_path) else None

# ---------- إدارة المهام ---------- #
async def process_video(user_id, message):
    """معالجة فيديو واحد بشكل كامل"""
    temp_file = None
    thumb = None

    try:
        # تنزيل الملف مع إعادة المحاولة
        temp_file = await handle_errors(
            app.download_media,
            message,
            file_name=os.path.join(TEMP_DIR.name, f"temp_{message.id}.mp4")
        )
        
        # محاولة إصلاح الفيديو بإعادة تغليفه باستخدام ffmpeg
        fixed_file = os.path.join(TEMP_DIR.name, f"fixed_{message.id}.mp4")
        cmd = [
            'ffmpeg', '-y', '-i', temp_file, '-c', 'copy',
            '-movflags', 'faststart', fixed_file
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(fixed_file):
            os.remove(temp_file)
            temp_file = fixed_file
        
        # استخراج بيانات الفيديو باستخدام MoviePy
        with VideoFileClip(temp_file, audio=False) as clip:
            metadata = {
                'duration': int(clip.duration),
                'width': clip.size[0],
                'height': clip.size[1]
            }
        
        # إنشاء الصورة المصغرة
        thumb = await handle_errors(generate_thumbnail, temp_file)
        
        # بدء مهمة تحديث التقدم
        progress_task = asyncio.create_task(update_progress(user_id))
        try:
            # رفع الفيديو مع البيانات المصاحبة
            await handle_errors(
                app.send_video,
                chat_id=user_id,
                video=temp_file,
                duration=metadata['duration'],
                width=metadata['width'],
                height=metadata['height'],
                thumb=thumb,
                caption=f"✅ {os.path.basename(temp_file)}",
                reply_to_message_id=message.id
            )
        finally:
            progress_task.cancel()
        
    finally:
        # التنظيف: حذف الملفات المؤقتة
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)

async def queue_manager():
    """مدير الطوابير الأساسي"""
    while True:
        for user_id, uq in list(user_queues.items()):
            if not uq.active and not uq.queue.empty():
                uq.active = True
                asyncio.create_task(process_queue(user_id))
        await asyncio.sleep(1)

async def process_queue(user_id):
    """معالجة طابور مستخدم واحد"""
    uq = user_queues[user_id]
    try:
        while not uq.queue.empty():
            message = await uq.queue.get()
            await process_video(user_id, message)
            uq.queue.task_done()
    except Exception as e:
        logging.error(f"فشل معالجة الطابور: {str(e)}")
        await app.send_message(user_id, f"⚠️ حدث خطأ جسيم: {str(e)}")
    finally:
        uq.active = False

async def update_progress(user_id):
    """تحديث التقدم كل 5 ثواني"""
    progress_msg = await app.send_message(user_id, "⏳ جاري التحضير...")
    last_update = 0
    try:
        while True:
            if time.time() - last_update > 5:
                await progress_msg.edit_text(
                    f"📊 الحالة:\n"
                    f"• المهام المتبقية: {user_queues[user_id].queue.qsize()}\n"
                    f"• المحاولات المتبقية: {user_queues[user_id].retry_count}"
                )
                last_update = time.time()
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass

# ---------- معالجة الأحداث ---------- #
@app.on_message(filters.video | filters.document)
async def on_video_receive(client, message):
    """إضافة الفيديو إلى الطابور"""
    user_id = message.from_user.id
    uq = user_queues[user_id]
    await uq.queue.put(message)
    
    await app.send_message(
        user_id,
        f"📥 تمت الإضافة إلى القائمة (الموقع: {uq.queue.qsize()})",
        reply_to_message_id=message.id
    )

@app.on_message(filters.command("start"))
async def start(client, message):
    """رسالة البدء"""
    text = (
        "مرحبًا في بوت معالجة الفيديو المتقدم! 🎥\n\n"
        "المميزات:\n"
        "• معالجة غير محدودة للفيديوهات\n"
        "• نظام طابور ذكي لكل مستخدم\n"
        "• تحديثات حالة كل 5 ثواني\n"
        "• إعادة محاولة تلقائية عند الأخطاء"
    )
    await message.reply(text)

# ---------- التشغيل ---------- #
if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(queue_manager())
        app.run()
    except KeyboardInterrupt:
        TEMP_DIR.cleanup()
        logging.info("تم إيقاف البوت بنجاح")

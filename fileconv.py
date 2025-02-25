import os
import asyncio
import logging
import tempfile
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

# ---------- تهيئة البوت باستخدام بوت توكن فقط ---------- #
app = Client(
    "advanced_video_bot",
    api_id=os.environ.get("ID"),
    api_hash=os.environ.get("HASH"),
    bot_token=os.environ.get("TOKEN"),
    parse_mode=enums.ParseMode.MARKDOWN
)

# ---------- هياكل البيانات ---------- #
class ChatQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active = False
        self.retry_count = 3  # عدد محاولات إعادة المحاولة

chat_queues = defaultdict(ChatQueue)
TEMP_DIR = tempfile.TemporaryDirectory()  # مجلد مؤقت يتم تنظيفه تلقائيًا

# قاموس لتخزين رسالة التأكيد الخاصة بكل رسالة واردة
confirmation_messages = {}

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

async def process_video(chat_id, message):
    """معالجة فيديو واحد بشكل كامل وحذف رسالة المستخدم ورسالة التأكيد بعد الرفع الناجح"""
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
        
        # تأخير 5 ثوانٍ قبل بدء عملية رفع الفيديو
        await asyncio.sleep(5)
        
        # رفع الفيديو المعالج
        await handle_errors(
            app.send_video,
            chat_id=chat_id,
            video=temp_file,
            duration=metadata['duration'],
            width=metadata['width'],
            height=metadata['height'],
            thumb=thumb,
            caption=f"✅ {os.path.basename(temp_file)}",
            reply_to_message_id=message.id
        )
        
        # حذف رسالة المستخدم بعد رفع الفيديو بنجاح
        try:
            await app.delete_messages(chat_id, message.message_id)
        except Exception as del_exc:
            logging.error(f"فشل حذف رسالة المستخدم: {del_exc}")
        
        # حذف رسالة التأكيد المرسلة من البوت
        confirmation_msg_id = confirmation_messages.pop(message.message_id, None)
        if confirmation_msg_id:
            try:
                await app.delete_messages(chat_id, confirmation_msg_id)
            except Exception as del_exc:
                logging.error(f"فشل حذف رسالة التأكيد: {del_exc}")
        
    finally:
        # التنظيف: حذف الملفات المؤقتة
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)

async def queue_manager():
    """مدير الطوابير الأساسي"""
    while True:
        for chat_id, cq in list(chat_queues.items()):
            if not cq.active and not cq.queue.empty():
                cq.active = True
                asyncio.create_task(process_queue(chat_id))
        await asyncio.sleep(1)

async def process_queue(chat_id):
    """معالجة طابور دردشة واحدة"""
    cq = chat_queues[chat_id]
    try:
        while not cq.queue.empty():
            message = await cq.queue.get()
            await process_video(chat_id, message)
            cq.queue.task_done()
            # تأخير 3 ثوانٍ قبل بدء تنزيل الملف التالي
            await asyncio.sleep(3)
    except Exception as e:
        logging.error(f"فشل معالجة الطابور: {str(e)}")
        await app.send_message(chat_id, f"⚠️ حدث خطأ جسيم: {str(e)}")
    finally:
        cq.active = False

# ---------- معالجة الأحداث ---------- #
@app.on_message(filters.video | filters.document)
async def on_video_receive(client, message):
    """إضافة الفيديو إلى الطابور وحفظ رسالة التأكيد"""
    chat_id = message.chat.id
    cq = chat_queues[chat_id]
    await cq.queue.put(message)
    
    confirm_msg = await app.send_message(
        chat_id,
        f"📥 تمت الإضافة إلى القائمة (الموقع: {cq.queue.qsize()})",
        reply_to_message_id=message.id
    )
    # تخزين معرف رسالة التأكيد باستخدام معرف رسالة المستخدم كمرجع
    confirmation_messages[message.message_id] = confirm_msg.message_id

@app.on_message(filters.command("start"))
async def start(client, message):
    """رسالة البدء"""
    text = (
        "مرحبًا في بوت معالجة الفيديو المتقدم! 🎥\n\n"
        "المميزات:\n"
        "• معالجة غير محدودة للفيديوهات\n"
        "• نظام طابور ذكي لكل دردشة\n"
        "• إعادة محاولة تلقائية عند الأخطاء\n"
        "• تأخير 5 ثوانٍ قبل رفع الفيديو\n"
        "• تأخير 3 ثوانٍ بين كل ملف (بعد رفع الملف الحالي)\n"
        "• حذف رسالة المستخدم ورسالة التأكيد بعد المعالجة والرفع الناجح"
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

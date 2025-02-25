import os
import asyncio
import logging
import tempfile
import subprocess
from collections import defaultdict
from pyrogram import Client, filters, enums
from moviepy.editor import VideoFileClip

# إصلاح مشاكل البيئة
os.environ.update({
    'XDG_RUNTIME_DIR': '/tmp/runtime-user',
    'ALSA_CONFIG_PATH': '/dev/null',
    'PYROGRAM_SILENT_FFMPEG': '1'
})

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

app = Client(
    "video_bot_fixed",
    api_id=os.environ.get("ID"),
    api_hash=os.environ.get("HASH"),
    bot_token=os.environ.get("TOKEN"),
    parse_mode=enums.ParseMode.MARKDOWN
)

# هياكل البيانات المحسنة
class UserQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active = False
        self.progress_task = None
        self.current_processing = None

user_queues = defaultdict(UserQueue)
TEMP_DIR = tempfile.TemporaryDirectory()

async def verify_video_file(file_path):
    """التحقق من سلامة الفيديو باستخدام ffprobe"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise Exception(f"FFprobe error: {stderr.decode().strip()}")
            
        return True
    except Exception as e:
        logging.error(f"File verification failed: {str(e)}")
        return False

async def safe_download(message):
    """تنزيل الملف مع التحقق من السلامة"""
    temp_file = os.path.join(TEMP_DIR.name, f"temp_{message.id}.mp4")
    
    # التنزيل
    await app.download_media(message, file_name=temp_file)
    
    # التحقق من الملف
    if not await verify_video_file(temp_file):
        raise ValueError("الملف التالف أو غير مكتمل")
    
    return temp_file

async def process_video(user_id, message):
    uq = user_queues[user_id]
    temp_file = None
    thumb = None
    
    try:
        # التنزيل الآمن
        temp_file = await safe_download(message)
        
        # استخراج الميتاداتا
        with VideoFileClip(temp_file, audio=False) as clip:
            metadata = {
                'duration': int(clip.duration),
                'width': clip.size[0],
                'height': clip.size[1]
            }
        
        # إنشاء الثمبنييل
        thumb = await generate_thumbnail(temp_file)
        
        # الرفع مع التحكم بالتقدم
        await upload_video(user_id, message, temp_file, metadata, thumb)
        
    except Exception as e:
        await handle_upload_error(user_id, message, str(e))
        
    finally:
        await cleanup_resources(temp_file, thumb)
        uq.current_processing = None

async def generate_thumbnail(video_path):
    """إنشاء صورة مصغرة مع التعامل مع الأخطاء"""
    try:
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
    except Exception as e:
        logging.error(f"Thumbnail error: {str(e)}")
        return None

async def upload_video(user_id, message, file_path, metadata, thumb):
    """الرفع مع التحكم في المعدل"""
    progress_msg = None
    try:
        progress_msg = await app.send_message(user_id, "⏳ جاري الرفع...")
        
        await app.send_video(
            chat_id=user_id,
            video=file_path,
            duration=metadata['duration'],
            width=metadata['width'],
            height=metadata['height'],
            thumb=thumb,
            caption=f"✅ {os.path.basename(file_path)}",
            reply_to_message_id=message.id,
            progress=create_progress_callback(progress_msg)
        )
        
    finally:
        if progress_msg:
            await progress_msg.delete()

def create_progress_callback(progress_msg):
    """إنشاء دالة تحديث التقدم"""
    last_update = 0
    
    async def callback(current, total):
        nonlocal last_update
        if time.time() - last_update > 5:
            percent = current * 100 / total
            try:
                await progress_msg.edit_text(f"📤 جاري الرفع: {percent:.1f}%")
                last_update = time.time()
            except:
                pass
    return callback

async def handle_upload_error(user_id, message, error):
    """معالجة أخطاء الرفع"""
    logging.error(f"Upload error: {error}")
    await app.send_message(
        user_id,
        f"❌ فشل في معالجة الملف:\n{error}",
        reply_to_message_id=message.id
    )

async def cleanup_resources(*files):
    """تنظيف الملفات المؤقتة"""
    for f in files:
        if f and os.path.exists(f):
            try:
                os.remove(f)
            except Exception as e:
                logging.error(f"Cleanup error: {str(e)}")

async def queue_supervisor():
    """مراقب الطوابير الرئيسي"""
    while True:
        for user_id, uq in list(user_queues.items()):
            if not uq.active and not uq.queue.empty():
                uq.active = True
                asyncio.create_task(process_user_queue(user_id))
        await asyncio.sleep(1)

async def process_user_queue(user_id):
    """معالجة طابور مستخدم واحد"""
    uq = user_queues[user_id]
    try:
        while not uq.queue.empty():
            message = await uq.queue.get()
            uq.current_processing = message
            await process_video(user_id, message)
            uq.queue.task_done()
    except Exception as e:
        logging.error(f"Queue error: {str(e)}")
    finally:
        uq.active = False

@app.on_message(filters.video | filters.document)
async def handle_video(client, message):
    """إدارة الفيديوهات الواردة"""
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
    """رسالة الترحيب"""
    text = (
        "مرحبًا في بوت معالجة الفيديو المتقدم! 🎥\n"
        "أرسل الفيديوهات وسيتم معالجتها واحدة تلو الأخرى\n"
        "ميزات البوت:\n"
        "- تحقق من سلامة الملفات\n"
        "- معالجة تسلسلية آمنة\n"
        "- إدارة أخطاء محسنة"
    )
    await message.reply(text)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.create_task(queue_supervisor())
        app.run()
    except KeyboardInterrupt:
        TEMP_DIR.cleanup()
    finally:
        loop.close()

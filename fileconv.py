import os
import asyncio
import logging
from collections import defaultdict
from pyrogram import Client, filters, enums
from moviepy.editor import VideoFileClip

# ---------- إعدادات البيئة ---------- #
os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-user'
logging.basicConfig(level=logging.INFO)

# ---------- تهيئة البوت ---------- #
app = Client(
    "video_bot",
    api_id=os.environ.get("ID"),
    api_hash=os.environ.get("HASH"),
    bot_token=os.environ.get("TOKEN"),
    parse_mode=enums.ParseMode.MARKDOWN
)

# ---------- هياكل البيانات ---------- #
user_queues = defaultdict(asyncio.Queue)  # طوابير المستخدمين
processing = defaultdict(bool)           # حالة المعالجة
user_tasks = defaultdict(list)           # المهام النشطة

# ---------- وظائف معالجة الفيديو ---------- #
async def extract_metadata(video_path):
    """استخراج بيانات الفيديو باستخدام moviepy"""
    try:
        with VideoFileClip(video_path, audio=False) as clip:
            return {
                'duration': int(clip.duration),
                'width': clip.size[0],
                'height': clip.size[1]
            }
    except Exception as e:
        logging.error(f"Metadata error: {str(e)}")
        raise

async def generate_thumb(video_path):
    """إنشاء ثومبنييل باستخدام ffmpeg"""
    try:
        output_path = f"{video_path}_thumb.jpg"
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg', '-y', '-loglevel', 'error',
            '-i', video_path, '-ss', '00:00:01',
            '-vframes', '1', '-vf', 'scale=320:-1',
            output_path
        )
        await proc.wait()
        return output_path if os.path.exists(output_path) else None
    except Exception as e:
        logging.error(f"Thumbnail error: {str(e)}")
        return None

# ---------- إدارة الطوابير ---------- #
async def process_queue(user_id):
    """معالجة طابور مستخدم واحد بشكل تسلسلي"""
    while not user_queues[user_id].empty():
        message = await user_queues[user_id].get()
        await process_single_video(message)
        user_queues[user_id].task_done()

async def process_single_video(message):
    """معالجة فيديو واحد من البداية للنهاية"""
    user_id = message.from_user.id
    temp_file = None
    thumb = None
    
    try:
        # التنزيل
        temp_file = await app.download_media(message, file_name=f"temp_{message.id}.mp4")
        
        # المعالجة
        metadata = await extract_metadata(temp_file)
        thumb = await generate_thumb(temp_file)
        
        # الرفع
        await upload_video(message, temp_file, metadata, thumb)
        
    except Exception as e:
        await app.send_message(user_id, f"❌ خطأ في المعالجة: {str(e)}")
        
    finally:
        # التنظيف
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)

async def upload_video(message, video_path, metadata, thumb):
    """رفع الفيديو مع التتبع التسلسلي"""
    user_id = message.from_user.id
    progress_msg = None
    
    try:
        progress_msg = await app.send_message(user_id, "⏳ جاري الرفع...")
        
        await app.send_video(
            chat_id=user_id,
            video=video_path,
            duration=metadata['duration'],
            width=metadata['width'],
            height=metadata['height'],
            thumb=thumb,
            caption=f"✅ {os.path.basename(video_path)}",
            reply_to_message_id=message.id,
            progress=lambda c, t: update_progress(c, t, progress_msg)
        )
        
    finally:
        if progress_msg:
            await progress_msg.delete()

async def update_progress(current, total, progress_msg):
    """تحديث حالة التقدم"""
    try:
        percent = current * 100 / total
        if int(percent) % 5 == 0:  # تحديث كل 5%
            await progress_msg.edit_text(f"📤 جاري الرفع: {percent:.1f}%")
    except:
        pass

# ---------- معالجة الأحداث ---------- #
@app.on_message(filters.video | filters.document)
async def handle_video(client, message):
    """إضافة الفيديو إلى طابور المستخدم"""
    user_id = message.from_user.id
    await user_queues[user_id].put(message)
    
    if not processing[user_id]:
        processing[user_id] = True
        await process_queue(user_id)
        processing[user_id] = False

@app.on_message(filters.command("start"))
async def start(client, message):
    """رسالة الترحيب"""
    text = (
        "مرحبًا! 🎥\n"
        "أرسل لي الفيديوهات وسأقوم بمعالجتها بشكل تسلسلي\n"
        "الميزات:\n"
        "- معالجة واحدة تلو الأخرى\n"
        "- حفظ جودة الفيديو الأصلية\n"
        "- دعم الصور المصغرة التلقائية"
    )
    await message.reply(text)

# ---------- التشغيل ---------- #
if __name__ == "__main__":
    print("✅ البوت يعمل الآن...")
    app.run()

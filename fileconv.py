import os
import threading
import time
import queue
import subprocess
from collections import defaultdict
from pyrogram import Client, filters
from moviepy.editor import VideoFileClip

# ---------- Configuration ---------- #
bot_token = os.environ.get("TOKEN", "")
api_hash = os.environ.get("HASH", "") 
api_id = os.environ.get("ID", "")

# ---------- Global Variables ---------- #
app = Client("video_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
task_queue = queue.Queue()
MAX_WORKERS = 3

# ---------- Video Processing Functions ---------- #
def extract_video_metadata(file_path):
    """استخراج معلومات الفيديو باستخدام moviepy"""
    with VideoFileClip(file_path) as clip:
        return {
            'duration': int(clip.duration),
            'width': clip.size[0],
            'height': clip.size[1],
            'fps': clip.fps
        }

def generate_thumbnail(video_path, output_path="thumbnail.jpg"):
    """إنشاء صورة مصغرة باستخدام ffmpeg"""
    cmd = [
        'ffmpeg',
        '-i', video_path,
        '-ss', '00:00:01',
        '-vframes', '1',
        '-vf', 'scale=320:-1',
        output_path
    ]
    subprocess.run(cmd, stderr=subprocess.DEVNULL)
    return output_path if os.path.exists(output_path) else None

# ---------- Worker Class ---------- #
class VideoProcessor:
    def __init__(self):
        self.active_workers = 0
        self.lock = threading.Lock()

    def process_task(self, message):
        try:
            # تنزيل الفيديو
            temp_file = self.download_video(message)
            
            # استخراج المعلومات
            metadata = extract_video_metadata(temp_file)
            thumb_path = generate_thumbnail(temp_file)
            
            # رفع الفيديو مع الخصائص
            self.upload_video(
                message=message,
                video_path=temp_file,
                duration=metadata['duration'],
                width=metadata['width'],
                height=metadata['height'],
                thumbnail=thumb_path
            )
            
        except Exception as e:
            error_msg = f"❌ فشل في المعالجة: {str(e)}"
            app.send_message(message.chat.id, error_msg)
        finally:
            # تنظيف الملفات المؤقتة
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)

    def download_video(self, message):
        temp_file = f"temp_{message.id}.mp4"
        return app.download_media(message, file_name=temp_file)

    def upload_video(self, message, video_path, duration, width, height, thumbnail):
        # إرسال رسالة تقدم
        progress_msg = app.send_message(message.chat.id, "⏳ جاري الرفع...")
        
        # رفع الفيديو مع جميع الخصائص
        app.send_video(
            chat_id=message.chat.id,
            video=video_path,
            duration=duration,
            width=width,
            height=height,
            thumb=thumbnail,
            caption=f"🎥 {os.path.basename(video_path)}",
            reply_to_message_id=message.id,
            progress=self.upload_progress,
            progress_args=(progress_msg,)
        )
        
        # حذف رسالة التقدم
        app.delete_messages(message.chat.id, progress_msg.id)

    def upload_progress(self, current, total, progress_msg):
        percent = current * 100 / total
        try:
            app.edit_message_text(
                chat_id=progress_msg.chat.id,
                message_id=progress_msg.id,
                text=f"📤 جاري الرفع: {percent:.1f}%"
            )
        except:
            pass

# ---------- Bot Handlers ---------- #
video_processor = VideoProcessor()

@app.on_message(filters.video | filters.document)
def handle_video(client, message):
    task_queue.put(message)
    process_queue()

def process_queue():
    if not task_queue.empty() and video_processor.active_workers < MAX_WORKERS:
        with video_processor.lock:
            video_processor.active_workers += 1
        
        message = task_queue.get()
        worker = threading.Thread(
            target=video_processor.process_task,
            args=(message,),
            daemon=True
        )
        worker.start()

@app.on_message(filters.command("start"))
def start(client, message):
    help_text = (
        "مرحبًا! 👋\n"
        "أرسل لي أي فيديو وسأقوم برفعه مع:\n"
        "- الصورة المصغرة التلقائية\n"
        "- مدة الفيديو\n"
        "- دقة الفيديو الأصلية\n"
        "- التنسيق الأمثل للبث"
    )
    app.send_message(message.chat.id, help_text)

# ---------- Run Bot ---------- #
if __name__ == "__main__":
    print("✅ البوت يعمل الآن...")
    app.run()

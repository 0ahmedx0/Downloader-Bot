import os
import threading
import time
import queue
import subprocess
import logging
from pyrogram import Client, filters, enums
from moviepy.editor import VideoFileClip

# ---------- إعدادات البيئة ---------- #
os.environ['XDG_RUNTIME_DIR'] = '/tmp/runtime-user'
os.environ['ALSA_CONFIG_PATH'] = '/dev/null'

# ---------- التهيئة ---------- #
logging.basicConfig(level=logging.INFO)
bot_token = os.environ.get("TOKEN", "")
api_hash = os.environ.get("HASH", "") 
api_id = os.environ.get("ID", "")

# ---------- المتغيرات العامة ---------- #
app = Client(
    "video_bot_pro",
    api_id=api_id,
    api_hash=api_hash,
    bot_token=bot_token,
    workers=3,
    sleep_threshold=30,
    parse_mode=enums.ParseMode.MARKDOWN
)

task_queue = queue.Queue()
MAX_WORKERS = 10
QUEUE_CHECK_INTERVAL = 1

# ---------- وظائف معالجة الفيديو ---------- #
def extract_metadata(video_path):
    """استخراج بيانات الفيديو بدون صوت"""
    try:
        with VideoFileClip(video_path, audio=False) as clip:
            return {
                'duration': int(clip.duration),
                'width': clip.size[0],
                'height': clip.size[1]
            }
    except Exception as e:
        logging.error(f"Metadata extraction failed: {str(e)}")
        raise

def generate_thumb(video_path):
    """إنشاء ثومبنييل مع تعطيل الإخراج"""
    try:
        output_path = f"{video_path}_thumb.jpg"
        subprocess.run([
            'ffmpeg', '-y', '-loglevel', 'error',
            '-i', video_path, '-ss', '00:00:01',
            '-vframes', '1', '-vf', 'scale=320:-1',
            output_path
        ], check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        logging.error(f"Thumbnail generation failed: {str(e)}")
        return None

# ---------- إدارة المهام ---------- #
class TaskManager:
    def __init__(self):
        self.active_tasks = 0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    def start_processing(self):
        """بدء معالجة المهام في الخلفية"""
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()

    def _process_queue(self):
        """معالجة مستمرة للقائمة"""
        while not self.stop_event.is_set():
            try:
                if self.active_tasks < MAX_WORKERS and not task_queue.empty():
                    with self.lock:
                        self.active_tasks += 1
                    
                    message = task_queue.get()
                    threading.Thread(
                        target=self.handle_video,
                        args=(message,),
                        daemon=True
                    ).start()
                
                time.sleep(QUEUE_CHECK_INTERVAL)
            except Exception as e:
                logging.error(f"Queue processing error: {str(e)}")

    def handle_video(self, message):
        """معالجة الفيديو الفردي"""
        temp_file = None
        try:
            # التنزيل
            temp_file = app.download_media(message, file_name=f"temp_{message.id}.mp4")
            if not temp_file:
                raise ValueError("Failed to download video")

            # استخراج البيانات
            metadata = extract_metadata(temp_file)
            thumb = generate_thumb(temp_file)
            
            # الرفع
            self.upload_video(
                message=message,
                video_path=temp_file,
                duration=metadata['duration'],
                width=metadata['width'],
                height=metadata['height'],
                thumbnail=thumb
            )

        except Exception as e:
            logging.error(f"Video processing failed: {str(e)}")
            app.send_message(message.chat.id, f"❌ فشلت المعالجة: {str(e)}")
        
        finally:
            # التنظيف
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)
            if thumb and os.path.exists(thumb):
                os.remove(thumb)
            
            with self.lock:
                self.active_tasks -= 1
            task_queue.task_done()

    def upload_video(self, message, video_path, duration, width, height, thumbnail):
        """رفع الفيديو مع التحكم في المعدل"""
        progress_msg = None
        last_update = 0
        update_interval = 5  # ثواني بين التحديثات
        
        try:
            # بدء الرفع
            progress_msg = app.send_message(message.chat.id, "⏳ جاري البدء في الرفع...")
            
            # التقدم التراكمي
            def progress(current, total):
                nonlocal last_update
                if time.time() - last_update > update_interval:
                    percent = current * 100 / total
                    try:
                        app.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=progress_msg.id,
                            text=f"📤 جاري الرفع: {percent:.1f}%"
                        )
                        last_update = time.time()
                    except:
                        pass

            # الإرسال النهائي
            app.send_video(
                chat_id=message.chat.id,
                video=video_path,
                duration=duration,
                width=width,
                height=height,
                thumb=thumbnail,
                caption=f"✅ {os.path.basename(video_path)}",
                reply_to_message_id=message.id,
                progress=progress
            )
            
        finally:
            if progress_msg:
                try:
                    app.delete_messages(message.chat.id, progress_msg.id)
                except:
                    pass

# ---------- معالجة الأحداث ---------- #
task_manager = TaskManager()

@app.on_message(filters.video | filters.document)
def on_video_receive(client, message):
    """استقبال الفيديوهات الجديدة"""
    try:
        task_queue.put(message)
        app.send_message(
            message.chat.id,
            f"📥 تمت الإضافة إلى القائمة (الموقع: {task_queue.qsize()})",
            reply_to_message_id=message.id
        )
    except Exception as e:
        logging.error(f"Error adding to queue: {str(e)}")

@app.on_message(filters.command("status"))
def show_status(client, message):
    """عرض حالة النظام"""
    status = (
        f"🔧 الحالة التشغيلية:\n"
        f"العمال النشطون: {task_manager.active_tasks}/{MAX_WORKERS}\n"
        f"المهام في الانتظار: {task_queue.qsize()}\n"
        f"الإصدار: 2.1"
    )
    app.send_message(message.chat.id, status)

@app.on_message(filters.command("start"))
def start(client, message):
    """رسالة البداية"""
    welcome = (
        "مرحبا بكم في بوت معالجة الفيديوهات المتقدم! 🎬\n\n"
        "مميزات البوت:\n"
        "- معالجة متزامنة لعدة فيديوهات\n"
        "- دعم الصور المصغرة التلقائية\n"
        "- نظام إدارة مهام ذكي\n"
        "- تحديثات حالة مباشرة\n\n"
        "أرسل أي فيديو للبدء!"
    )
    app.send_message(message.chat.id, welcome)

# ---------- التشغيل ---------- #
if __name__ == "__main__":
    try:
        task_manager.start_processing()
        logging.info("✅ البوت يعمل الآن بشكل متقدم...")
        app.run()
    except KeyboardInterrupt:
        task_manager.stop_event.set()
        logging.info("⛔ إيقاف البوت...")

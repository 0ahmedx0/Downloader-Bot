import os
import threading
import queue
import time
from pyrogram import Client, filters

# env
bot_token = os.environ.get("TOKEN", "")
api_hash = os.environ.get("HASH", "")
api_id = os.environ.get("ID", "")

# bot
app = Client("my_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# Queue for processing messages
message_queue = queue.Queue()

# Maximum number of threads
MAX_THREADS = 3

# Dictionary to store saved messages
MESGS = {}

# Functions to manage saved messages
def saveMsg(msg, msg_type):
    MESGS[msg.from_user.id] = [msg, msg_type]

def getSavedMsg(msg):
    return MESGS.get(msg.from_user.id, [None, None])

def removeSavedMsg(msg):
    if msg.from_user.id in MESGS:
        del MESGS[msg.from_user.id]

# Function to process messages from the queue
def process_messages():
    while True:
        try:
            # Get a message from the queue
            message = message_queue.get()
            if message is None:
                break

            # Check if the message is a video
            if message.video:
                handle_video(message)
            else:
                app.send_message(message.chat.id, "This is not a video.", reply_to_message_id=message.id)

            # Mark the task as done
            message_queue.task_done()
        except Exception as e:
            print(f"Error processing message: {e}")

# Function to handle video messages
def handle_video(message):
    # Save the message as a video
    saveMsg(message, "VIDEO")

    # Send a temporary message indicating that the video is being processed
    oldm = app.send_message(message.chat.id, '__Sending in Stream Format__', reply_to_message_id=message.id)

    # Start a thread to send the video
    sv = threading.Thread(target=lambda: sendvideo(message, oldm), daemon=True)
    sv.start()

# Rest of the code remains unchanged...
# Function to send video
def sendvideo(message, oldmessage):
    file, msg = down(message)
    thumb, duration, width, height = mediainfo.allinfo(file)
    up(message, file, msg, video=True, capt=f'**{file.split("/")[-1]}**', thumb=thumb, duration=duration, height=height, widht=width)
    app.delete_messages(message.chat.id, message_ids=oldmessage.id)
    os.remove(file)

# Download function
def down(message):
    try:
        size = int(message.document.file_size)
    except:
        try:
            size = int(message.video.file_size)
        except:
            size = 1
    if size > 25000000:
        msg = app.send_message(message.chat.id, '__Downloading__', reply_to_message_id=message.id)
        dosta = threading.Thread(target=lambda: downstatus(f'{message.id}downstatus.txt', msg), daemon=True)
        dosta.start()
    else:
        msg = None
    file = app.download_media(message, progress=dprogress, progress_args=[message])
    os.remove(f'{message.id}downstatus.txt')
    return file, msg

# Upload function
def up(message, file, msg, video=False, capt="", thumb=None, duration=0, widht=0, height=0, multi=False):
    if msg is not None:
        try:
            app.edit_message_text(message.chat.id, msg.id, '__Uploading__')
        except:
            pass

    if os.path.getsize(file) > 25000000:
        upsta = threading.Thread(target=lambda: upstatus(f'{message.id}upstatus.txt', msg), daemon=True)
        upsta.start()
    if not video:
        app.send_document(message.chat.id, document=file, caption=capt, force_document=True, reply_to_message_id=message.id, progress=uprogress, progress_args=[message])
    else:
        app.send_video(message.chat.id, video=file, caption=capt, thumb=thumb, duration=duration, width=widht, height=height, reply_to_message_id=message.id, progress=uprogress, progress_args=[message])
    if thumb is not None:
        os.remove(thumb)
    if os.path.exists(f'{message.id}upstatus.txt'):
        os.remove(f'{message.id}upstatus.txt')
    if msg is not None and not multi:
        app.delete_messages(message.chat.id, message_ids=msg.id)

# Progress functions
def uprogress(current, total, message):
    with open(f'{message.id}upstatus.txt', "w") as fileup:
        fileup.write(f"{current * 100 / total:.1f}%")

def dprogress(current, total, message):
    with open(f'{message.id}downstatus.txt', "w") as fileup:
        fileup.write(f"{current * 100 / total:.1f}%")

# Status functions
def upstatus(statusfile, message):
    while True:
        if os.path.exists(statusfile):
            break
    time.sleep(5)
    while os.path.exists(statusfile):
        with open(statusfile, "r") as upread:
            txt = upread.read()
        try:
            app.edit_message_text(message.chat.id, message.id, f"__Uploaded__ : **{txt}**")
            time.sleep(10)
        except:
            time.sleep(5)

def downstatus(statusfile, message):
    while True:
        if os.path.exists(statusfile):
            break
    time.sleep(5)
    while os.path.exists(statusfile):
        with open(statusfile, "r") as upread:
            txt = upread.read()
        try:
            app.edit_message_text(message.chat.id, message.id, f"__Downloaded__ : **{txt}**")
            time.sleep(10)
        except:
            time.sleep(5)

# Message handlers
@app.on_message(filters.video)
def video(client, message):
    # Add the message to the queue
    message_queue.put(message)

@app.on_message(filters.command(['start']))
def start(client, message):
    app.send_message(message.chat.id, f"Welcome {message.from_user.mention}\nSend a **File** first and then you can choose **Extension**\n\n__Want to know more about me?\nUse /help - to get List of Commands\nUse /detail - to get List of Supported Extensions\n\nI also have Special AI features including ChatBot, you don't believe me? Ask me anything__", reply_to_message_id=message.id)

# Start worker threads
threads = []
for _ in range(MAX_THREADS):
    t = threading.Thread(target=process_messages, daemon=True)
    t.start()
    threads.append(t)

# Run the bot
app.run()

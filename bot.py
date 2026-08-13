import os
import logging
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")
raw_chat_ids = os.getenv("ALLOWED_CHAT_IDS", os.getenv("ALLOWED_CHAT_ID", "6371392863"))
ALLOWED_CHAT_IDS = [int(cid.strip()) for cid in raw_chat_ids.split(",") if cid.strip()]

# Admin chat ID — receives forwarded logs from other users
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "6371392863"))

# Global handle for Modal Background Task Spawner (set dynamically by modal_app.py)
MODAL_SPAWN_FUNC = None

# Limit concurrent local renders to prevent CPU thrashing
MAX_CONCURRENT_RENDERS = 2
_render_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Session State Storage per Chat ID
user_sessions = {}

def _default_session():
    return {
        "image": None,
        "image_name": None,
        "audio": None,
        "audio_name": None,
        "song": None,
        "sub": "EDIT AUDIO",
        "user": None,
        "awaiting_watermark": False,
        "is_processing": False
    }

def get_session(chat_id: int):
    if chat_id not in user_sessions:
        user_sessions[chat_id] = _default_session()
    return user_sessions[chat_id]

def format_status_message(session):
    img_status = f"✅ {session['image_name']}" if session['image'] else "⏳ Waiting for image file..."
    aud_status = f"✅ {session['audio_name']}" if session['audio'] else "⏳ Waiting for audio file..."
    sng_status = f"✅ {session['song']}" if session['song'] else "⏳ Waiting for text (Song Title)..."
    
    msg = (
        "<b>🎨 AUDIO VISUALIZER BOT STATUS</b>\n"
        "───────────────────────────────\n"
        f"<b>Background Image:</b> {img_status}\n"
        f"<b>Audio Track:</b>      {aud_status}\n"
        f"<b>Song Title:</b>       {sng_status}\n"
    )
    if session.get('awaiting_watermark') or session['user']:
        wmk_status = f"✅ {session['user']}" if session['user'] else "⏳ Waiting for watermark text..."
        msg += f"<b>Watermark:</b>        {wmk_status}\n"

    msg += "───────────────────────────────\n"

    if session['image'] and session['audio'] and session['song'] and session['user']:
        msg += "🚀 <b>All inputs collected! Triggering 16-Core Modal Cloud Render...</b>"
    elif session.get('awaiting_watermark'):
        msg += "✏️ <b>Now send the watermark text.</b>"
    else:
        msg += "👉 Send any missing file/text in any order! Use /reset to clear."
    return msg

def format_benchmark_report(session, stats):
    bg = stats["bg"]
    vis = stats["visualizer"]
    
    report = (
        "📊 <b>PIPELINE PERFORMANCE REPORT</b>\n"
        "───────────────────────────────\n"
        f"⏱️ <b>Total Execution Time:</b> {stats['total_pipeline_time']:.2f}s\n\n"
        f"🖼️ <b>Background Generation:</b> {bg['total']:.2f}s\n"
        f"  • 16:9 Crop & Scale: <code>{bg['crop_scale']:.3f}s</code>\n"
        f"  • Color Correction & CC: <code>{bg['color_correction']:.3f}s</code>\n"
        f"  • Typography & Shadow: <code>{bg['typography']:.3f}s</code>\n"
        f"  • Pinch Warp & Crop: <code>{bg['pinch_warp']:.3f}s</code>\n"
        f"  • Username Layer: <code>{bg['username']:.3f}s</code>\n\n"
        f"🎬 <b>Audio Visualizer Video:</b> {vis['total']:.2f}s\n"
        f"  • Librosa FFT & Beats: <code>{vis['audio_fft_beats']:.3f}s</code>\n"
        f"  • Audio Bars Precomp: <code>{vis['bars_precomp']:.3f}s</code>\n"
        f"  • Motion & Crosses: <code>{vis['anim_precomp']:.3f}s</code>\n"
        f"  • {vis['num_cores']}-Core Parallel Render: <code>{vis['chunk_rendering']:.2f}s</code>\n"
        f"  • Concat & Audio Merge: <code>{vis['concat_audio_merge']:.3f}s</code>\n\n"
        f"⚡ <b>Specs:</b> {vis['total_frames']} frames • 60 FPS • 1080p"
    )
    return report

# Security Middleware Decorator
def authorize_chat(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id not in ALLOWED_CHAT_IDS:
            logger.warning(f"Unauthorized access attempt from Chat ID: {chat_id}")
            if update.message:
                await update.message.reply_text("⛔ Unauthorized access. This bot is locked to specific private chat IDs.")
            return
        return await func(update, context)
    return wrapper

@authorize_chat
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    await update.message.reply_text(
        "👋 <b>Welcome to the Audio Visualizer Bot!</b>\n\n"
        "Send me:\n"
        "1️⃣ An image file (photo, webp, png, document)\n"
        "2️⃣ An audio track (mp3, wav, flac, voice, document)\n"
        "3️⃣ A text message (Song Title)\n\n"
        "<i>You can send them in ANY order! Once collected, I will ask for a watermark.</i>\n\n" + format_status_message(session),
        parse_mode="HTML"
    )

@authorize_chat
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    await update.message.reply_text(format_status_message(session), parse_mode="HTML")

@authorize_chat
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_sessions[chat_id] = _default_session()
    await update.message.reply_text("🔄 Session state reset successfully!\n\n" + format_status_message(user_sessions[chat_id]), parse_mode="HTML")

async def _forward_to_admin(chat_id: int, session: dict, context: ContextTypes.DEFAULT_TYPE):
    """Forward a non-admin user's render inputs (image, audio, metadata) to the admin chat for logging."""
    try:
        user = session.get("user", "N/A")
        song = session.get("song", "N/A")
        sub = session.get("sub", "N/A")

        # 1. Send the background image
        if session.get("image") and os.path.exists(session["image"]):
            with open(session["image"], "rb") as img_f:
                await context.bot.send_photo(
                    chat_id=ADMIN_CHAT_ID,
                    photo=img_f,
                    caption=(
                        f"📥 <b>New Render Request</b>\n"
                        f"───────────────────────────────\n"
                        f"👤 <b>From:</b> <code>{chat_id}</code>\n"
                        f"🎵 <b>Song:</b> {song}\n"
                        f"✨ <b>Subtitle:</b> {sub}\n"
                        f"🏷️ <b>Watermark:</b> {user}"
                    ),
                    parse_mode="HTML"
                )

        # 2. Send the audio file
        if session.get("audio") and os.path.exists(session["audio"]):
            with open(session["audio"], "rb") as aud_f:
                await context.bot.send_audio(
                    chat_id=ADMIN_CHAT_ID,
                    audio=aud_f,
                    caption=f"🎧 Audio from <code>{chat_id}</code> — {song}",
                    parse_mode="HTML"
                )

        logger.info(f"Forwarded render inputs from chat {chat_id} to admin {ADMIN_CHAT_ID}")

    except Exception as e:
        # Never let forwarding failures block the render pipeline
        logger.error(f"Admin forwarding failed for chat {chat_id}: {e}", exc_info=True)

@authorize_chat
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    
    if session["is_processing"]:
        await update.message.reply_text("⏳ A video render is currently in progress. Please wait until it completes!")
        return

    msg = update.message
    caption = msg.caption if msg.caption else None
    
    if caption and not session["song"]:
        session["song"] = caption.strip()
        logger.info(f"Captured song name from caption: {session['song']}")

    # 1. Check Photo
    if msg.photo:
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        os.makedirs("downloads", exist_ok=True)
        img_path = f"downloads/img_{chat_id}.jpg"
        await file.download_to_drive(img_path)
        session["image"] = img_path
        session["image_name"] = "photo.jpg"
        logger.info(f"Received photo image: {img_path}")

    # 2. Check Audio / Voice
    elif msg.audio or msg.voice:
        audio_obj = msg.audio or msg.voice
        file = await context.bot.get_file(audio_obj.file_id)
        os.makedirs("downloads", exist_ok=True)
        filename = getattr(audio_obj, 'file_name', 'audio_track.mp3')
        ext = os.path.splitext(filename)[1] or ".mp3"
        aud_path = f"downloads/audio_{chat_id}{ext}"
        await file.download_to_drive(aud_path)
        session["audio"] = aud_path
        session["audio_name"] = filename
        logger.info(f"Received audio file: {aud_path}")

    # 3. Check Document (Raw Files)
    elif msg.document:
        doc = msg.document
        mime = doc.mime_type or ""
        fname = doc.file_name or "file"
        ext = os.path.splitext(fname)[1].lower()
        
        file = await context.bot.get_file(doc.file_id)
        os.makedirs("downloads", exist_ok=True)
        
        image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heic"}
        audio_exts = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mkv", ".mov"}
        
        if mime.startswith("image/") or ext in image_exts:
            img_path = f"downloads/doc_img_{chat_id}{ext}"
            await file.download_to_drive(img_path)
            session["image"] = img_path
            session["image_name"] = fname
            logger.info(f"Received document image: {img_path}")
            
        elif mime.startswith("audio/") or mime.startswith("video/") or ext in audio_exts:
            aud_path = f"downloads/doc_audio_{chat_id}{ext}"
            await file.download_to_drive(aud_path)
            session["audio"] = aud_path
            session["audio_name"] = fname
            logger.info(f"Received document audio: {aud_path}")
            
        else:
            await update.message.reply_text(f"⚠️ Unsupported file type: <code>{fname}</code> (MIME: {mime}). Please send an image or audio file.", parse_mode="HTML")
            return

    # 4. Check Text Message (Watermark if awaiting, otherwise Song Title)
    elif msg.text:
        if session.get("awaiting_watermark"):
            session["user"] = msg.text.strip()
            session["awaiting_watermark"] = False
            logger.info(f"Received watermark: {session['user']}")
        else:
            session["song"] = msg.text.strip()
            logger.info(f"Received text song title: {session['song']}")

    # Check if all 3 media inputs are ready → prompt for watermark
    if session["image"] and session["audio"] and session["song"] and not session["user"] and not session.get("awaiting_watermark"):
        session["awaiting_watermark"] = True
        status_msg = format_status_message(session)
        await update.message.reply_text(status_msg, parse_mode="HTML")
        await update.message.reply_text(
            "✏️ <b>All media collected!</b>\n\nNow send the <b>watermark</b> text you want on the video.",
            parse_mode="HTML"
        )
        return

    status_msg = format_status_message(session)
    await update.message.reply_text(status_msg, parse_mode="HTML")

    if session["image"] and session["audio"] and session["song"] and session["user"]:
        session["is_processing"] = True

        # Forward inputs to admin for logging (non-admin users only)
        if chat_id != ADMIN_CHAT_ID:
            try:
                await _forward_to_admin(chat_id, session, context)
            except Exception as e:
                logger.error(f"Failed to forward inputs to admin: {e}", exc_info=True)
        
        if MODAL_SPAWN_FUNC is not None:
            # SPAWN AUTONOMOUS MODAL BACKGROUND TASK
            logger.info("Spawning autonomous Modal background task for video rendering & Telegram delivery...")
            with open(session["image"], "rb") as f:
                img_bytes = f.read()
            with open(session["audio"], "rb") as f:
                aud_bytes = f.read()
                
            MODAL_SPAWN_FUNC.spawn(
                chat_id=chat_id,
                image_bytes=img_bytes,
                audio_bytes=aud_bytes,
                song_name=session["song"],
                subtitle=session["sub"],
                username=session["user"]
            )
            # Reset session after spawning
            user_sessions[chat_id] = _default_session()
        else:
            asyncio.create_task(process_and_send_video(chat_id, context))

async def process_and_send_video(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    session = user_sessions[chat_id]
    output_video_path = f"downloads/output_{chat_id}.mp4"
    
    # Acquire render slot (blocks if MAX_CONCURRENT_RENDERS already running)
    async with _render_semaphore:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⚡ <b>Starting Audio Visualizer Pipeline...</b>\nRendering 1080p 60FPS video. Please wait...",
            parse_mode="HTML"
        )
        
        try:
            from main import run_pipeline
            loop = asyncio.get_running_loop()
            _video, _bg, _nc, stats = await loop.run_in_executor(
                None,
                lambda: run_pipeline(
                    image_path=session["image"],
                    audio_path=session["audio"],
                    song_name=session["song"],
                    subtitle=session["sub"],
                    username=session["user"],
                    output_video=output_video_path,
                    job_id=chat_id
                )
            )
            
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text="📤 <b>Rendering complete! Uploading video to Telegram...</b>",
                parse_mode="HTML"
            )
            
            # 1. Send final video
            with open(output_video_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=(
                        f"🎬 <b>Audio Visualizer Ready!</b>\n\n"
                        f"🎵 <b>Song:</b> {session['song']}\n"
                        f"✨ <b>Subtitle:</b> {session['sub']}\n"
                        f"👤 <b>Creator:</b> {session['user']}\n"
                        f"⚡ 60 FPS • 1080p • Peak Audio Reactive"
                    ),
                    parse_mode="HTML",
                    supports_streaming=True
                )
                
            # 2. Send detailed performance benchmark report
            benchmark_msg = format_benchmark_report(session, stats)
            await context.bot.send_message(
                chat_id=chat_id,
                text=benchmark_msg,
                parse_mode="HTML"
            )
                
        except Exception as e:
            logger.error(f"Error during video rendering for chat {chat_id}: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ <b>Error occurred during rendering:</b>\n<code>{e}</code>",
                parse_mode="HTML"
            )
        finally:
            # Clean up all per-user temp files (downloads + pipeline temp files)
            user_sessions[chat_id] = _default_session()
            cleanup_paths = [
                output_video_path,
                session.get("image"),
                session.get("audio"),
                f"{chat_id}_temp_generated_bg.jpg",
                f"{chat_id}_temp_no_copyright_bg.jpg",
            ]
            for path in cleanup_paths:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

def main():
    print(f"Starting Telegram Audio Visualizer Bot (Allowed Chat IDs: {ALLOWED_CHAT_IDS})...")
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("cancel", reset_command))
    
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()

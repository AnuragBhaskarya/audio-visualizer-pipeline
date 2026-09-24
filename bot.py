import os
import uuid
import time
import shutil
import logging
import asyncio
from enum import Enum, auto
from typing import Optional, Dict
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

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION CONSTANTS
# ═══════════════════════════════════════════════════════════════
SESSION_TTL_SECONDS = 30 * 60       # 30 minutes inactivity timeout
MAX_FILE_SIZE_MB = 50               # Max file size accepted
MAX_AUDIO_DURATION_SECONDS = 600    # 10 minutes max audio
DOWNLOAD_RETRY_ATTEMPTS = 3         # Retry count for Telegram file downloads
DOWNLOAD_RETRY_BASE_DELAY = 1.5     # Seconds, doubled each retry
MAX_TEXT_LENGTH = 200               # Max chars for text inputs
DOWNLOADS_BASE_DIR = "downloads"

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# FINITE STATE MACHINE — Deterministic, no boolean flag soup
# ═══════════════════════════════════════════════════════════════

class SessionState(Enum):
    COLLECTING = auto()            # Gathering image / audio / song (any order)
    AWAITING_WATERMARK = auto()    # All 3 core inputs collected, need watermark text
    AWAITING_FULL_SONG = auto()    # Need full song name for iTunes (admin only)
    PROCESSING = auto()            # Render in progress
    DONE = auto()                  # Completed, pending cleanup


# ═══════════════════════════════════════════════════════════════
# SESSION — Isolated, batch-ID-tagged, per-user
# ═══════════════════════════════════════════════════════════════

class Session:
    """
    Each user request gets a unique batch-ID session with its own
    file directory, FSM state, and asyncio lock for race protection.
    """

    def __init__(self, chat_id: int):
        self.batch_id: str = uuid.uuid4().hex[:12]
        self.chat_id: int = chat_id
        self.state: SessionState = SessionState.COLLECTING
        self.created_at: float = time.time()
        self.last_activity: float = time.time()

        # Inputs
        self.image_path: Optional[str] = None
        self.image_name: Optional[str] = None
        self.audio_path: Optional[str] = None
        self.audio_name: Optional[str] = None
        self.song_title: Optional[str] = None
        self.watermark: Optional[str] = None
        self.full_song: Optional[str] = None   # admin only
        self.subtitle: str = "EDIT AUDIO"

        # Tracking
        self.status_msg_id: Optional[int] = None
        self.lock: asyncio.Lock = asyncio.Lock()

    # ── Properties ──

    @property
    def batch_dir(self) -> str:
        return os.path.join(DOWNLOADS_BASE_DIR, self.batch_id)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > SESSION_TTL_SECONDS

    def touch(self):
        """Bump last-activity timestamp."""
        self.last_activity = time.time()

    @property
    def has_image(self) -> bool:
        return self.image_path is not None

    @property
    def has_audio(self) -> bool:
        return self.audio_path is not None

    @property
    def has_song(self) -> bool:
        return self.song_title is not None

    @property
    def has_core_inputs(self) -> bool:
        return self.has_image and self.has_audio and self.has_song

    @property
    def is_ready_for_render(self) -> bool:
        """All required inputs present for this user type."""
        if not self.has_core_inputs or not self.watermark:
            return False
        if self.chat_id == ADMIN_CHAT_ID and not self.full_song:
            return False
        return True

    def missing_inputs(self) -> list:
        missing = []
        if not self.has_image:
            missing.append("Background Image")
        if not self.has_audio:
            missing.append("Audio Track")
        if not self.has_song:
            missing.append("Song Title")
        return missing

    def cleanup_files(self):
        """Remove this batch's entire file directory."""
        if os.path.exists(self.batch_dir):
            try:
                shutil.rmtree(self.batch_dir)
                logger.info(f"[{self.batch_id}] Cleaned up batch directory")
            except OSError as e:
                logger.warning(f"[{self.batch_id}] Cleanup failed: {e}")
        # Also clean any CWD temp files the pipeline may have created
        for suffix in ("_temp_generated_bg.jpg", "_temp_no_copyright_bg.jpg"):
            p = f"{self.batch_id}{suffix}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def snapshot(self) -> dict:
        """Atomic, serializable copy for the render pipeline."""
        return {
            "batch_id": self.batch_id,
            "chat_id": self.chat_id,
            "image": self.image_path,
            "image_name": self.image_name,
            "audio": self.audio_path,
            "audio_name": self.audio_name,
            "song": self.song_title,
            "sub": self.subtitle,
            "user": self.watermark,
            "full_song": self.full_song,
        }

    # ── Serialization (for Modal Dict webhook persistence) ──

    def to_dict(self) -> dict:
        """Serialize to a plain dict for external storage."""
        return {
            "batch_id": self.batch_id,
            "chat_id": self.chat_id,
            "state": self.state.name,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "image_path": self.image_path,
            "image_name": self.image_name,
            "audio_path": self.audio_path,
            "audio_name": self.audio_name,
            "song_title": self.song_title,
            "watermark": self.watermark,
            "full_song": self.full_song,
            "subtitle": self.subtitle,
            "status_msg_id": self.status_msg_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Reconstruct a Session from a serialized dict."""
        s = cls.__new__(cls)
        s.batch_id = data["batch_id"]
        s.chat_id = data["chat_id"]
        s.state = SessionState[data["state"]]
        s.created_at = data["created_at"]
        s.last_activity = data["last_activity"]
        s.image_path = data["image_path"]
        s.image_name = data["image_name"]
        s.audio_path = data["audio_path"]
        s.audio_name = data["audio_name"]
        s.song_title = data["song_title"]
        s.watermark = data["watermark"]
        s.full_song = data["full_song"]
        s.subtitle = data["subtitle"]
        s.status_msg_id = data["status_msg_id"]
        s.lock = asyncio.Lock()
        return s


# ═══════════════════════════════════════════════════════════════
# SESSION MANAGER — Thread-safe with auto-expiry
# ═══════════════════════════════════════════════════════════════

class SessionManager:
    """
    In-memory session storage for polling mode.
    Subclass / replace for webhook mode (Modal Dict backend).
    """

    def __init__(self):
        self._sessions: Dict[int, Session] = {}
        self._global_lock = asyncio.Lock()

    async def get(self, chat_id: int) -> Session:
        """Get or create a session. Auto-replaces expired non-PROCESSING sessions."""
        async with self._global_lock:
            session = self._sessions.get(chat_id)
            if session is None or (session.is_expired and session.state != SessionState.PROCESSING):
                if session and session.is_expired:
                    logger.info(f"Session expired for chat {chat_id} (batch {session.batch_id})")
                    session.cleanup_files()
                session = Session(chat_id)
                self._sessions[chat_id] = session
            return session

    async def save(self, session: Session):
        """No-op for in-memory — session is already a live reference."""
        pass

    async def reset(self, chat_id: int) -> Session:
        """Destroy old session, create fresh one. Returns the new session."""
        async with self._global_lock:
            old = self._sessions.pop(chat_id, None)
            if old:
                old.cleanup_files()
            new_session = Session(chat_id)
            self._sessions[chat_id] = new_session
            return new_session

    async def remove(self, chat_id: int):
        """Fully remove a session after successful completion."""
        async with self._global_lock:
            old = self._sessions.pop(chat_id, None)
            if old:
                old.cleanup_files()

    async def cleanup_expired(self):
        """Periodic sweep — cleans up all expired non-PROCESSING sessions."""
        async with self._global_lock:
            expired_ids = [
                cid for cid, s in self._sessions.items()
                if s.is_expired and s.state != SessionState.PROCESSING
            ]
            for cid in expired_ids:
                session = self._sessions.pop(cid)
                session.cleanup_files()
                logger.info(f"Auto-expired session for chat {cid} (batch {session.batch_id})")


# Module-level default — can be replaced by modal_app.py for webhook persistence
sessions = SessionManager()


# ═══════════════════════════════════════════════════════════════
# FILE DOWNLOAD — Retry with exponential backoff
# ═══════════════════════════════════════════════════════════════

async def download_file_with_retry(
    bot, file_id: str, dest_path: str,
    max_retries: int = DOWNLOAD_RETRY_ATTEMPTS
) -> bool:
    """
    Download a Telegram file with exponential backoff.
    Returns True on success, False if all retries exhausted.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            tg_file = await bot.get_file(file_id)
            await tg_file.download_to_drive(dest_path)

            # Verify non-empty
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                return True
            else:
                last_error = "Downloaded file is empty or missing"
                logger.warning(f"Download attempt {attempt}: {last_error} at {dest_path}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Download attempt {attempt}/{max_retries} failed: {e}")

        if attempt < max_retries:
            delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            await asyncio.sleep(delay)

    logger.error(f"All {max_retries} download attempts failed. Last error: {last_error}")
    return False


# ═══════════════════════════════════════════════════════════════
# INPUT VALIDATION
# ═══════════════════════════════════════════════════════════════

def validate_file_size(file_size: Optional[int]) -> Optional[str]:
    """Returns error message if file too large, None if OK."""
    if file_size and file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        return f"File too large ({file_size / 1024 / 1024:.1f}MB). Maximum is {MAX_FILE_SIZE_MB}MB."
    return None


def validate_audio_duration(duration: Optional[int]) -> Optional[str]:
    """Returns error message if audio too long, None if OK."""
    if duration and duration > MAX_AUDIO_DURATION_SECONDS:
        mins = MAX_AUDIO_DURATION_SECONDS // 60
        return f"Audio too long ({duration}s). Maximum is {MAX_AUDIO_DURATION_SECONDS}s ({mins} min)."
    return None


def validate_text_input(text: str, field_name: str) -> Optional[str]:
    """Returns error message if text is invalid, None if OK."""
    text = text.strip()
    if not text:
        return f"{field_name} cannot be empty."
    if len(text) > MAX_TEXT_LENGTH:
        return f"{field_name} too long ({len(text)} chars). Max is {MAX_TEXT_LENGTH}."
    if text.startswith("/"):
        return f"That looks like a command, not a {field_name.lower()}. Send plain text."
    return None


# ═══════════════════════════════════════════════════════════════
# STATUS MESSAGE FORMATTING
# ═══════════════════════════════════════════════════════════════

_STATE_EMOJI = {
    SessionState.COLLECTING: "📥",
    SessionState.AWAITING_WATERMARK: "✏️",
    SessionState.AWAITING_FULL_SONG: "🎵",
    SessionState.PROCESSING: "⚙️",
    SessionState.DONE: "✅",
}

def format_status_message(session: Session) -> str:
    img_status = f"✅ {session.image_name}" if session.has_image else "⏳ Waiting..."
    aud_status = f"✅ {session.audio_name}" if session.has_audio else "⏳ Waiting..."
    sng_status = f"✅ {session.song_title}" if session.has_song else "⏳ Waiting..."

    emoji = _STATE_EMOJI.get(session.state, "📥")
    msg = (
        f"<b>{emoji} AUDIO VISUALIZER — Batch <code>{session.batch_id}</code></b>\n"
        "───────────────────────────────\n"
        f"<b>Background Image:</b> {img_status}\n"
        f"<b>Audio Track:</b>      {aud_status}\n"
        f"<b>Song Title:</b>       {sng_status}\n"
    )

    # Show watermark if in or past AWAITING_WATERMARK
    if session.state in (SessionState.AWAITING_WATERMARK,) or session.watermark:
        wmk = f"✅ {session.watermark}" if session.watermark else "⏳ Waiting for watermark text..."
        msg += f"<b>Watermark:</b>        {wmk}\n"

    # Show full song for admin if relevant
    if session.chat_id == ADMIN_CHAT_ID and (
        session.state == SessionState.AWAITING_FULL_SONG or session.full_song
    ):
        fs = f"✅ {session.full_song}" if session.full_song else "⏳ Waiting for Full Song Name..."
        msg += f"<b>Full Song:</b>        {fs}\n"

    # TTL warning when < 5 minutes remaining
    remaining = SESSION_TTL_SECONDS - (time.time() - session.last_activity)
    if 0 < remaining < 300 and session.state != SessionState.PROCESSING:
        msg += f"⚠️ <i>Session expires in {int(remaining // 60)}m {int(remaining % 60)}s</i>\n"

    msg += "───────────────────────────────\n"

    # State-specific footer
    if session.state == SessionState.COLLECTING:
        missing = session.missing_inputs()
        if missing:
            msg += f"👉 Still need: <b>{', '.join(missing)}</b>\n"
            msg += "<i>Send in any order. Use /redo [image|audio|song] to replace.</i>"
        else:
            msg += "🚀 <b>All core inputs collected! Transitioning...</b>"
    elif session.state == SessionState.AWAITING_WATERMARK:
        msg += "✏️ <b>Send the watermark text you want on the video.</b>"
    elif session.state == SessionState.AWAITING_FULL_SONG:
        msg += "🎵 <b>Send the full song name (e.g., 'Ice Spice - Big Guy') for iTunes.</b>"
    elif session.state == SessionState.PROCESSING:
        msg += "⚙️ <b>Render in progress... Please wait!</b>"
    elif session.state == SessionState.DONE:
        msg += "✅ <b>Done! Send new files to start a fresh batch.</b>"

    return msg


async def send_or_update_status(session: Session, context):
    """Delete old status message, send a fresh one."""
    old_msg_id = session.status_msg_id
    if old_msg_id:
        try:
            await context.bot.delete_message(chat_id=session.chat_id, message_id=old_msg_id)
        except Exception:
            pass

    msg_text = format_status_message(session)
    try:
        new_msg = await context.bot.send_message(
            chat_id=session.chat_id, text=msg_text, parse_mode="HTML"
        )
        session.status_msg_id = new_msg.message_id
    except Exception as e:
        logger.error(f"[{session.batch_id}] Failed to send status: {e}")


def format_benchmark_report(session_snapshot, stats):
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


# ═══════════════════════════════════════════════════════════════
# SECURITY MIDDLEWARE
# ═══════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════
# SESSION EXPIRY BACKGROUND LOOP
# ═══════════════════════════════════════════════════════════════

async def _session_expiry_loop():
    """Runs as a background task, sweeps expired sessions every 60s."""
    while True:
        await asyncio.sleep(60)
        try:
            await sessions.cleanup_expired()
        except Exception as e:
            logger.error(f"Session cleanup error: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════
# STATE MACHINE TRANSITIONS
# ═══════════════════════════════════════════════════════════════

async def _try_advance_state(session: Session):
    """
    Attempt to advance the FSM from COLLECTING to the next state.
    Only runs when state == COLLECTING and all 3 core inputs are present.
    """
    if session.state != SessionState.COLLECTING or not session.has_core_inputs:
        return

    if session.chat_id == ADMIN_CHAT_ID:
        # Admin: auto-set watermark, then ask for full song
        if not session.watermark:
            session.watermark = "SO9iC"
            logger.info(f"[{session.batch_id}] Admin: auto-set watermark to 'SO9iC'")
        if not session.full_song:
            session.state = SessionState.AWAITING_FULL_SONG
            logger.info(f"[{session.batch_id}] State → AWAITING_FULL_SONG")
    else:
        # Non-admin: ask for watermark
        if not session.watermark:
            session.state = SessionState.AWAITING_WATERMARK
            logger.info(f"[{session.batch_id}] State → AWAITING_WATERMARK")


async def _check_and_trigger_render(session: Session, context):
    """If all inputs are present and we're not already rendering, fire the pipeline."""
    if not session.is_ready_for_render:
        return
    if session.state == SessionState.PROCESSING:
        return

    session.state = SessionState.PROCESSING
    logger.info(f"[{session.batch_id}] State → PROCESSING — triggering render")

    await send_or_update_status(session, context)
    await sessions.save(session)

    # Forward to admin for logging (non-admin users only)
    if session.chat_id != ADMIN_CHAT_ID:
        try:
            await _forward_to_admin(session, context)
        except Exception as e:
            logger.error(f"[{session.batch_id}] Admin forward failed: {e}", exc_info=True)

    # Take atomic snapshot before launching render
    snapshot = session.snapshot()

    if MODAL_SPAWN_FUNC is not None:
        await _trigger_modal_render(session, snapshot, context)
    else:
        asyncio.create_task(_trigger_local_render(session, snapshot, context))


# ═══════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════

@authorize_chat
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = await sessions.get(chat_id)

    admin_note = ""
    if chat_id == ADMIN_CHAT_ID:
        admin_note = (
            "\n\n🔑 <b>ADMIN:</b> After core inputs, you'll be asked for "
            "the full song name for iTunes tracking."
        )

    await update.message.reply_text(
        "👋 <b>Welcome to the Audio Visualizer Bot!</b>\n\n"
        "Send me:\n"
        "1️⃣ An image file (photo, webp, png, document)\n"
        "2️⃣ An audio track (mp3, wav, flac, voice, document)\n"
        "3️⃣ A text message (Song Title)\n\n"
        "<i>Send in ANY order! Once collected, I'll ask for a watermark.</i>\n\n"
        "<b>Commands:</b>\n"
        "/status — View current batch status\n"
        "/reset — Clear everything and start over\n"
        "/redo image — Replace just the image\n"
        "/redo audio — Replace just the audio\n"
        "/redo song — Replace just the song title\n"
        "/redo watermark — Replace the watermark"
        + admin_note,
        parse_mode="HTML"
    )
    await send_or_update_status(session, context)


@authorize_chat
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = await sessions.get(chat_id)
    await send_or_update_status(session, context)


@authorize_chat
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    old = await sessions.get(chat_id)

    if old.state == SessionState.PROCESSING:
        await update.message.reply_text(
            "⏳ Cannot reset while a render is in progress. Please wait!\n"
            f"<i>Batch: <code>{old.batch_id}</code></i>",
            parse_mode="HTML"
        )
        return

    new_session = await sessions.reset(chat_id)
    await update.message.reply_text(
        f"🔄 Session reset! New batch: <code>{new_session.batch_id}</code>",
        parse_mode="HTML"
    )
    await send_or_update_status(new_session, context)


@authorize_chat
async def redo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Granular redo — replace ONE input without nuking the whole session."""
    chat_id = update.effective_chat.id
    session = await sessions.get(chat_id)

    if session.state == SessionState.PROCESSING:
        await update.message.reply_text("⏳ Cannot modify while rendering. Please wait!")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "<b>Usage:</b> /redo [image|audio|song|watermark]\n"
            "Example: <code>/redo image</code> — clears only the image so you can send a new one.",
            parse_mode="HTML"
        )
        return

    target = args[0].lower().strip()

    async with session.lock:
        session.touch()
        reverted = False

        if target == "image":
            if session.image_path and os.path.exists(session.image_path):
                try:
                    os.remove(session.image_path)
                except OSError:
                    pass
            session.image_path = None
            session.image_name = None
            reverted = True
            await update.message.reply_text("🖼️ Image cleared. Send a new one!")

        elif target == "audio":
            if session.audio_path and os.path.exists(session.audio_path):
                try:
                    os.remove(session.audio_path)
                except OSError:
                    pass
            session.audio_path = None
            session.audio_name = None
            reverted = True
            await update.message.reply_text("🎵 Audio cleared. Send a new one!")

        elif target == "song":
            session.song_title = None
            reverted = True
            await update.message.reply_text("📝 Song title cleared. Send a new one!")

        elif target == "watermark":
            session.watermark = None
            reverted = True
            await update.message.reply_text("🏷️ Watermark cleared. Send a new one!")

        else:
            await update.message.reply_text(
                f"Unknown target: <code>{target}</code>. "
                "Use: <code>image</code>, <code>audio</code>, <code>song</code>, <code>watermark</code>",
                parse_mode="HTML"
            )
            return

        # Revert state back to COLLECTING if we removed a core input
        if reverted and session.state in (
            SessionState.AWAITING_WATERMARK,
            SessionState.AWAITING_FULL_SONG,
        ):
            session.state = SessionState.COLLECTING
            logger.info(f"[{session.batch_id}] State reverted → COLLECTING after /redo {target}")

        await sessions.save(session)

    await send_or_update_status(session, context)


# ═══════════════════════════════════════════════════════════════
# MAIN MESSAGE HANDLER — State-machine routed, lock-protected
# ═══════════════════════════════════════════════════════════════

@authorize_chat
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = await sessions.get(chat_id)
    msg = update.message
    if not msg:
        return

    async with session.lock:
        session.touch()

        # ── Block all input during render ──
        if session.state == SessionState.PROCESSING:
            await msg.reply_text(
                "⏳ A render is in progress. Please wait!\n"
                f"<i>Batch: <code>{session.batch_id}</code></i>",
                parse_mode="HTML"
            )
            return

        # ── Route by message type ──
        handled = False

        if msg.photo:
            handled = await _handle_photo(session, msg, context)
        elif msg.audio or msg.voice:
            handled = await _handle_audio(session, msg, context)
        elif msg.document:
            handled = await _handle_document(session, msg, context)
        elif msg.sticker and not msg.sticker.is_animated and not msg.sticker.is_video:
            handled = await _handle_sticker(session, msg, context)
        elif msg.text:
            handled = await _handle_text(session, msg, context)
        else:
            await msg.reply_text("❓ Unsupported message type. Send an image, audio, or text.")
            return

        if not handled:
            return

        # ── Extract song title from media caption if still needed ──
        if msg.caption and not session.has_song and session.state == SessionState.COLLECTING:
            err = validate_text_input(msg.caption, "Song Title")
            if not err:
                session.song_title = msg.caption.strip()
                logger.info(f"[{session.batch_id}] Song title from caption: {session.song_title}")

        # ── FSM: Try to advance state ──
        await _try_advance_state(session)

        # ── Update status dashboard ──
        await send_or_update_status(session, context)
        await sessions.save(session)

        # ── Trigger render if ready ──
        await _check_and_trigger_render(session, context)


# ═══════════════════════════════════════════════════════════════
# INPUT HANDLERS — Each returns True if the input was accepted
# ═══════════════════════════════════════════════════════════════

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".heic"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mkv", ".mov"}


async def _handle_photo(session: Session, msg, context) -> bool:
    if session.state != SessionState.COLLECTING:
        await _wrong_type_hint(session, msg, "a photo")
        return False

    photo = msg.photo[-1]

    err = validate_file_size(photo.file_size)
    if err:
        await msg.reply_text(f"⚠️ {err}")
        return False

    dest = os.path.join(session.batch_dir, f"image_{session.batch_id}.jpg")
    if not await download_file_with_retry(context.bot, photo.file_id, dest):
        await msg.reply_text("❌ Failed to download photo after multiple retries. Please try again.")
        return False

    _remove_old_file(session.image_path, dest)
    session.image_path = dest
    session.image_name = "photo.jpg"
    logger.info(f"[{session.batch_id}] ✅ Image received → {dest}")
    return True


async def _handle_audio(session: Session, msg, context) -> bool:
    if session.state != SessionState.COLLECTING:
        await _wrong_type_hint(session, msg, "an audio file")
        return False

    audio_obj = msg.audio or msg.voice

    err = validate_file_size(audio_obj.file_size)
    if err:
        await msg.reply_text(f"⚠️ {err}")
        return False

    dur_err = validate_audio_duration(getattr(audio_obj, "duration", None))
    if dur_err:
        await msg.reply_text(f"⚠️ {dur_err}")
        return False

    filename = getattr(audio_obj, "file_name", "audio_track.mp3") or "audio_track.mp3"
    ext = os.path.splitext(filename)[1] or ".mp3"
    dest = os.path.join(session.batch_dir, f"audio_{session.batch_id}{ext}")

    if not await download_file_with_retry(context.bot, audio_obj.file_id, dest):
        await msg.reply_text("❌ Failed to download audio after multiple retries. Please try again.")
        return False

    _remove_old_file(session.audio_path, dest)
    session.audio_path = dest
    session.audio_name = filename
    logger.info(f"[{session.batch_id}] ✅ Audio received → {dest} ({filename})")
    return True


async def _handle_document(session: Session, msg, context) -> bool:
    if session.state != SessionState.COLLECTING:
        await _wrong_type_hint(session, msg, "a file")
        return False

    doc = msg.document
    mime = doc.mime_type or ""
    fname = doc.file_name or "file"
    ext = os.path.splitext(fname)[1].lower()

    err = validate_file_size(doc.file_size)
    if err:
        await msg.reply_text(f"⚠️ {err}")
        return False

    if mime.startswith("image/") or ext in IMAGE_EXTS:
        dest = os.path.join(session.batch_dir, f"doc_image_{session.batch_id}{ext}")
        if not await download_file_with_retry(context.bot, doc.file_id, dest):
            await msg.reply_text("❌ Failed to download image. Please try again.")
            return False
        _remove_old_file(session.image_path, dest)
        session.image_path = dest
        session.image_name = fname
        logger.info(f"[{session.batch_id}] ✅ Document image → {dest} ({fname})")
        return True

    elif mime.startswith("audio/") or mime.startswith("video/") or ext in AUDIO_EXTS:
        dest = os.path.join(session.batch_dir, f"doc_audio_{session.batch_id}{ext}")
        if not await download_file_with_retry(context.bot, doc.file_id, dest):
            await msg.reply_text("❌ Failed to download audio. Please try again.")
            return False
        _remove_old_file(session.audio_path, dest)
        session.audio_path = dest
        session.audio_name = fname
        logger.info(f"[{session.batch_id}] ✅ Document audio → {dest} ({fname})")
        return True

    else:
        await msg.reply_text(
            f"⚠️ Unsupported file: <code>{fname}</code> (MIME: {mime}).\n"
            "Please send an image or audio file.",
            parse_mode="HTML"
        )
        return False


async def _handle_sticker(session: Session, msg, context) -> bool:
    if session.state != SessionState.COLLECTING:
        await _wrong_type_hint(session, msg, "a sticker")
        return False

    dest = os.path.join(session.batch_dir, f"sticker_{session.batch_id}.webp")
    if not await download_file_with_retry(context.bot, msg.sticker.file_id, dest):
        await msg.reply_text("❌ Failed to download sticker. Please try again.")
        return False

    _remove_old_file(session.image_path, dest)
    session.image_path = dest
    session.image_name = "sticker.webp"
    logger.info(f"[{session.batch_id}] ✅ Sticker image → {dest}")
    return True


async def _handle_text(session: Session, msg, context) -> bool:
    """Route text by FSM state — deterministic, never ambiguous."""
    text = msg.text.strip()

    # ── AWAITING_WATERMARK: this text is the watermark ──
    if session.state == SessionState.AWAITING_WATERMARK:
        err = validate_text_input(text, "Watermark")
        if err:
            await msg.reply_text(f"⚠️ {err}")
            return False
        session.watermark = text
        logger.info(f"[{session.batch_id}] Watermark set: {text}")

        # Admin goes to AWAITING_FULL_SONG next
        if session.chat_id == ADMIN_CHAT_ID and not session.full_song:
            session.state = SessionState.AWAITING_FULL_SONG
            logger.info(f"[{session.batch_id}] State → AWAITING_FULL_SONG")
        return True

    # ── AWAITING_FULL_SONG: this text is the full song name ──
    if session.state == SessionState.AWAITING_FULL_SONG:
        err = validate_text_input(text, "Full Song Name")
        if err:
            await msg.reply_text(f"⚠️ {err}")
            return False
        session.full_song = text
        logger.info(f"[{session.batch_id}] Full song set: {text}")
        return True

    # ── COLLECTING: this text is the song title ──
    if session.state == SessionState.COLLECTING:
        if session.has_song:
            await msg.reply_text(
                f"📝 Song title already set to: <b>{session.song_title}</b>\n"
                "Use <code>/redo song</code> to replace it.",
                parse_mode="HTML"
            )
            return False

        err = validate_text_input(text, "Song Title")
        if err:
            await msg.reply_text(f"⚠️ {err}")
            return False
        session.song_title = text
        logger.info(f"[{session.batch_id}] Song title set: {text}")
        return True

    return False


# ═══════════════════════════════════════════════════════════════
# HELPER UTILITIES
# ═══════════════════════════════════════════════════════════════

def _remove_old_file(old_path: Optional[str], new_path: str):
    """Remove the previous file if it exists and differs from the new one."""
    if old_path and old_path != new_path and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass


async def _wrong_type_hint(session: Session, msg, what: str):
    """Inform user they sent the wrong type for the current state."""
    if session.state == SessionState.AWAITING_WATERMARK:
        await msg.reply_text(
            f"✏️ I'm waiting for <b>watermark text</b>, not {what}.\n"
            "Send text, or use <code>/redo image</code> / <code>/redo audio</code> to replace a file.",
            parse_mode="HTML"
        )
    elif session.state == SessionState.AWAITING_FULL_SONG:
        await msg.reply_text(
            f"🎵 I'm waiting for the <b>full song name</b>, not {what}.\nSend text.",
            parse_mode="HTML"
        )


# ═══════════════════════════════════════════════════════════════
# ADMIN FORWARDING
# ═══════════════════════════════════════════════════════════════

async def _forward_to_admin(session: Session, context):
    """Forward a non-admin user's render inputs to admin for logging."""
    try:
        if session.image_path and os.path.exists(session.image_path):
            with open(session.image_path, "rb") as img_f:
                await context.bot.send_photo(
                    chat_id=ADMIN_CHAT_ID,
                    photo=img_f,
                    caption=(
                        f"📥 <b>New Render Request</b>\n"
                        f"───────────────────────────────\n"
                        f"🆔 <b>Batch:</b> <code>{session.batch_id}</code>\n"
                        f"👤 <b>From:</b> <code>{session.chat_id}</code>\n"
                        f"🎵 <b>Song:</b> {session.song_title}\n"
                        f"✨ <b>Subtitle:</b> {session.subtitle}\n"
                        f"🏷️ <b>Watermark:</b> {session.watermark}"
                    ),
                    parse_mode="HTML"
                )

        if session.audio_path and os.path.exists(session.audio_path):
            with open(session.audio_path, "rb") as aud_f:
                await context.bot.send_audio(
                    chat_id=ADMIN_CHAT_ID,
                    audio=aud_f,
                    caption=f"🎧 Audio — Batch <code>{session.batch_id}</code>",
                    parse_mode="HTML"
                )

        logger.info(f"[{session.batch_id}] Forwarded to admin {ADMIN_CHAT_ID}")
    except Exception as e:
        # Never let forwarding failures block the render
        logger.error(f"[{session.batch_id}] Admin forward failed: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════
# RENDER TRIGGERS
# ═══════════════════════════════════════════════════════════════

async def _trigger_modal_render(session: Session, snapshot: dict, context):
    """Spawn a Modal cloud render background task."""
    batch_id = snapshot["batch_id"]
    logger.info(f"[{batch_id}] Spawning Modal background task")

    try:
        with open(snapshot["image"], "rb") as f:
            img_bytes = f.read()
        with open(snapshot["audio"], "rb") as f:
            aud_bytes = f.read()
    except (IOError, OSError) as e:
        logger.error(f"[{batch_id}] Failed to read files for Modal: {e}")
        session.state = SessionState.COLLECTING
        await sessions.save(session)
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"❌ Failed to read input files: <code>{e}</code>\n"
                "Your session is preserved. Try <code>/redo</code> or <code>/reset</code>."
            ),
            parse_mode="HTML"
        )
        return

    try:
        MODAL_SPAWN_FUNC.spawn(
            chat_id=snapshot["chat_id"],
            image_bytes=img_bytes,
            audio_bytes=aud_bytes,
            song_name=snapshot["song"],
            subtitle=snapshot["sub"],
            username=snapshot["user"],
            full_song=snapshot.get("full_song")
        )
        # Clean up after successful spawn
        session.cleanup_files()
        await sessions.remove(session.chat_id)
        logger.info(f"[{batch_id}] Modal task spawned, session cleaned up")

    except Exception as e:
        logger.error(f"[{batch_id}] Modal spawn failed: {e}", exc_info=True)
        session.state = SessionState.COLLECTING
        await sessions.save(session)
        await context.bot.send_message(
            chat_id=session.chat_id,
            text=(
                f"❌ Cloud render failed to start: <code>{e}</code>\n"
                "Your inputs are preserved. Try again or <code>/reset</code>."
            ),
            parse_mode="HTML"
        )


async def _trigger_local_render(session: Session, snapshot: dict, context):
    """Execute local render with semaphore throttling and error recovery."""
    batch_id = snapshot["batch_id"]
    chat_id = snapshot["chat_id"]
    output_video_path = os.path.join(session.batch_dir, f"output_{batch_id}.mp4")

    async with _render_semaphore:
        status_msg = await context.bot.send_message(
            chat_id=chat_id,
            text="⚡ <b>Starting Audio Visualizer Pipeline...</b>\nRendering 1080p 60FPS video. Please wait...",
            parse_mode="HTML"
        )

        try:
            from main import run_pipeline
            from caption_generator import fetch_song_details, generate_caption_async
            loop = asyncio.get_running_loop()

            task_video = loop.run_in_executor(
                None,
                lambda: run_pipeline(
                    image_path=snapshot["image"],
                    audio_path=snapshot["audio"],
                    song_name=snapshot["song"],
                    subtitle=snapshot["sub"],
                    username=snapshot["user"],
                    output_video=output_video_path,
                    job_id=batch_id
                )
            )

            async def run_caption_gen():
                if chat_id == ADMIN_CHAT_ID and snapshot.get("full_song"):
                    itunes_data = await loop.run_in_executor(
                        None, fetch_song_details, snapshot["full_song"]
                    )
                    return await generate_caption_async(
                        snapshot["full_song"], itunes_data, snapshot["user"]
                    )
                return None

            task_caption = asyncio.create_task(run_caption_gen())

            results, generated_caption = await asyncio.gather(task_video, task_caption)
            _video, _bg, _nc, stats = results

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg.message_id,
                text="📤 <b>Rendering complete! Uploading video to Telegram...</b>",
                parse_mode="HTML"
            )

            # Send final video
            with open(output_video_path, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video_file,
                    caption=(
                        f"🎬 <b>Audio Visualizer Ready!</b>\n\n"
                        f"🎵 <b>Song:</b> {snapshot['song']}\n"
                        f"✨ <b>Subtitle:</b> {snapshot['sub']}\n"
                        f"👤 <b>Creator:</b> {snapshot['user']}\n"
                        f"⚡ 60 FPS • 1080p • Peak Audio Reactive"
                    ),
                    parse_mode="HTML",
                    supports_streaming=True
                )

            # AI-generated caption for admin
            if chat_id == ADMIN_CHAT_ID and generated_caption:
                import html
                escaped = html.escape(generated_caption)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"📝 <b>AI-Generated SEO Caption</b> (Tap to copy):\n\n<pre>{escaped}</pre>",
                    parse_mode="HTML"
                )

            # Performance benchmark report
            benchmark_msg = format_benchmark_report(snapshot, stats)
            await context.bot.send_message(
                chat_id=chat_id,
                text=benchmark_msg,
                parse_mode="HTML"
            )

        except Exception as e:
            logger.error(f"[{batch_id}] Render error: {e}", exc_info=True)
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ <b>Render failed:</b>\n<code>{e}</code>\n\n"
                    "Your inputs are preserved. Send /reset to start over or try again."
                ),
                parse_mode="HTML"
            )
            # ── IDEMPOTENT RECOVERY: revert state so user can retry ──
            current = await sessions.get(chat_id)
            if current.batch_id == batch_id:
                current.state = SessionState.COLLECTING
                await sessions.save(current)
            return

        finally:
            # Clean pipeline temp files from CWD (the batch_dir is cleaned by sessions.remove)
            for suffix in ("_temp_generated_bg.jpg", "_temp_no_copyright_bg.jpg"):
                p = f"{batch_id}{suffix}"
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

        # ── SUCCESS: full cleanup ──
        await sessions.remove(chat_id)
        logger.info(f"[{batch_id}] Render complete, session cleaned up")


# ═══════════════════════════════════════════════════════════════
# APPLICATION ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

async def _post_init(application):
    """Called after bot initialization — starts background tasks."""
    asyncio.create_task(_session_expiry_loop())
    logger.info("Session expiry background loop started")


def main():
    print(f"Starting Telegram Audio Visualizer Bot (Allowed Chat IDs: {ALLOWED_CHAT_IDS})...")
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("cancel", reset_command))
    app.add_handler(CommandHandler("redo", redo_command))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()

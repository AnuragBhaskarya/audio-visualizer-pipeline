import os
import modal

# Define Modal App Name
app = modal.App("audio-visualizer-pipeline")

# Shared Modal Dict for persisting user session state across webhook requests
user_sessions = modal.Dict.from_name("audio-vis-user-sessions", create_if_missing=True)

# Define Modal Cloud Image with FFmpeg and dependencies
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "opencv-python-headless",
        "Pillow",
        "numpy",
        "librosa",
        "numba",
        "soxr",
        "tqdm",
        "python-telegram-bot",
        "python-dotenv",
        "httpx",
        "fastapi[standard]",
        "imageio-ffmpeg"
    )
    .add_local_file("make_bg.py", "/root/make_bg.py")
    .add_local_file("visualizer.py", "/root/visualizer.py")
    .add_local_file("main.py", "/root/main.py")
    .add_local_file("LEMONMILK-Bold.otf", "/root/LEMONMILK-Bold.otf")
    .add_local_file("assets/Milker.otf", "/root/assets/Milker.otf")
    .add_local_file("bot.py", "/root/bot.py")
)

@app.function(
    image=image,
    cpu=16.0,            # 16-Core parallel CPU rendering on Modal Cloud
    memory=4096,         # 4GB RAM
    scaledown_window=2,  # Shut down 16-core container 2s after render completes
    timeout=600,         # 10 minutes timeout
)
def render_visualizer_modal(image_bytes: bytes, audio_bytes: bytes, song_name: str, subtitle: str = "EDIT AUDIO", username: str = "SO9IC"):
    """
    Executes the Audio Visualizer Pipeline on Modal Cloud Infrastructure across 16 CPU Cores!
    """
    import os
    import sys
    sys.path.append("/root")
    from main import run_pipeline

    print("=" * 60)
    print("EXECUTING VISUALIZER PIPELINE ON 16-CORE MODAL CLOUD")
    print("=" * 60)
    
    temp_img_path = "/tmp/modal_input_img.jpg"
    temp_audio_path = "/tmp/modal_input_audio.mp3"
    temp_out_path = "/tmp/modal_output.mp4"
    temp_no_copyright_path = "/tmp/modal_no_copyright.jpg"

    with open(temp_img_path, "wb") as f:
        f.write(image_bytes)
    with open(temp_audio_path, "wb") as f:
        f.write(audio_bytes)

    out_video, final_bg_file, no_copyright_file, stats = run_pipeline(
        image_path=temp_img_path,
        audio_path=temp_audio_path,
        song_name=song_name,
        subtitle=subtitle,
        username=username,
        output_video=temp_out_path,
        no_copyright_path=temp_no_copyright_path
    )

    with open(temp_out_path, "rb") as f:
        video_bytes = f.read()

    with open(final_bg_file, "rb") as f:
        final_bg_bytes = f.read()

    with open(no_copyright_file, "rb") as f:
        no_copyright_bg_bytes = f.read()

    for p in [temp_img_path, temp_audio_path, temp_out_path, final_bg_file, no_copyright_file]:
        if os.path.exists(p):
            os.remove(p)

    return video_bytes, final_bg_bytes, no_copyright_bg_bytes, stats

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("visualizer-secrets")],
    scaledown_window=2,
    timeout=600
)
def process_and_send_modal(chat_id: int, image_bytes: bytes, audio_bytes: bytes, song_name: str, subtitle: str, username: str):
    """
    Autonomous Modal Background Task.
    Executes 16-core video rendering and sends 2 Background Images + final .mp4 video & benchmark report directly to Telegram!
    """
    import os
    import sys
    import traceback
    import asyncio
    sys.path.append("/root")
    from telegram import Bot
    import bot

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    print("=" * 60)
    print(f"MODAL BACKGROUND TASK STARTED for Chat ID {chat_id}")
    print("=" * 60)

    async def _send_status():
        try:
            async with Bot(token=bot_token) as tg_bot:
                await tg_bot.send_message(
                    chat_id=chat_id,
                    text="⚡ <b>Executing 16-Core Parallel Cloud Rendering...</b>\nPlease wait ~10 seconds...",
                    parse_mode="HTML"
                )
                print("[LOG] Successfully updated status message in Telegram.")
        except Exception as e:
            print(f"[LOG ERROR] Updating Telegram status message: {e}")
            traceback.print_exc()

    asyncio.run(_send_status())

    print("[LOG] Triggering 16-Core Parallel Cloud Rendering function...")
    video_bytes, final_bg_bytes, no_copyright_bg_bytes, stats = render_visualizer_modal.remote(
        image_bytes=image_bytes,
        audio_bytes=audio_bytes,
        song_name=song_name,
        subtitle=subtitle,
        username=username
    )
    print(f"[LOG] Rendering complete! Received {len(video_bytes)} bytes MP4, {len(final_bg_bytes)} bytes Final BG, {len(no_copyright_bg_bytes)} bytes No Copyright BG.")

    temp_out_path = f"/tmp/modal_out_{chat_id}.mp4"
    temp_final_bg_path = f"/tmp/modal_final_bg_{chat_id}.jpg"
    temp_no_copyright_path = f"/tmp/modal_nc_bg_{chat_id}.jpg"

    with open(temp_out_path, "wb") as f:
        f.write(video_bytes)
    with open(temp_final_bg_path, "wb") as f:
        f.write(final_bg_bytes)
    with open(temp_no_copyright_path, "wb") as f:
        f.write(no_copyright_bg_bytes)

    async def _send_all_media():
        try:
            async with Bot(token=bot_token) as tg_bot:
                # 1. Send Final Edit Background Photo
                print(f"[LOG] Uploading Photo 1: Final Edit Background to Telegram Chat ID {chat_id}...")
                with open(temp_final_bg_path, "rb") as f1:
                    await tg_bot.send_photo(
                        chat_id=chat_id,
                        photo=f1,
                        caption=f"🖼️ <b>Final Background Edit</b>\n🎵 <i>{song_name}</i> • {subtitle}",
                        parse_mode="HTML"
                    )
                print("[LOG SUCCESS] Photo 1 delivered!")

                # 2. Send Red NO COPYRIGHT Thumbnail Background Photo
                print(f"[LOG] Uploading Photo 2: Red NO COPYRIGHT Thumbnail Background to Telegram Chat ID {chat_id}...")
                with open(temp_no_copyright_path, "rb") as f2:
                    await tg_bot.send_photo(
                        chat_id=chat_id,
                        photo=f2,
                        caption="🚩 <b>No Copyright Thumbnail Background</b>\n✨ Clean edit with top-right diagonal badge",
                        parse_mode="HTML"
                    )
                print("[LOG SUCCESS] Photo 2 delivered!")

                # 3. Send 1080p 60 FPS Audio Visualizer Video
                print(f"[LOG] Uploading Video to Telegram Chat ID {chat_id}...")
                with open(temp_out_path, "rb") as vf:
                    await tg_bot.send_video(
                        chat_id=chat_id,
                        video=vf,
                        caption=(
                            f"🎬 <b>Audio Visualizer Ready!</b>\n\n"
                            f"🎵 <b>Song:</b> {song_name}\n"
                            f"✨ <b>Subtitle:</b> {subtitle}\n"
                            f"👤 <b>Creator:</b> {username}\n"
                            f"⚡ 60 FPS • 1080p • Peak Audio Reactive"
                        ),
                        parse_mode="HTML",
                        supports_streaming=True
                    )
                print("[LOG SUCCESS] Video delivered!")

                # 4. Send detailed performance benchmark report
                benchmark_msg = bot.format_benchmark_report({"song": song_name}, stats)
                print("[LOG] Sending performance benchmark report to Telegram...")
                await tg_bot.send_message(
                    chat_id=chat_id,
                    text=benchmark_msg,
                    parse_mode="HTML"
                )
                print("[LOG SUCCESS] Performance benchmark report delivered!")

        except Exception as e:
            print(f"[CRITICAL ERROR] Failed sending media to Telegram: {e}")
            traceback.print_exc()

    asyncio.run(_send_all_media())

    # Clean up temp files
    for p in [temp_out_path, temp_final_bg_path, temp_no_copyright_path]:
        if os.path.exists(p):
            os.remove(p)
            print(f"[LOG CLEANUP] Removed temporary file: {p}")

    print("=" * 60)
    print("MODAL BACKGROUND TASK COMPLETED FINISHED")
    print("=" * 60)

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("visualizer-secrets")],
    scaledown_window=2
)
@modal.fastapi_endpoint(method="POST")
async def telegram_webhook(request_json: dict):
    """
    Serverless Telegram Webhook Endpoint on Modal.
    Receives incoming updates from Telegram, updates state machine, and spawns background render task.
    """
    import sys
    sys.path.append("/root")
    from telegram import Update
    from telegram.ext import ApplicationBuilder
    import bot

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return {"status": "error", "message": "Missing TELEGRAM_BOT_TOKEN"}

    # Inject Modal spawn handle into bot.py
    bot.MODAL_SPAWN_FUNC = process_and_send_modal

    # Initialize bot application
    tg_app = ApplicationBuilder().token(bot_token).build()
    
    # Register handlers from bot.py
    tg_app.add_handler(bot.CommandHandler("start", bot.start_command))
    tg_app.add_handler(bot.CommandHandler("status", bot.status_command))
    tg_app.add_handler(bot.CommandHandler("reset", bot.reset_command))
    tg_app.add_handler(bot.CommandHandler("cancel", bot.reset_command))
    tg_app.add_handler(bot.MessageHandler(bot.filters.ALL & ~bot.filters.COMMAND, bot.handle_message))

    await tg_app.initialize()
    
    # Process update
    update = Update.de_json(data=request_json, bot=tg_app.bot)
    if update:
        await tg_app.process_update(update)

    return {"status": "ok"}

@app.local_entrypoint()
def set_webhook():
    """Local entrypoint to register deployed Modal Production Webhook URL with Telegram API."""
    import httpx
    from dotenv import load_dotenv
    load_dotenv()
    
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "8988427246:AAGE0zXawGS5Oc6Jx14ZGQXWrKeWAXc5cKk")
    webhook_url = "https://dekamukul013--audio-visualizer-pipeline-telegram-webhook.modal.run"
    
    print("=" * 60)
    print(f"Production Modal Webhook URL: {webhook_url}")
    print("Registering Webhook URL with Telegram API...")
    
    resp = httpx.post(
        f"https://api.telegram.org/bot{bot_token}/setWebhook",
        data={"url": webhook_url}
    )
    print(f"Telegram setWebhook Response: {resp.json()}")
    print("=" * 60)

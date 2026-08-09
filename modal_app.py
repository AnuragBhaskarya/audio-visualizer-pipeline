import os
import modal

# Define Modal App Name
app = modal.App("audio-visualizer-pipeline")

# Define Modal Cloud Image with FFmpeg and dependencies
image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("ffmpeg", "libsndfile1")
    .pip_install(
        "opencv-python-headless",
        "Pillow",
        "numpy",
        "librosa",
        "tqdm",
        "python-telegram-bot",
        "python-dotenv",
        "httpx",
        "fastapi[standard]"
    )
    .add_local_file("make_bg.py", "/root/make_bg.py")
    .add_local_file("visualizer.py", "/root/visualizer.py")
    .add_local_file("main.py", "/root/main.py")
    .add_local_file("LEMONMILK-Bold.otf", "/root/LEMONMILK-Bold.otf")
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

    with open(temp_img_path, "wb") as f:
        f.write(image_bytes)
    with open(temp_audio_path, "wb") as f:
        f.write(audio_bytes)

    output_path, stats = run_pipeline(
        image_path=temp_img_path,
        audio_path=temp_audio_path,
        song_name=song_name,
        subtitle=subtitle,
        username=username,
        output_video=temp_out_path
    )

    with open(temp_out_path, "rb") as f:
        video_bytes = f.read()

    for p in [temp_img_path, temp_audio_path, temp_out_path]:
        if os.path.exists(p):
            os.remove(p)

    return video_bytes, stats

@app.function(
    image=image,
    secrets=[modal.Secret.from_name("visualizer-secrets")],
    scaledown_window=2
)
@modal.fastapi_endpoint(method="POST")
async def telegram_webhook(request_json: dict):
    """
    Serverless Telegram Webhook Endpoint on Modal.
    Receives incoming updates from Telegram, processes state machine, and triggers 16-core video rendering.
    """
    import sys
    sys.path.append("/root")
    from telegram import Update
    from telegram.ext import ApplicationBuilder
    import bot

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return {"status": "error", "message": "Missing TELEGRAM_BOT_TOKEN"}

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
    # Target deployed production URL
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

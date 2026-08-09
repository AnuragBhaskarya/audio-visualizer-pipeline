# 🎵 Audio Visualizer Pipeline & Serverless Telegram Bot

A high-performance, GPU-grade **1080p 60 FPS Audio Visualizer Engine** and **Serverless Telegram Bot** built with Python, OpenCV, Librosa, FFmpeg, Pillow, and Modal Cloud Infrastructure.

![Python](https://img.shields.io/badge/Python-3.10-blue.svg)
![FFmpeg](https://img.shields.io/badge/FFmpeg-Fast%20Encode-green.svg)
![Modal](https://img.shields.io/badge/Modal-16--Core%20Serverless-purple.svg)
![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-blue.svg)

---

## ✨ Features

- **🎨 Dynamic 16:9 Background Engine (`make_bg.py`)**:
  - **Dynamic Title Font Scaling:** Automatically measures string pixel width and scales font down if text is long, preventing line-breaks or frame clipping.
  - **Locked Baseline Alignment:** Anchors main title at bottom edge (`anchor="mb"`), keeping vertical space above the horizontal line 100% constant across all text lengths.
  - **Color Correction (CC):** UnsharpMask detail sharpening, negative exposure black point offset, +48% contrast boost, saturation boost, and radial vignette.
  - **Full-Frame Inward Pinch Filter:** Applies 360° inward pinch distortion with a mathematically verified NumPy mask crop that eliminates 100% of outer mirrored tile reflections.
  - **Hard Light Graphics:** Blends tapered horizontal separator line and bottom username using 128-gray Hard Light composite maps.

- **⚡ High-Speed 60 FPS Visualizer (`visualizer.py`)**:
  - **Floating Cross Particle System:** Pure-white sharp cross polygons with 90° cap angles, dynamic thickness scaling, and depth-based additive opacity.
  - **Beat-Reactive Opacity Impulse:** Crosses sit at 40% opacity on idle, spike to 100% on bass onset beats, and decay smoothly over 0.6s slow release.
  - **0px Padding Audio Waves:** Audio-reactive horn bars anchored to absolute 0px top and bottom edges.
  - **True Alpha Fade-In & Black Fade-Out:** 2.5s transparent additive overlay fade-in for visualizer elements, and 2.5s black fade-out for the full frame at video conclusion.
  - **Zero-IPC Multi-Core Parallelism:** Divides video frames across CPU cores for maximum speed and merges audio in a single pass without redundant re-encoding.

- **🤖 Order-Agnostic Telegram Bot (`bot.py`)**:
  - **Strict Security:** Locked to authorized `ALLOWED_CHAT_ID`.
  - **Universal MIME Inspector:** Supports raw photos, audio files, voice notes, and uncompressed documents (`.webp`, `.png`, `.jpg`, `.mp3`, `.wav`, `.flac`, etc.).
  - **Order Agnostic:** Send Image $\to$ Audio $\to$ Text, or Text $\to$ Audio $\to$ Image in any order.
  - **Detailed Performance Report:** Sends a benchmark report message detailing execution times for every sub-phase (crop, CC, typography, pinch, FFT, render, concat).

- **☁️ Modal Cloud Deployment (`modal_app.py`)**:
  - Deployable as a serverless 16-Core CPU app on Modal Cloud.
  - Features `scaledown_window=2` for instant scale-down and zero idle costs.
  - Implements Modal Serverless FastAPI Webhook endpoint with automated Telegram `setWebhook` registration.

---

## 🚀 Quick Start (Local Execution)

### 1. Installation
```bash
git clone https://github.com/AnuragBhaskarya/audio-visualizer-pipeline.git
cd audio-visualizer-pipeline
pip install -r requirements.txt
```

### 2. Run Pipeline CLI
```bash
python3 main.py --image "assets/sample.webp" \
                --audio "assets/track.mp3" \
                --song "YOUR SONG TITLE" \
                --out "output.mp4"
```

---

## 🤖 Telegram Bot Configuration

Create a `.env` file in the project directory:
```env
TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
ALLOWED_CHAT_ID="YOUR_NUMERIC_CHAT_ID"
```

Run the bot locally:
```bash
python3 bot.py
```

---

## ☁️ Deploy to Modal Cloud

### 1. Create Modal Secret
```bash
modal secret create visualizer-secrets TELEGRAM_BOT_TOKEN="YOUR_TOKEN" ALLOWED_CHAT_ID="YOUR_CHAT_ID"
```

### 2. Deploy App to Modal
```bash
modal deploy modal_app.py
```

### 3. Register Webhook
```bash
modal run modal_app.py::set_webhook
```

---

## 📊 Benchmark Report Example

```text
📊 PIPELINE PERFORMANCE REPORT
───────────────────────────────
⏱️ Total Execution Time: 48.25s

🖼️ Background Generation: 0.46s
  • 16:9 Crop & Resample: 0.084s
  • Color Correction & CC: 0.121s
  • Typography & Shadow: 0.093s
  • Pinch Warp & Crop: 0.142s
  • Username Layer: 0.021s

🎬 Audio Visualizer Video: 47.79s
  • Librosa FFT & Beats: 0.723s
  • Audio Bars Precomp: 0.148s
  • Motion & Crosses: 0.082s
  • 4-Core Parallel Render: 46.21s
  • Concat & Audio Merge: 0.628s

⚡ Specs: 4261 frames • 60 FPS • 1080p
```

---

## 📜 License
MIT License. Free for personal and commercial use.

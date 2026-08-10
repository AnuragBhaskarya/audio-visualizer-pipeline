import time
import subprocess
import numpy as np
import librosa
from scipy.signal import find_peaks

audio_path = "assets/na na na na - j star [edit audio].mp3"

# 1. FFmpeg Load
t0 = time.time()
cmd = [
    "ffmpeg", "-i", audio_path,
    "-f", "s16le", "-ac", "1", "-ar", "22050", "-"
]
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
raw_pcm, _ = proc.communicate()
y = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
sr = 22050
t_load = time.time() - t0

# 2. Onset Envelope
t0 = time.time()
S = librosa.feature.melspectrogram(y=y, sr=sr, fmax=250)
onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(S, ref=np.max), sr=sr)
t_onset = time.time() - t0

# 3. Librosa beat_track
t0 = time.time()
tempo_lib, beats_lib = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
beat_times_lib = librosa.frames_to_time(beats_lib, sr=sr)
t_beat_lib = time.time() - t0

# 4. Scipy Peak Detection
t0 = time.time()
hop_length = 512
fps_onset = sr / hop_length
distance = int(fps_onset * (60.0 / 180.0)) # Max 180 BPM distance limit
peaks, _ = find_peaks(onset_env, distance=distance, height=np.mean(onset_env))
beat_times_scipy = librosa.frames_to_time(peaks, sr=sr)
t_beat_scipy = time.time() - t0

print(f"FFmpeg Load Time:     {t_load:.4f}s")
print(f"Onset Env Time:       {t_onset:.4f}s")
print(f"Librosa Beat Track:   {t_beat_lib:.4f}s ({len(beat_times_lib)} beats)")
print(f"Scipy Find Peaks:     {t_beat_scipy:.4f}s ({len(beat_times_scipy)} beats)")
print(f"Scipy Speedup:        {t_beat_lib / max(t_beat_scipy, 1e-6):.1f}x Faster!")

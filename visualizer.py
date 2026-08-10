import os
import sys
import math
import time
import subprocess
import numpy as np
import cv2
from PIL import Image
import librosa
import tqdm
from multiprocessing import Pool, cpu_count
import imageio_ffmpeg as im_ffmpeg

# Global worker variables
_worker_img = None
_worker_intensities = None
_worker_wiggles = None
_worker_bars = None
_worker_x_coords = None
_worker_params = None
_worker_crosses_data = None
_worker_matrices = None
_ffmpeg_exe = None

def init_worker(img, intensities, wiggles, bars, x_coords, params, crosses_data, ffmpeg_exe):
    """Initializes global memory structures once per worker process to minimize IPC overhead."""
    global _worker_img, _worker_intensities, _worker_wiggles, _worker_bars, _worker_x_coords, _worker_params, _worker_crosses_data, _worker_matrices, _ffmpeg_exe
    _worker_img = img
    _worker_intensities = intensities
    _worker_wiggles = wiggles
    _worker_bars = bars
    _worker_x_coords = x_coords
    _worker_params = params
    _worker_crosses_data = crosses_data
    _ffmpeg_exe = ffmpeg_exe
    
    _worker_matrices = {
        'M_r': np.float32([[1, 0, 0], [0, 1, 0]]),
        'M_b': np.float32([[1, 0, 0], [0, 1, 0]]),
        'M_center': (params['target_w'] / 2, params['target_h'] / 2)
    }

def render_chunk(args):
    """Worker function that renders a specific range of frames directly into its own MP4."""
    chunk_id, start_frame, end_frame = args
    target_w = _worker_params['target_w']
    target_h = _worker_params['target_h']
    fps = _worker_params['fps']
    chunk_filename = f"temp_chunk_{chunk_id}.mp4"
    
    command = [
        _ffmpeg_exe,
        '-y',
        '-f', 'rawvideo',
        '-vcodec', 'rawvideo',
        '-s', f'{target_w}x{target_h}',
        '-pix_fmt', 'rgb24',
        '-r', str(fps),
        '-i', '-', 
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-tune', 'zerolatency',
        '-crf', '30',
        '-pix_fmt', 'yuv420p',
        '-threads', '1', 
        chunk_filename
    ]
    
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    
    pts_bottom = np.zeros((200, 1, 2), dtype=np.int32)
    pts_top = np.zeros((200, 1, 2), dtype=np.int32)
    pts_bottom[:, 0, 0] = _worker_x_coords
    pts_top[:, 0, 0] = _worker_x_coords
    
    frame = np.empty_like(_worker_img)
    temp_frame = np.empty_like(_worker_img)
    overlay = np.empty_like(_worker_img)
    small_overlay = np.empty((target_h // 8, target_w // 8, 3), dtype=np.uint8)
    r_shifted = np.empty((target_h, target_w), dtype=np.uint8)
    b_shifted = np.empty((target_h, target_w), dtype=np.uint8)
    
    total_frames = _worker_bars.shape[0]
    total_duration = total_frames / fps

    for frame_idx in range(start_frame, end_frame):
        t = frame_idx / fps
        
        # 2.5s Fade-In for visualizer elements (bars & crosses)
        elem_fade_in = min(1.0, t / 2.5)
        
        # 2.5s Black Fade-Out for the entire video at the end
        remaining_time = total_duration - t
        whole_fade_out = max(0.0, min(1.0, remaining_time / 2.5))
        
        np.copyto(frame, _worker_img)
        intensity = _worker_intensities[frame_idx]
        
        wiggle_x, wiggle_y, wiggle_angle, wiggle_scale, shake_osc = _worker_wiggles[frame_idx]
        
        scale = wiggle_scale
        angle = wiggle_angle
        dy = wiggle_y
        dx = wiggle_x
        
        if intensity > 0.01:
            max_shake_y = 120 
            dy += int(max_shake_y * intensity * shake_osc)
            scale -= (0.05 * intensity)
            
        M_center = _worker_matrices['M_center']
        M = cv2.getRotationMatrix2D(M_center, angle, scale)
        M[0, 2] += dx
        M[1, 2] += dy
        
        cv2.warpAffine(frame, M, (target_w, target_h), dst=temp_frame, borderMode=cv2.BORDER_REFLECT)
        np.copyto(frame, temp_frame)
        
        if intensity > 0.01:
            blur_amount = int(30 * intensity)
            if blur_amount > 0:
                cv2.blur(frame, (1, blur_amount), dst=frame)
        
        if intensity > 0:
            split_amount = int(15 * intensity)
            if split_amount > 0:
                r = frame[:, :, 0]
                g = frame[:, :, 1]
                b = frame[:, :, 2]
                
                np.copyto(r_shifted, r)
                r_shifted[:, :-split_amount] = r[:, split_amount:]
                
                np.copyto(b_shifted, b)
                b_shifted[:, split_amount:] = b[:, :-split_amount]
                
                cv2.merge((r_shifted, g, b_shifted), dst=frame)
        
        # --- DRAW FLOATING CROSSES ---
        cross_x, cross_start_y, cross_size, cross_speed, cross_rot_speed, cumulative_distance, cross_beat_pulse = _worker_crosses_data
        dist_t = cumulative_distance[frame_idx]
        pulse = cross_beat_pulse[frame_idx]
        
        opacity_scale = 0.40 + 0.60 * pulse
        
        overlay.fill(0)
        
        for c in range(len(cross_x)):
            cx = cross_x[c]
            cy = int(cross_start_y[c] - (cross_speed[c] * dist_t)) % target_h
            size = cross_size[c]
            angle = cross_rot_speed[c] * dist_t
            
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            
            line_thickness = int(5 + 5 * ((size - 10) / 12.0))
            
            base_opacity_val = int(80 + 175 * ((size - 10) / 12.0))
            opacity_val = int(base_opacity_val * opacity_scale * elem_fade_in)
            color = (opacity_val, opacity_val, opacity_val)
            
            L = float(size)
            W = line_thickness / 2.0
            
            dx1 = L * cos_a
            dy1 = L * sin_a
            dx2 = W * -sin_a
            dy2 = W * cos_a
            
            poly1 = np.array([
                [cx - dx1 - dx2, cy - dy1 - dy2],
                [cx + dx1 - dx2, cy + dy1 - dy2],
                [cx + dx1 + dx2, cy + dy1 + dy2],
                [cx - dx1 + dx2, cy - dy1 + dy2]
            ], dtype=np.int32)
            
            dx3 = W * cos_a
            dy3 = W * sin_a
            dx4 = L * -sin_a
            dy4 = L * cos_a
            
            poly2 = np.array([
                [cx - dx3 - dx4, cy - dy3 - dy4],
                [cx + dx3 - dx4, cy + dy3 - dy4],
                [cx + dx3 + dx4, cy + dy3 + dy4],
                [cx - dx3 + dx4, cy - dy3 + dy4]
            ], dtype=np.int32)
            
            cv2.fillPoly(overlay, [poly1], color, lineType=cv2.LINE_8)
            cv2.fillPoly(overlay, [poly2], color, lineType=cv2.LINE_8)
            
        cv2.add(frame, overlay, dst=frame)
            
        bars = _worker_bars[frame_idx, :]
        max_bar_h = target_h // 2
        h_vals = (bars * max_bar_h).astype(np.int32)
        
        baseline = 0
        pts_bottom[:, 0, 1] = target_h - baseline - h_vals
        pts_top[:, 0, 1] = baseline + h_vals
        
        pts_bottom_small = (pts_bottom / 8).astype(np.int32)
        pts_top_small = (pts_top / 8).astype(np.int32)
        
        small_overlay.fill(0)
        cv2.polylines(small_overlay, [pts_bottom_small, pts_top_small], isClosed=False, color=(255, 255, 255), thickness=2, lineType=cv2.LINE_AA)
        
        cv2.GaussianBlur(small_overlay, (21, 21), 0, dst=small_overlay)
        glow = cv2.resize(small_overlay, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        
        glow_weight = 0.6 * elem_fade_in
        cv2.addWeighted(glow, glow_weight, frame, 1.0, 0, dst=frame)
        
        overlay.fill(0)
        cv2.polylines(overlay, [pts_bottom, pts_top], isClosed=False, color=(255, 255, 255), thickness=8, lineType=cv2.LINE_AA)
        if elem_fade_in > 0:
            cv2.addWeighted(overlay, elem_fade_in, frame, 1.0, 0, dst=frame)

        if whole_fade_out < 1.0:
            cv2.convertScaleAbs(frame, alpha=whole_fade_out, beta=0, dst=frame)

        process.stdin.write(frame.tobytes())

    process.stdin.close()
    process.wait()
    return chunk_filename

def build_visualizer(audio_path, image_path, output_final_path="output_visualizer.mp4", target_w=1280, target_h=720, fps=60):
    t_vis_start = time.time()
    vis_stats = {}
    
    output_video_path = "temp_video.mp4"
    num_bars = 4
    
    print(f"Loading audio ({audio_path}) and image ({image_path})...")
    t0 = time.time()
    # High-speed FFmpeg PCM stream directly to RAM float32 array
    ffmpeg_exe = im_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe,
        "-y",
        "-i", audio_path,
        "-f", "s16le",
        "-ac", "1",
        "-ar", "22050",
        "-"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    raw_pcm, _ = proc.communicate()
    y = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    sr = 22050
    duration = len(y) / sr

    S = librosa.feature.melspectrogram(y=y, sr=sr, fmax=250)
    onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(S, ref=np.max), sr=sr)
    tempo_val = librosa.feature.rhythm.tempo(onset_envelope=onset_env, sr=sr)[0]
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, bpm=tempo_val, tightness=100)
    beat_times = librosa.frames_to_time(beats, sr=sr)
    vis_stats["audio_fft_beats"] = time.time() - t0
    
    t0 = time.time()
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not load image: {image_path}")
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w, _ = img.shape
    
    img_aspect = w / h
    target_aspect = target_w / target_h
    if img_aspect > target_aspect:
        new_w = int(h * target_aspect)
        offset = (w - new_w) // 2
        img = img[:, offset:offset+new_w]
    elif img_aspect < target_aspect:
        new_h = int(w / target_aspect)
        offset = (h - new_h) // 2
        img = img[offset:offset+new_h, :]
    img = cv2.resize(img, (target_w, target_h))
    
    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    stft_db = librosa.amplitude_to_db(stft, ref=np.max)
    stft_norm = np.clip((stft_db + 80) / 80.0, 0, 1)
    
    relevant_freqs = stft_norm[:stft_norm.shape[0]//4, :]
    
    bars = np.zeros((3, relevant_freqs.shape[1]))
    bars[0, :] = np.mean(relevant_freqs[1:6, :], axis=0)
    bars[1, :] = np.mean(relevant_freqs[6:95, :], axis=0)
    bars[2, :] = np.mean(relevant_freqs[95:256, :], axis=0)
    bars = bars ** 2
    
    smoothed_bars = np.zeros_like(bars)
    attack = 0.85
    release = 0.7 
    smoothed_bars[:, 0] = bars[:, 0]
    for t_idx in range(1, bars.shape[1]):
        for b in range(3):
            if bars[b, t_idx] > smoothed_bars[b, t_idx-1]:
                smoothed_bars[b, t_idx] = attack * bars[b, t_idx] + (1 - attack) * smoothed_bars[b, t_idx-1]
            else:
                smoothed_bars[b, t_idx] = release * bars[b, t_idx] + (1 - release) * smoothed_bars[b, t_idx-1]
                
    all_bars = smoothed_bars
    vis_stats["bars_precomp"] = time.time() - t0
    
    t0 = time.time()
    total_frames = int(math.ceil(duration * fps))
    
    intensities = np.zeros(total_frames)
    for i in range(total_frames):
        t_sec = i / fps
        for b in reversed(beat_times):
            if b <= t_sec:
                diff = t_sec - b
                if diff < 0.3:
                    intensities[i] = math.exp(-diff * 18)
                break
                
    t_arr = np.arange(total_frames) / fps
    wiggle_x_arr = np.sin(t_arr * 0.5) * 6 + np.sin(t_arr * 0.2) * 4
    wiggle_y_arr = np.cos(t_arr * 0.4) * 6 + np.cos(t_arr * 0.3) * 4
    wiggle_angle_arr = np.sin(t_arr * 0.2) * 0.6
    wiggle_scale_arr = 1.03 + np.sin(t_arr * 0.15) * 0.015
    shake_oscillation_arr = np.sin(t_arr * 110) * 0.6 + np.sin(t_arr * 150) * 0.4
    
    wiggles = np.column_stack((wiggle_x_arr, wiggle_y_arr, wiggle_angle_arr, wiggle_scale_arr, shake_oscillation_arr))
    
    num_crosses = 25
    np.random.seed(42)
    cross_x = np.random.randint(0, target_w, num_crosses)
    cross_start_y = np.random.randint(0, target_h, num_crosses)
    cross_size = np.random.randint(10, 22, num_crosses)
    cross_speed = np.random.uniform(50.0, 150.0, num_crosses)
    cross_rot_speed = np.random.uniform(-0.5, 0.5, num_crosses)
    
    dt = 1.0 / fps
    speed_boost_factor = 5.0
    frame_speed_mult = 1.0 + (intensities * speed_boost_factor)
    cumulative_distance = np.cumsum(frame_speed_mult * dt)
    
    cross_beat_pulse = np.zeros(total_frames)
    for i in range(total_frames):
        t_sec = i / fps
        for b in reversed(beat_times):
            if b <= t_sec:
                diff = t_sec - b
                if diff < 0.6:
                    cross_beat_pulse[i] = math.exp(-diff * 5.0)
                break
                
    crosses_data = (cross_x, cross_start_y, cross_size, cross_speed, cross_rot_speed, cumulative_distance, cross_beat_pulse)
    
    scaled_bars = np.zeros((3, total_frames))
    for i in range(total_frames):
        t_sec = i / fps
        frame_idx = int((t_sec * sr) / 512)
        if frame_idx >= all_bars.shape[1]:
            frame_idx = all_bars.shape[1] - 1
        scaled_bars[:, i] = all_bars[:, frame_idx]
        
    scaled_bars = scaled_bars ** 1.5
    scaled_bars[0] *= 2.0
    scaled_bars[1] *= 2.0
    scaled_bars[2] *= 3.0
    
    worker_x_coords = np.linspace(0, target_w, 200, dtype=np.int32)
    x_normalized = np.linspace(-1, 1, 200)

    smooth_bars = np.zeros((total_frames, 200), dtype=np.float32)
    
    left_centers = [-0.85, -0.65, -0.40]
    right_centers = [0.85, 0.65, 0.40]
    bump_widths = [0.08, 0.06, 0.04]
    
    bump_templates = []
    for b in range(3):
        lx = (x_normalized - left_centers[b]) / bump_widths[b]
        left_bump = np.exp(-0.5 * lx**2)
        rx = (x_normalized - right_centers[b]) / bump_widths[b]
        right_bump = np.exp(-0.5 * rx**2)
        bump_templates.append(left_bump + right_bump)
        
    for i in range(total_frames):
        frame_smooth = np.zeros(200, dtype=np.float32)
        for b in range(3):
            frame_smooth += bump_templates[b] * scaled_bars[b, i]
        smooth_bars[i, :] = frame_smooth
        
    vis_stats["anim_precomp"] = time.time() - t0
    
    params = {'target_w': target_w, 'target_h': target_h, 'fps': fps}
    ffmpeg_exe = im_ffmpeg.get_ffmpeg_exe()
    
    t0 = time.time()
    num_cores = cpu_count()
    chunk_size_frames = math.ceil(total_frames / num_cores)
    chunks = []
    for i in range(num_cores):
        start = i * chunk_size_frames
        end = min(start + chunk_size_frames, total_frames)
        if start < end:
            chunks.append((i, start, end))
            
    chunk_files = []
    with Pool(processes=num_cores, initializer=init_worker, initargs=(img, intensities, wiggles, smooth_bars, worker_x_coords, params, crosses_data, ffmpeg_exe)) as pool:
        for chunk_file in tqdm.tqdm(pool.imap_unordered(render_chunk, chunks), total=len(chunks), desc="Rendering Chunks"):
            chunk_files.append(chunk_file)
            
    chunk_files.sort(key=lambda x: int(x.split('_')[-1].split('.')[0]))
    vis_stats["chunk_rendering"] = time.time() - t0
    
    t0 = time.time()
    with open("concat.txt", "w") as f:
        for cf in chunk_files:
            f.write(f"file '{cf}'\n")
            
    concat_command = [
        ffmpeg_exe,
        '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', 'concat.txt',
        '-i', audio_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-shortest',
        output_final_path
    ]
    subprocess.run(concat_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    if os.path.exists("concat.txt"):
        os.remove("concat.txt")
    for cf in chunk_files:
        if os.path.exists(cf):
            os.remove(cf)
            
    vis_stats["concat_audio_merge"] = time.time() - t0
    vis_stats["total"] = time.time() - t_vis_start
    vis_stats["num_cores"] = num_cores
    vis_stats["total_frames"] = total_frames

    print("\n[BENCHMARK] Visualizer Video Breakdown:")
    print(f"  • Librosa FFT & Beats:     {vis_stats['audio_fft_beats']:.4f}s")
    print(f"  • Audio Bars Precomp:      {vis_stats['bars_precomp']:.4f}s")
    print(f"  • Motion & Cross Precomp:  {vis_stats['anim_precomp']:.4f}s")
    print(f"  • {num_cores}-Core Parallel Render: {vis_stats['chunk_rendering']:.4f}s")
    print(f"  • Concat & Audio Merge:    {vis_stats['concat_audio_merge']:.4f}s")
    print(f"  TOTAL VIDEO TIME:          {vis_stats['total']:.4f}s\n")
    
    return output_final_path, vis_stats

if __name__ == "__main__":
    audio_test = "assets/na na na na - j star [edit audio].mp3"
    img_test = "assets/36369664.webp"
    if os.path.exists(audio_test) and os.path.exists(img_test):
        build_visualizer(audio_test, img_test)

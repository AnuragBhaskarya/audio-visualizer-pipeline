import os
import sys
import time
import argparse
from make_bg import create_background
from visualizer import build_visualizer

def run_pipeline(image_path, audio_path, song_name, subtitle="EDIT AUDIO", username="SO9IC", output_video="output_final.mp4", no_copyright_path=None, job_id=None):
    """
    Executes the full Audio Visualizer Pipeline and returns output video, background images, and timing benchmark stats.
    Pass a unique job_id (e.g. chat_id) to isolate temp files for concurrent runs.
    """
    prefix = f"{job_id}_" if job_id else ""
    t_pipe_start = time.time()
    
    print("=" * 65)
    print("STARTING AUDIO VISUALIZER PIPELINE (WITH PROFILER BENCHMARKS)")
    print("=" * 65)
    print(f"Raw Image Path: {image_path}")
    print(f"Audio Path:     {audio_path}")
    print(f"Song Name:      {song_name}")
    print(f"Subtitle:       {subtitle}")
    print(f"Username:       {username}")
    print(f"Output Video:   {output_video}")
    if job_id:
        print(f"Job ID:         {job_id}")
    print("-" * 65)
    
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Input image not found: {image_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Input audio file not found: {audio_path}")
        
    temp_bg_path = f"{prefix}temp_generated_bg.jpg"
    if no_copyright_path is None:
        no_copyright_path = f"{prefix}temp_no_copyright_bg.jpg"
    
    try:
        # Step 1: Generate dynamic background variants
        print("\n[STEP 1/2] Generating background image variants...")
        _, _, bg_stats = create_background(
            input_path=image_path,
            song_name=song_name,
            subtitle=subtitle,
            username=username,
            output_path=temp_bg_path,
            no_copyright_output_path=no_copyright_path
        )
        
        # Step 2: Render audio visualizer video
        print("\n[STEP 2/2] Rendering visualizer video...")
        final_video_path, vis_stats = build_visualizer(
            audio_path=audio_path,
            image_path=temp_bg_path,
            output_final_path=output_video
        )
        
        t_pipe_total = time.time() - t_pipe_start
        
        pipeline_stats = {
            "total_pipeline_time": t_pipe_total,
            "bg": bg_stats,
            "visualizer": vis_stats
        }
        
        print("=" * 65)
        print("PIPELINE PERFORMANCE SUMMARY:")
        print(f"  • Total Pipeline Time:    {t_pipe_total:.2f}s")
        print(f"  • Background Gen Time:    {bg_stats['total']:.2f}s")
        print(f"  • Visualizer Render Time: {vis_stats['total']:.2f}s ({vis_stats['num_cores']} Cores)")
        print(f"  • Video Output File:      {output_video}")
        print("=" * 65)
        
        return output_video, temp_bg_path, no_copyright_path, pipeline_stats

    finally:
        # Clean up temp files
        for tmp in [temp_bg_path, no_copyright_path]:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Full Audio Visualizer Pipeline")
    parser.add_argument("--image", required=True, help="Path to raw image file (webp, jpg, png, etc.)")
    parser.add_argument("--audio", required=True, help="Path to audio file (mp3, wav, flac, etc.)")
    parser.add_argument("--song", required=True, help="Song title (dynamically scaled)")
    parser.add_argument("--sub", default="EDIT AUDIO", help="Subtitle text")
    parser.add_argument("--user", default="SO9IC", help="Username for bottom")
    parser.add_argument("--out", default="final_output.mp4", help="Output MP4 filename")
    
    args = parser.parse_args()
    
    run_pipeline(
        image_path=args.image,
        audio_path=args.audio,
        song_name=args.song,
        subtitle=args.sub,
        username=args.user,
        output_video=args.out
    )

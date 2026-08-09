import sys
import argparse
import cv2
import os
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance, ImageChops

def fit_to_16_9(img):
    """Crop the image to a 16:9 aspect ratio and resize to 1920x1080."""
    target_ratio = 16 / 9
    w, h = img.size
    img_ratio = w / h
    
    if img_ratio > target_ratio:
        new_w = int(h * target_ratio)
        offset = (w - new_w) // 2
        img = img.crop((offset, 0, offset + new_w, h))
    elif img_ratio < target_ratio:
        new_h = int(w / target_ratio)
        offset = (h - new_h) // 2
        img = img.crop((0, offset, w, offset + new_h))
        
    return img.resize((1920, 1080), Image.Resampling.LANCZOS)

def draw_red_no_copyright_banner(img, font_path):
    """
    Draws a vibrant red diagonal corner banner badge in the top-right corner with 'NO COPYRIGHT' text.
    Matches the exact design aesthetic of YouTube thumbnail badges.
    """
    img = img.convert("RGBA")
    w, h = img.size
    
    banner_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(banner_layer)
    
    banner_width = 620
    banner_height = 220
    
    p1 = (w - banner_width, 0)
    p2 = (w, 0)
    p3 = (w, banner_height)
    
    red_color = (211, 18, 18, 255)  # Vibrant Red #D31212
    draw.polygon([p1, p2, p3], fill=red_color)
    
    angle_deg = -np.degrees(np.arctan2(banner_height, banner_width))
    
    try:
        font_nc = ImageFont.truetype(font_path, 42)
    except IOError:
        font_nc = ImageFont.load_default()
        
    text = "NO COPYRIGHT"
    
    txt_img = Image.new("RGBA", (500, 100), (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_img)
    txt_draw.text((250, 50), text, font=font_nc, fill=(255, 255, 255, 255), anchor="mm")
    
    rotated_txt = txt_img.rotate(angle_deg, resample=Image.Resampling.BICUBIC, expand=True)
    
    banner_center_x = w - (banner_width / 3.0) + 20
    banner_center_y = (banner_height / 3.0) - 10
    
    rx, ry = rotated_txt.size
    paste_pos = (int(banner_center_x - rx / 2.0), int(banner_center_y - ry / 2.0))
    
    banner_layer.paste(rotated_txt, paste_pos, rotated_txt)
    
    img = Image.alpha_composite(img, banner_layer)
    return img.convert("RGB")

def draw_text_with_stroke(img, text, position, font, stroke_color="white", stroke_width=6, shadow_color="black", shadow_blur=25, shadow_intensity=2):
    """Draws hollow text (stroke ONLY, 100% transparent interior) with a soft blurred drop shadow behind the stroke."""
    img = img.convert("RGBA")
    x, y = position
    
    # 1. Soft blurred drop shadow behind the hollow stroke
    shadow_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    shadow_draw.text((x, y), text, font=font, fill=(0, 0, 0, 0), stroke_fill=shadow_color, stroke_width=stroke_width + 6, anchor="mm")
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))
    
    for _ in range(shadow_intensity):
        img = Image.alpha_composite(img, shadow_layer)
        
    # 2. Pure white hollow stroke (fill=(0, 0, 0, 0))
    txt_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(txt_layer)
    draw.text((x, y), text, font=font, fill=(0, 0, 0, 0), stroke_fill=stroke_color, stroke_width=stroke_width, anchor="mm")
    
    img = Image.alpha_composite(img, txt_layer)
    return img.convert("RGB")

def draw_text_with_blurred_shadow(img, text, position, font, text_color="white", shadow_color="black", shadow_offset=(8, 8), shadow_blur=10, is_hard_light=False, shadow_intensity=1, anchor="mm"):
    """Draws text with a blurred drop shadow. Supports Hard Light blend for the text body."""
    shadow_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    
    x, y = position
    shadow_x = x + shadow_offset[0]
    shadow_y = y + shadow_offset[1]
    
    shadow_draw.text((shadow_x, shadow_y), text, font=font, fill=shadow_color, anchor=anchor)
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))
    
    img = img.convert("RGBA")
    for _ in range(shadow_intensity):
        img.paste(shadow_layer, (0, 0), shadow_layer)
    img = img.convert("RGB")
    
    if is_hard_light:
        fg_img = Image.new('RGB', img.size, (128, 128, 128))
        text_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_layer)
        
        text_draw.text((x, y), text, font=font, fill=text_color, anchor=anchor)
        
        fg_img.paste(text_layer, (0, 0), text_layer)
        img = ImageChops.hard_light(img, fg_img)
    else:
        img = img.convert("RGBA")
        draw = ImageDraw.Draw(img)
        draw.text((x, y), text, font=font, fill=text_color, anchor=anchor)
        img = img.convert("RGB")
        
    return img

def draw_hard_light_line(img, center_pos, width=950, height=14):
    """Draws a horizontal line tapering to sharp points at both ends using Hard Light blend."""
    fg_img = Image.new('RGB', img.size, (128, 128, 128))
    line_layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(line_layer)
    
    cx, cy = center_pos
    hw = width // 2
    hh = height // 2
    
    points = [
        (cx - hw, cy),
        (cx, cy - hh),
        (cx + hw, cy),
        (cx, cy + hh)
    ]
    
    draw.polygon(points, fill=(255, 255, 255, 255))
    fg_img.paste(line_layer, (0, 0), line_layer)
    return ImageChops.hard_light(img, fg_img)

def apply_color_correction(img):
    """Applies high contrast, unsharp mask sharpening, dark exposure offset, and radial vignette."""
    img_sharp = img.filter(ImageFilter.UnsharpMask(radius=2, percent=140, threshold=3))
    
    arr = np.array(img_sharp, dtype=np.float32)
    exposure_scale = 0.75
    black_offset = 22.0
    arr = np.clip((arr * exposure_scale) - black_offset, 0, 255).astype(np.uint8)
    img_dark = Image.fromarray(arr)
    
    enhancer_contrast = ImageEnhance.Contrast(img_dark)
    img_cc = enhancer_contrast.enhance(1.48)
    
    enhancer_color = ImageEnhance.Color(img_cc)
    img_cc = enhancer_color.enhance(1.20)
    
    w, h = img_cc.size
    y, x = np.ogrid[:h, :w]
    center_y, center_x = h / 2, w / 2
    radius = np.sqrt((x - center_x)**2 + (y - center_y)**2)
    max_radius = np.sqrt(center_x**2 + center_y**2)
    norm_radius = radius / max_radius
    
    vignette = 1.0 - 0.50 * (norm_radius ** 2.2)
    vignette = np.clip(vignette, 0.30, 1.0)[..., np.newaxis]
    
    arr_cc = np.array(img_cc, dtype=np.float32) * vignette
    return Image.fromarray(np.clip(arr_cc, 0, 255).astype(np.uint8))

def apply_pinch_warp(img, amount=0.55):
    """Applies a full-frame 360° inward pinch warp and crops outer mirrored tiles to exact 16:9."""
    pil_in = img.convert("RGB")
    cv_img = cv2.cvtColor(np.array(pil_in), cv2.COLOR_RGB2BGR)

    h, w = cv_img.shape[:2]

    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    max_dist = np.sqrt(cx**2 + cy**2)

    ys, xs = np.indices((h, w), dtype=np.float32)
    dx = xs - cx
    dy = ys - cy
    d = np.sqrt(dx**2 + dy**2)
    norm_d = d / max_dist

    factor = 1.0 + amount * ((np.maximum(0.0, 1.0 - norm_d)) ** 1.5)

    src_x = cx + dx * factor
    src_y = cy + dy * factor

    map_x = src_x.astype(np.float32)
    map_y = src_y.astype(np.float32)

    pinched = cv2.remap(cv_img, map_x, map_y, interpolation=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT_101)

    is_original = (map_x >= 0) & (map_x <= w - 1) & (map_y >= 0) & (map_y <= h - 1)

    valid_cols = np.where(is_original.all(axis=0))[0]
    valid_rows = np.where(is_original.all(axis=1))[0]

    if len(valid_cols) > 0 and len(valid_rows) > 0:
        min_x, max_x = valid_cols[0], valid_cols[-1]
        min_y, max_y = valid_rows[0], valid_rows[-1]
        crop_x1 = max(min_x, w - 1 - max_x)
        crop_y1 = max(min_y, h - 1 - max_y)
    else:
        crop_x1 = int(w * 0.08)
        crop_y1 = int(h * 0.08)

    crop_w = w - 2 * crop_x1
    crop_h = h - 2 * crop_y1

    target_ratio = 16.0 / 9.0
    if crop_w / crop_h > target_ratio:
        crop_w = int(crop_h * target_ratio)
    else:
        crop_h = int(crop_w / target_ratio)

    x1 = cx - crop_w / 2.0
    y1 = cy - crop_h / 2.0

    x1_i, y1_i = int(round(x1)), int(round(y1))

    cropped = pinched[y1_i:y1_i + crop_h, x1_i:x1_i + crop_w]
    final_cv = cv2.resize(cropped, (1920, 1080), interpolation=cv2.INTER_LANCZOS4)

    final_rgb = cv2.cvtColor(final_cv, cv2.COLOR_BGR2RGB)
    return Image.fromarray(final_rgb)

def create_background(input_path, song_name, subtitle="EDIT AUDIO", username="SO9IC", output_path="final_bg.jpg", no_copyright_output_path="no_copyright_bg.jpg"):
    """Generates both Version 1 (Final BG) and Version 2 (Red NO COPYRIGHT Banner BG)."""
    t_start = time.time()
    bg_stats = {}
    
    print(f"Loading raw background: {input_path}")
    raw_img = Image.open(input_path)
    
    t0 = time.time()
    img = fit_to_16_9(raw_img)
    bg_stats["crop_scale"] = time.time() - t0
    
    t0 = time.time()
    img = apply_color_correction(img)
    bg_stats["color_correction"] = time.time() - t0
    
    t0 = time.time()
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        font_path = os.path.join(script_dir, "LEMONMILK-Bold.otf")
        if not os.path.exists(font_path):
            font_path = "/home/so9ic/coding/Cyber/LEMONMILK-Bold.otf"
        
        base_title_size = 175
        max_title_width = 1450
        
        temp_title_font = ImageFont.truetype(font_path, base_title_size)
        bbox = temp_title_font.getbbox(song_name.upper())
        title_w = bbox[2] - bbox[0]
        
        if title_w > max_title_width:
            dynamic_size = int(base_title_size * (max_title_width / title_w))
            font_title = ImageFont.truetype(font_path, max(dynamic_size, 40))
        else:
            font_title = temp_title_font
            
        font_sub = ImageFont.truetype(font_path, 85)
        font_user = ImageFont.truetype(font_path, 35)
    except IOError as e:
        print(f"Error loading fonts: {e}")
        font_path = None
        font_title = font_sub = font_user = ImageFont.load_default()
    
    w, h = img.size
    center_x = w // 2
    center_y = h // 2

    # --- VERSION 1: FINAL FULL TYPOGRAPHY BG ---
    img_v1 = img.copy()
    
    # 1. Main Song Title (Solid White with Drop Shadow)
    img_v1 = draw_text_with_blurred_shadow(
        img_v1, 
        song_name.upper(), 
        (center_x, center_y + 45), 
        font_title, 
        shadow_offset=(0, 0), 
        shadow_blur=45, 
        is_hard_light=False,
        shadow_intensity=3,
        anchor="mb"
    )
    
    # 2. Subtitle Text (Hollow Outlined Stroke + Blurred Drop Shadow)
    img_v1 = draw_text_with_stroke(
        img_v1, 
        subtitle.upper(), 
        (center_x, center_y + 135), 
        font_sub, 
        stroke_color="white",
        stroke_width=6,
        shadow_color="black",
        shadow_blur=25,
        shadow_intensity=2
    )
    
    # 3. Hard Light Tapered Separator Line (ON TOP of everything)
    img_v1 = draw_hard_light_line(img_v1, (center_x, center_y + 80), width=950, height=14)
    bg_stats["typography"] = time.time() - t0
    
    t0 = time.time()
    img_v1 = apply_pinch_warp(img_v1, amount=0.55)
    bg_stats["pinch_warp"] = time.time() - t0
    
    t0 = time.time()
    # 4. Username Text (LEMONMILK-Bold + Hard Light Blend + Shadow)
    img_v1 = draw_text_with_blurred_shadow(
        img_v1, 
        username.upper(), 
        (center_x, h - 50), 
        font_user, 
        shadow_offset=(4, 6), 
        shadow_blur=10,
        is_hard_light=True,
        shadow_intensity=2
    )
    bg_stats["username"] = time.time() - t0
    
    img_v1.save(output_path, quality=98)

    # --- VERSION 2: RED NO COPYRIGHT DIAGONAL BANNER BG ---
    img_v2 = img.copy()
    img_v2 = apply_pinch_warp(img_v2, amount=0.55)
    img_v2 = draw_red_no_copyright_banner(img_v2, font_path)
    img_v2.save(no_copyright_output_path, quality=98)
    
    bg_stats["total"] = time.time() - t_start
    
    print("\n[BENCHMARK] Background Generation Breakdown:")
    print(f"  • 16:9 Crop & Scale:      {bg_stats['crop_scale']:.4f}s")
    print(f"  • Color Correction & CC:  {bg_stats['color_correction']:.4f}s")
    print(f"  • Typography & Shadow:    {bg_stats['typography']:.4f}s")
    print(f"  • Pinch Warp & Crop:      {bg_stats['pinch_warp']:.4f}s")
    print(f"  • Username Layer:         {bg_stats['username']:.4f}s")
    print(f"  TOTAL BG TIME:            {bg_stats['total']:.4f}s\n")
    
    return output_path, no_copyright_output_path, bg_stats

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create 16:9 Audio Visualizer Background")
    parser.add_argument("--image", required=True, help="Path to raw background image")
    parser.add_argument("--song", default="BELIEVER", help="Song Name")
    parser.add_argument("--sub", default="EDIT AUDIO", help="Subtitle")
    parser.add_argument("--user", default="SO9IC", help="Username for bottom")
    parser.add_argument("--out", default="final_bg.jpg", help="Output filename")
    
    args = parser.parse_args()
    
    create_background(
        input_path=args.image,
        song_name=args.song,
        subtitle=args.sub,
        username=args.user,
        output_path=args.out
    )

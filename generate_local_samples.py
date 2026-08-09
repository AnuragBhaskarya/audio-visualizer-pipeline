import os
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from make_bg import fit_to_16_9, apply_color_correction, apply_pinch_warp, draw_text_with_blurred_shadow, draw_text_with_stroke, draw_hard_light_line

def render_sample_local(warp_intensity_px, out_filename, font_size=48):
    raw_path = "/home/so9ic/coding/Cyber/15042876.webp"
    raw_img = Image.open(raw_path)
    img = fit_to_16_9(raw_img)
    img = apply_color_correction(img)
    
    # 1. Base Edit Background (Typography + Username)
    font_path = "LEMONMILK-Bold.otf"
    font_title = ImageFont.truetype(font_path, 175)
    font_sub = ImageFont.truetype(font_path, 85)
    font_user = ImageFont.truetype(font_path, 35)
    
    w, h = img.size
    center_x, center_y = w // 2, h // 2
    
    img = draw_text_with_blurred_shadow(img, "MALA", (center_x, center_y + 45), font_title, shadow_offset=(0, 0), shadow_blur=45, is_hard_light=False, shadow_intensity=3, anchor="mb")
    img = draw_text_with_stroke(img, "EDIT AUDIO", (center_x, center_y + 135), font_sub, stroke_color="white", stroke_width=6, shadow_color="black", shadow_blur=25, shadow_intensity=2)
    img = draw_hard_light_line(img, (center_x, center_y + 80), width=950, height=14)
    img = apply_pinch_warp(img, amount=0.55)
    img = draw_text_with_blurred_shadow(img, "SO9IC", (center_x, h - 50), font_user, shadow_offset=(4, 6), shadow_blur=10, is_hard_light=True, shadow_intensity=2)
    
    # 2. Red Ribbon Layer
    img = img.convert("RGBA")
    banner_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(banner_layer)
    
    p_top_left = (w - 750, 0)
    p_top_right = (w, 0)
    p_bottom_right = (w, 136)
    p_bottom_left = (w - 680, 116)
    
    draw.polygon([p_top_left, p_top_right, p_bottom_right, p_bottom_left], fill=(211, 18, 18, 255))
    
    # 3. Text Layer with Milker.otf (Font Size 48 as before)
    milker_font_path = "assets/Milker.otf"
    font_nc = ImageFont.truetype(milker_font_path, font_size)
    text = "NO COPYRIGHT"
    
    bbox = font_nc.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    padding_w, padding_h = tw + 80, th + 60
    txt_canvas = Image.new("RGBA", (padding_w, padding_h), (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_canvas)
    txt_draw.text((padding_w / 2.0, padding_h / 2.0), text, font=font_nc, fill=(255, 255, 255, 255), anchor="mm")
    
    cv_txt = cv2.cvtColor(np.array(txt_canvas), cv2.COLOR_RGBA2BGRA)
    
    src_pts = np.float32([
        [0, 0],
        [padding_w, 0],
        [padding_w, padding_h],
        [0, padding_h]
    ])
    
    dst_pts = np.float32([
        [0, 0],
        [padding_w, 0],                                      # Top edge flat
        [padding_w, padding_h + warp_intensity_px],          # Bottom-Right stretches DOWNWARDS
        [0, padding_h]                                       # Bottom-Left flat
    ])
    
    M_perspective = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped_cv = cv2.warpPerspective(cv_txt, M_perspective, (padding_w, padding_h + warp_intensity_px + 10), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    
    warped_rgba = cv2.cvtColor(warped_cv, cv2.COLOR_BGRA2RGBA)
    warped_pil = Image.fromarray(warped_rgba)
    
    banner_center_x = w - 325
    banner_center_y = 54
    
    rw, rh = warped_pil.size
    paste_pos = (int(banner_center_x - rw / 2.0), int(banner_center_y - rh / 2.0))
    
    banner_layer.paste(warped_pil, paste_pos, warped_pil)
    
    final_img = Image.alpha_composite(img, banner_layer).convert("RGB")
    
    # Save directly in current working directory .
    final_img.save(out_filename, quality=98)
    print(f"Generated sample in current dir: {out_filename}")

if __name__ == "__main__":
    render_sample_local(warp_intensity_px=0, out_filename="sample1_size48_flat.jpg")
    render_sample_local(warp_intensity_px=8, out_filename="sample2_size48_subtle_warp.jpg")
    render_sample_local(warp_intensity_px=16, out_filename="sample3_size48_medium_warp.jpg")
    render_sample_local(warp_intensity_px=24, out_filename="sample4_size48_strong_warp.jpg")

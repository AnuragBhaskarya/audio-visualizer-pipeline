import os
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from make_bg import fit_to_16_9, apply_color_correction, apply_pinch_warp, draw_text_with_blurred_shadow, draw_text_with_stroke, draw_hard_light_line

def render_size_sample(font_size, out_filename):
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
    
    # 2. Red Ribbon Layer scaled appropriately to font size
    img = img.convert("RGBA")
    banner_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(banner_layer)
    
    # Dynamically scale ribbon box height and width based on font size
    box_height_right = int(120 + (font_size - 66) * 1.1)
    box_height_left = int(105 + (font_size - 66) * 1.0)
    box_length_top = int(750 + (font_size - 66) * 4.5)
    box_length_bot = int(680 + (font_size - 66) * 4.0)
    
    p_top_left = (w - box_length_top, 0)
    p_top_right = (w, 0)
    p_bottom_right = (w, box_height_right)
    p_bottom_left = (w - box_length_bot, box_height_left)
    
    draw.polygon([p_top_left, p_top_right, p_bottom_right, p_bottom_left], fill=(211, 18, 18, 255))
    
    # 3. Text Layer with Milker.otf (FLAT, No rotation, No warp)
    milker_font_path = "assets/Milker.otf"
    font_nc = ImageFont.truetype(milker_font_path, font_size)
    text = "NO COPYRIGHT"
    
    center_x_text = w - (box_length_bot / 2.0) + 15
    center_y_text = box_height_right / 2.0 - 2
    
    draw.text((center_x_text, center_y_text), text, font=font_nc, fill=(255, 255, 255, 255), anchor="mm")
    
    final_img = Image.alpha_composite(img, banner_layer).convert("RGB")
    final_img.save(out_filename, quality=98)
    print(f"Generated size sample: {out_filename} ({font_size}px)")

if __name__ == "__main__":
    render_size_sample(font_size=74, out_filename="size_74px_flat.jpg")
    render_size_sample(font_size=84, out_filename="size_84px_flat.jpg")
    render_size_sample(font_size=96, out_filename="size_96px_flat.jpg")
    render_size_sample(font_size=108, out_filename="size_108px_flat.jpg")

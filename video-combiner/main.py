import json
import tempfile
import os
import shutil
import zipfile
import logging
import sys
from typing import Optional

import numpy as np
from PIL import Image

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse

from moviepy.editor import (
    VideoClip, concatenate_videoclips, AudioFileClip, TextClip
)
from moviepy.audio.fx.all import audio_loop

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- CONFIGURATION ---
FONT_NAME = "Arial" 
FONT_SIZE = 70
COLOR_INACTIVE = 'white'
COLOR_ACTIVE = 'yellow'
STROKE_COLOR = 'black'
STROKE_WIDTH = 3
BOTTOM_MARGIN = 150 
FPS = 24  

# --- HELPER FUNCTIONS ---

def find_file_in_dir(filename, search_path):
    """Recursively searches for a filename in a directory."""
    for root, dirs, files in os.walk(search_path):
        if filename in files:
            return os.path.join(root, filename)
    return None


def create_motion_clip(img_path, duration, effect, zoom_amount, target_w, target_h, fps):
    """
    Extremely fast Native PIL image generator to replace heavy MoviePy transforms. 
    Handles bounding-box logic to pan and zoom instantly.
    """
    img = Image.open(img_path).convert("RGB")
    w, h = img.size
    target_ratio = target_w / target_h
    
    # Gracefully fallback Resampling algorithm based on Pillow version
    resample_filter = getattr(Image.Resampling, 'BILINEAR', Image.BILINEAR) if hasattr(Image, 'Resampling') else Image.BILINEAR

    def make_frame(t):
        progress = max(0.0, min(1.0, t / duration))
        
        if effect == "zoom_in":
            max_ch = h if w / h > target_ratio else int(w / target_ratio)
            max_cw = int(h * target_ratio) if w / h > target_ratio else w
                
            factor = 1 + (zoom_amount * progress)
            cw, ch = int(max_cw / factor), int(max_ch / factor)
            cx, cy = (w - cw) // 2, (h - ch) // 2
            
        elif effect == "zoom_out":
            max_ch = h if w / h > target_ratio else int(w / target_ratio)
            max_cw = int(h * target_ratio) if w / h > target_ratio else w
                
            factor = 1 + zoom_amount - (zoom_amount * progress)
            cw, ch = int(max_cw / factor), int(max_ch / factor)
            cx, cy = (w - cw) // 2, (h - ch) // 2
            
        elif effect == "pan_right":
            max_ch = h if w / h > target_ratio else int(w / target_ratio)
            max_cw = int(h * target_ratio) if w / h > target_ratio else w
                
            factor = 1 + zoom_amount
            cw, ch = int(max_cw / factor), int(max_ch / factor)
            
            cy = (h - ch) // 2
            cx = int((w - cw) * progress)
            
        else: # Static default
            ch = h if w / h > target_ratio else int(w / target_ratio)
            cw = int(h * target_ratio) if w / h > target_ratio else w
            cx, cy = (w - cw) // 2, (h - ch) // 2
            
        # Natively extract & format the box
        crop_box = (cx, cy, cx + cw, cy + ch)
        cropped = img.crop(crop_box)
        resized = cropped.resize((target_w, target_h), resample_filter)
        return np.array(resized)

    return VideoClip(make_frame, duration=duration).set_fps(fps)


def generate_subtitle_states(word_data, video_w, video_h):
    """
    Groups words into lines and calculates state-arrays per subtitle.
    Greatly eliminates `CompositeVideoClip` array-blending overhead per frame.
    """
    if not word_data:
        return[]

    lines, current_line = [],[]
    current_width = 0
    max_width = video_w - 100 

    for item in word_data:
        word_txt = item['word']
        try:
            # Capture properties into memory just once per word.
            tc_base = TextClip(word_txt, font=FONT_NAME, fontsize=FONT_SIZE, color=COLOR_INACTIVE, stroke_color=STROKE_COLOR, stroke_width=STROKE_WIDTH)
            item['img_base'] = tc_base.get_frame(0)
            item['mask_base'] = tc_base.mask.get_frame(0) if tc_base.mask is not None else np.ones(item['img_base'].shape[:2])
            item['width'] = tc_base.w
            item['height'] = tc_base.h
            tc_base.close()

            tc_active = TextClip(word_txt, font=FONT_NAME, fontsize=FONT_SIZE, color=COLOR_ACTIVE, stroke_color=STROKE_COLOR, stroke_width=STROKE_WIDTH)
            item['img_active'] = tc_active.get_frame(0)
            item['mask_active'] = tc_active.mask.get_frame(0) if tc_active.mask is not None else np.ones(item['img_active'].shape[:2])
            tc_active.close()
        except Exception as e:
            logger.error(f"Error generating text clip for '{word_txt}': {e}")
            continue

        if current_width + item['width'] > max_width and current_line:
            lines.append(current_line)
            current_line, current_width =[], 0
        
        current_line.append(item)
        current_width += item['width'] + 20 

    if current_line:
        lines.append(current_line)

    states =[]
    for line in lines:
        if not line: continue
        line_start, line_end = line[0]['start'], line[-1]['end']
        
        total_line_width = sum(item['width'] for item in line) + (20 * (len(line) - 1))
        start_x = int((video_w - total_line_width) / 2)
        y_pos = int(video_h - BOTTOM_MARGIN - line[0]['height'])
        
        base_img_rgba = np.zeros((video_h, video_w, 4), dtype=np.uint8)
        current_x = start_x
        
        # Draw inactive layer layout cache
        for item in line:
            item['x'], item['y'] = current_x, y_pos
            rgb = item['img_base']
            alpha = (item['mask_base'] * 255).astype(np.uint8)
            h, w = rgb.shape[:2]
            
            end_y, end_x = min(y_pos + h, video_h), min(current_x + w, video_w)
            draw_h, draw_w = end_y - y_pos, end_x - current_x
            
            if draw_h > 0 and draw_w > 0:
                base_img_rgba[y_pos:end_y, current_x:end_x, :3] = rgb[:draw_h, :draw_w]
                base_img_rgba[y_pos:end_y, current_x:end_x, 3] = alpha[:draw_h, :draw_w]
            current_x += w + 20
            
        last_time = line_start
        # Record temporal states to swap highlights instantly.
        for item in line:
            if item['start'] > last_time:
                states.append({'start': last_time, 'end': item['start'], 'base_img': base_img_rgba, 'active_item': None})
                
            states.append({'start': item['start'], 'end': item['end'], 'base_img': base_img_rgba, 'active_item': item})
            last_time = item['end']
            
        if last_time < line_end:
            states.append({'start': last_time, 'end': line_end, 'base_img': base_img_rgba, 'active_item': None})

    return states


# --- API ENDPOINT ---

@app.post("/generate")
async def generate_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),         
    audio_file: Optional[UploadFile] = File(None), 
    timeline: str = Form(...),    
    subtitles: str = Form(None),  
    filename: str = Form(...),
    target_width: int = Form(720),
    target_height: int = Form(1280)
):
    logger.info("=== NEW REQUEST RECEIVED ===")
    logger.info(f"Target Filename: {filename}")
    
    temp_dir = tempfile.mkdtemp()
    
    try:
        timeline_data = json.loads(timeline)
        subtitle_data = json.loads(subtitles) if subtitles else[]
        
        # 1. Save and Extract ZIP
        zip_path = os.path.join(temp_dir, "upload.zip")
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # 2. Process Image Clips Native Generator
        processed_clips = []
        for index, item in enumerate(timeline_data):
            img_name = item["filename"]
            duration = float(item["duration"])
            effect = item.get("effect", "zoom_in")
            zoom_val = float(item.get("zoom_amount", 0.15))
            
            path = find_file_in_dir(img_name, temp_dir)
            if not path:
                logger.error(f"Image NOT FOUND: {img_name}. Skipping.")
                continue

            logger.info(f"Processing Clip {index+1}: {img_name} | Dur: {duration}s | Effect: {effect}")
            
            # Using the fast PIL image generator
            clip = create_motion_clip(path, duration, effect, zoom_val, target_width, target_height, FPS)
            processed_clips.append(clip)
            
        if not processed_clips:
            return {"error": "No images could be processed. Check filenames in JSON vs ZIP."}

        # 3. Concatenate Slideshow (`method="chain"` is wildly faster here because sizes are exact)
        base_video = concatenate_videoclips(processed_clips, method="chain")
        total_duration = base_video.duration

        # 4. Generate Subtitles Overlay Array Logic
        if subtitle_data:
            logger.info("Generating subtitle overlays...")
            states = generate_subtitle_states(subtitle_data, target_width, target_height)
            
            if states:
                def add_subtitles_filter(get_frame, t):
                    frame = get_frame(t)
                    for state in states:
                        if state['start'] <= t <= state['end']:
                            overlay = state['base_img'].copy()
                            act = state['active_item']
                            if act:
                                x, y = act['x'], act['y']
                                rgb = act['img_active']
                                alpha = (act['mask_active'] * 255).astype(np.uint8)
                                h, w = rgb.shape[:2]
                                
                                end_y, end_x = min(y + h, target_height), min(x + w, target_width)
                                draw_h, draw_w = end_y - y, end_x - x
                                if draw_h > 0 and draw_w > 0:
                                    overlay[y:end_y, x:end_x, :3] = rgb[:draw_h, :draw_w]
                                    overlay[y:end_y, x:end_x, 3] = alpha[:draw_h, :draw_w]
                            
                            # C++ Vectorized NumPy array blending 
                            alpha_layer = overlay[:, :, 3:4] / 255.0
                            frame = frame * (1 - alpha_layer) + overlay[:, :, :3] * alpha_layer
                            return frame.astype(np.uint8)
                    return frame

                base_video = base_video.fl(add_subtitles_filter)

        # 5. Fast Render Layer Lock
        final_clip = base_video.set_duration(total_duration).set_fps(FPS)

        # 6. Audio Integration
        if audio_file:
            audio_path = os.path.join(temp_dir, audio_file.filename)
            with open(audio_path, "wb") as buffer:
                shutil.copyfileobj(audio_file.file, buffer)
            
            audio = AudioFileClip(audio_path)
            if audio.duration < total_duration:
                audio = audio_loop(audio, duration=total_duration)
            else:
                audio = audio.set_duration(total_duration)
            
            final_clip = final_clip.set_audio(audio)

        # 7. Render (Threads expanded to consume all 8 cores)
        output_path = os.path.join(temp_dir, f"{filename}.mp4")
        logger.info(f"Rendering to {output_path}...")
        
        final_clip.write_videofile(
            output_path, 
            fps=FPS, 
            codec="libx264", 
            audio_codec="aac",
            threads=8, 
            preset="ultrafast",
            logger="bar" 
        )

        background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)
        return FileResponse(
            output_path, 
            media_type="video/mp4", 
            filename=f"{filename}.mp4"
        )

    except Exception as e:
        logger.exception("Error during generation")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return {"status": "error", "message": str(e)}

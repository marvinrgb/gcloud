import json
import tempfile
import os
import shutil
import zipfile
import logging
import sys
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import FileResponse

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

from moviepy.editor import (
    ImageClip, concatenate_videoclips, AudioFileClip, 
    CompositeVideoClip, TextClip
)
from moviepy.audio.fx.all import audio_loop

app = FastAPI()

# --- CONFIGURATION ---
FONT_NAME = "Arial" 
FONT_SIZE = 70
COLOR_INACTIVE = 'white'
COLOR_ACTIVE = 'yellow'
STROKE_COLOR = 'black'
STROKE_WIDTH = 3
BOTTOM_MARGIN = 150 
FPS = 24  # Enforce a constant framerate

# --- HELPER FUNCTIONS ---

def find_file_in_dir(filename, search_path):
    """Recursively searches for a filename in a directory."""
    for root, dirs, files in os.walk(search_path):
        if filename in files:
            return os.path.join(root, filename)
    return None

def apply_motion_effect(clip: ImageClip, effect_type="zoom_in", zoom_amount=0.1):
    """Applies a zoom or pan effect and ensures duration and FPS are preserved."""
    duration = clip.duration
    w, h = clip.size
    
    # We capture duration in a local var to ensure the lambda finds it
    d = float(duration)

    if effect_type == "zoom_in":
        clip = clip.resize(lambda t: 1 + (zoom_amount * (t / d)))
    elif effect_type == "zoom_out":
        clip = clip.resize(lambda t: 1 + zoom_amount - (zoom_amount * (t / d)))
    elif effect_type == "pan_right":
        # Pre-resize height to allow panning width
        clip = clip.resize(height=h * (1 + zoom_amount))
        new_w, _ = clip.size
        clip = clip.set_position(lambda t: (-( (new_w - w) * (t / d)), 'center'))
    
    # Crucial: Reset duration AND FPS after transformation
    return clip.set_duration(duration).set_fps(FPS)

def generate_subtitle_clips(word_data, video_w, video_h):
    """Generates word-by-word highlighting text clips."""
    if not word_data:
        return []

    generated_clips = []
    lines = []
    current_line = []
    current_width = 0
    max_width = video_w - 100 

    # Measure and Wrap
    for item in word_data:
        word_txt = item['word']
        # Create a temporary clip to measure width
        # Note: If ImageMagick is not installed/configured, TextClip will fail here.
        temp_clip = TextClip(word_txt, font=FONT_NAME, fontsize=FONT_SIZE)
        word_width = temp_clip.w
        word_height = temp_clip.h
        temp_clip.close()

        item['width'] = word_width
        item['height'] = word_height

        if current_width + word_width > max_width and current_line:
            lines.append(current_line)
            current_line = []
            current_width = 0
        
        current_line.append(item)
        current_width += word_width + 20 

    if current_line:
        lines.append(current_line)

    # Create Clips
    for line in lines:
        if not line: continue
        line_start = line[0]['start']
        line_end = line[-1]['end']
        
        total_line_width = sum(item['width'] for item in line) + (20 * (len(line) - 1))
        start_x = (video_w - total_line_width) / 2
        current_x = start_x
        y_pos = video_h - BOTTOM_MARGIN - line[0]['height']

        for item in line:
            # Base (Gray/White)
            txt_base = (TextClip(item['word'], font=FONT_NAME, fontsize=FONT_SIZE, 
                                 color=COLOR_INACTIVE, stroke_color=STROKE_COLOR, stroke_width=STROKE_WIDTH)
                        .set_position((current_x, y_pos))
                        .set_start(line_start)
                        .set_end(line_end)
                        .set_fps(FPS)) # Set FPS
            
            # Active (Yellow)
            txt_active = (TextClip(item['word'], font=FONT_NAME, fontsize=FONT_SIZE, 
                                   color=COLOR_ACTIVE, stroke_color=STROKE_COLOR, stroke_width=STROKE_WIDTH)
                          .set_position((current_x, y_pos))
                          .set_start(item['start'])
                          .set_end(item['end'])
                          .set_fps(FPS)) # Set FPS
            
            generated_clips.extend([txt_base, txt_active])
            current_x += item['width'] + 20

    return generated_clips


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
    # --- LOGGING REQUEST DATA ---
    logger.info("=== NEW REQUEST RECEIVED ===")
    logger.info(f"Target Filename: {filename}")
    logger.info(f"Target Resolution: {target_width}x{target_height}")
    logger.info(f"Zip Filename: {file.filename}")
    logger.info(f"Timeline Data (Raw): {timeline}")
    if subtitles:
        logger.info(f"Subtitle Data (Len): {len(subtitles)} chars")
    
    temp_dir = tempfile.mkdtemp()
    
    try:
        timeline_data = json.loads(timeline)
        subtitle_data = json.loads(subtitles) if subtitles else []
        
        # 1. Save and Extract ZIP
        zip_path = os.path.join(temp_dir, "upload.zip")
        with open(zip_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
            logger.info(f"Extracted zip to {temp_dir}")
            logger.info(f"Files in temp: {os.listdir(temp_dir)}")

        # 2. Process Image Clips
        processed_clips = []
        
        for index, item in enumerate(timeline_data):
            img_name = item["filename"]
            duration = float(item["duration"])
            effect = item.get("effect", "zoom_in")
            zoom_val = item.get("zoom_amount", 0.15)
            
            # Locate file (handle subfolders)
            path = find_file_in_dir(img_name, temp_dir)
            
            if not path:
                logger.error(f"Image NOT FOUND: {img_name}. Skipping.")
                continue

            logger.info(f"Processing Clip {index+1}: {img_name} | Dur: {duration}s | Effect: {effect}")

            # Load clip -> Set Duration -> Set FPS immediately
            # This is critical for concatenate_videoclips to calculate total duration correctly
            clip = ImageClip(path).set_duration(duration).set_fps(FPS)

            # Resize to cover the target area (Crop-to-fill logic)
            clip_ratio = clip.w / clip.h
            target_ratio = target_width / target_height

            if clip_ratio > target_ratio:
                # Image is wider than target
                clip = clip.resize(height=target_height)
            else:
                # Image is taller/narrower than target
                clip = clip.resize(width=target_width)

            # Apply motion (Returns a clip with FPS set)
            clip = apply_motion_effect(clip, effect_type=effect, zoom_amount=zoom_val)

            # Center and Crop to final size using CompositeVideoClip
            # CRITICAL: The composite wrapper must also have FPS and Duration set explicitly
            final_slide = CompositeVideoClip(
                [clip.set_position("center")], 
                size=(target_width, target_height)
            ).set_duration(duration).set_fps(FPS)

            processed_clips.append(final_slide)
            
        if not processed_clips:
            logger.error("No valid clips created.")
            return {"error": "No images could be processed. Check filenames in JSON vs ZIP."}

        # 3. Concatenate Slideshow
        logger.info(f"Concatenating {len(processed_clips)} clips...")
        # method="compose" is safer for mixing effects, provided FPS is set
        base_video = concatenate_videoclips(processed_clips, method="compose")
        total_duration = base_video.duration
        logger.info(f"Base video duration calculated: {total_duration}")

        # 4. Generate Subtitles
        final_layers = [base_video]
        if subtitle_data:
            logger.info("Generating subtitle clips...")
            text_clips = generate_subtitle_clips(subtitle_data, target_width, target_height)
            final_layers.extend(text_clips)

        # 5. Composite Final Video
        final_clip = CompositeVideoClip(final_layers, size=(target_width, target_height))
        final_clip = final_clip.set_duration(total_duration).set_fps(FPS)

        # 6. Audio Integration
        if audio_file:
            logger.info(f"Processing audio: {audio_file.filename}")
            audio_path = os.path.join(temp_dir, audio_file.filename)
            with open(audio_path, "wb") as buffer:
                shutil.copyfileobj(audio_file.file, buffer)
            
            audio = AudioFileClip(audio_path)
            if audio.duration < total_duration:
                audio = audio_loop(audio, duration=total_duration)
            else:
                audio = audio.set_duration(total_duration)
            
            final_clip = final_clip.set_audio(audio)

        # 7. Render
        output_path = os.path.join(temp_dir, f"{filename}.mp4")
        logger.info(f"Rendering to {output_path}...")
        
        final_clip.write_videofile(
            output_path, 
            fps=FPS, 
            codec="libx264", 
            audio_codec="aac",
            threads=4,
            preset="ultrafast", # Use 'ultrafast' for testing, 'medium' for quality
            logger="bar" 
        )
        
        logger.info("Rendering complete.")

        return FileResponse(
            output_path, 
            media_type="video/mp4", 
            filename=f"{filename}.mp4",
            background=background_tasks.add_task(shutil.rmtree, temp_dir)
        )

    except Exception as e:
        logger.exception("Error during generation")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        return {"status": "error", "message": str(e)}
import os
import subprocess
import uuid
import io
from flask import send_file
from werkzeug.utils import secure_filename

def add_audio_to_video(request):
    """HTTP Cloud Function to merge audio and video."""
    # 1. Validate request
    if request.method != 'POST':
        return 'Method not allowed. Please use POST.', 405

    if 'video' not in request.files or 'audio' not in request.files:
        return 'Missing "video" or "audio" files in the form-data request', 400

    video_file = request.files['video']
    audio_file = request.files['audio']

    # Helper function to get the file extension
    def get_extension(filename, default):
        if filename and '.' in filename:
            return '.' + secure_filename(filename).rsplit('.', 1)[1].lower()
        return default

    video_ext = get_extension(video_file.filename, '.mp4')
    audio_ext = get_extension(audio_file.filename, '.mp3')

    # 2. Setup unique temporary file paths
    session_id = str(uuid.uuid4())
    tmp_video = f'/tmp/video_{session_id}{video_ext}'
    tmp_audio = f'/tmp/audio_{session_id}{audio_ext}'
    tmp_output = f'/tmp/output_{session_id}.mp4'

    try:
        # 3. Save uploaded files to the in-memory /tmp directory
        video_file.save(tmp_video)
        audio_file.save(tmp_audio)

        # 4. Run FFmpeg
        # -map 0:v:0 -> takes video from the first input
        # -map 1:a:0 -> takes audio from the second input
        # -c:v copy  -> copies the video stream without re-encoding (very fast)
        # -c:a aac   -> encodes audio to AAC for wide MP4 compatibility
        # -shortest  -> cuts the output to the duration of the shortest input
        command = [
            'ffmpeg', '-y',
            '-i', tmp_video,
            '-i', tmp_audio,
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-shortest',
            tmp_output
        ]
        
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        if process.returncode != 0:
            error_msg = process.stderr.decode('utf-8')
            print(f"FFmpeg Error: {error_msg}")
            return f"Error processing media: {error_msg}", 500

        # 5. Read output into a memory buffer so we can safely delete the tmp files
        with open(tmp_output, 'rb') as f:
            output_data = f.read()

        # 6. Return the processed video directly in the HTTP response
        return send_file(
            io.BytesIO(output_data),
            mimetype='video/mp4',
            as_attachment=True,
            download_name='merged_output.mp4'
        )

    finally:
        # 7. CRITICAL: Clean up /tmp to avoid memory leaks
        # If you don't delete these, your function will run out of RAM on subsequent calls.
        for file_path in [tmp_video, tmp_audio, tmp_output]:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Cleanup error for {file_path}: {e}")
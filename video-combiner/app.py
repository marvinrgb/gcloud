import os
import subprocess
import uuid
import zipfile
from flask import Flask, request, send_file

app = Flask(__name__)

@app.route('/combine', methods=['POST'])
def combine_videos():
    # 1. Check if n8n sent files
    if not request.files:
        return "No files were uploaded", 400

    # Grab the first file in the request (regardless of the field name n8n uses)
    uploaded_file = next(iter(request.files.values()))
    
    if not uploaded_file.filename.lower().endswith('.zip'):
        return "Uploaded file must be a .zip file", 400

    # 2. Create a unique temporary folder
    job_id = str(uuid.uuid4())
    work_dir = f"/tmp/{job_id}"
    extracted_dir = f"{work_dir}/extracted"
    os.makedirs(extracted_dir, exist_ok=True)
    
    zip_path = f"{work_dir}/upload.zip"
    list_file_path = f"{work_dir}/mylist.txt"
    output_file = f"{work_dir}/final_output.mp4"
    
    # 3. Save and extract the zip file
    uploaded_file.save(zip_path)
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extracted_dir)
        
    # 4. Find the extracted files, filter out hidden/system files, and sort them
    # Sorting ensures they are combined in alphabetical order (e.g., 01.mp4, 02.mp4...)
    extracted_files = [
        f for f in os.listdir(extracted_dir) 
        if not f.startswith('.') and not f.startswith('__MACOSX')
    ]
    extracted_files.sort()
    
    if not extracted_files:
        return "No video files found in the zip", 400

    # 5. Build the FFmpeg list
    with open(list_file_path, 'w') as f:
        for idx, filename in enumerate(extracted_files):
            original_path = os.path.join(extracted_dir, filename)
            
            # We rename the files safely to avoid errors in FFmpeg if the original 
            # filenames contained spaces, single quotes, or special characters.
            safe_vid_path = os.path.join(extracted_dir, f"video_{idx}.mp4")
            os.rename(original_path, safe_vid_path)
            
            f.write(f"file '{safe_vid_path}'\n")
    
    # 6. Run FFmpeg to combine them
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
        "-i", list_file_path, "-c", "copy", output_file
    ]
    subprocess.run(cmd, check=True)
    
    # 7. Send the finished video back to n8n
    return send_file(output_file, mimetype='video/mp4')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
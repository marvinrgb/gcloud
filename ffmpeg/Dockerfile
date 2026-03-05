# Use a lightweight Python version
FROM python:3.9-slim

# Install FFmpeg (Required for moviepy)
RUN apt-get update && \
    apt-get install -y ffmpeg libsm6 libxext6 imagemagick && \
    rm -rf /var/lib/apt/lists/*

RUN sed -i 's/none/read,write/g' /etc/ImageMagick-7/policy.xml
# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Expose port (Cloud Run defaults to 8080)
ENV PORT=8080

# Run with Uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
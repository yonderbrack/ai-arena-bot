FROM python:3.11-slim
ARG CACHE_BUST=20250825_1350_V10_TV_ANTIBOT
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -U yt-dlp
COPY . .
CMD ["python", "-u", "youtube_live_bot.py"]

FROM python:3.11-slim
ARG CACHE_BUST=20250825_1315
WORKDIR /app
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "youtube_live_bot.py"]

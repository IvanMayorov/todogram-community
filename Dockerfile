FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY webapp ./webapp
ENV DB_PATH=/srv/data/bot.sqlite3 CACHE_DIR=/srv/data/cache PYTHONUNBUFFERED=1
CMD ["python", "-m", "app.main"]

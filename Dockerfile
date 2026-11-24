FROM python:3.12-alpine

LABEL maintainer="scanek"
LABEL description="Universal Jellyfin & qBittorrent Media Renamer with Kinopoisk Unofficial API"

WORKDIR /app

COPY jellyfin_renamer.py /app/
COPY auto_renamer.sh /app/

RUN chmod +x /app/jellyfin_renamer.py /app/auto_renamer.sh

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/app/jellyfin_renamer.py"]
CMD ["--help"]

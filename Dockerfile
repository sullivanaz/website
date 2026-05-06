FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY site-src ./site-src
COPY tools ./tools
COPY README.md ./

RUN mkdir -p /data/dist /data/cache \
    && chown -R app:app /app /data

EXPOSE 8585

VOLUME ["/data"]

USER app:app

CMD ["python", "tools/run_gallery_service.py"]

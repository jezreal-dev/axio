FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SAMPLE_MODE=true \
    PORT=8000 \
    HOST=0.0.0.0

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY data/ ./data/
COPY input_examples/ ./input_examples/
COPY output_examples/ ./output_examples/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY web/ ./web/
COPY run.py .env.example metadata.json README.md ./

RUN mkdir -p logs state && python3 data/db_seed.py

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["python3", "run.py"]

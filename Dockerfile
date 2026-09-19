FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the spaCy model at BUILD time — doing it at runtime would make every
# cold start re-download ~12 MB.
RUN python -m spacy download en_core_web_sm

COPY . .
RUN mkdir -p uploads output

# Render injects $PORT; fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]

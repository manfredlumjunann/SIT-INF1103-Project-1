FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for document parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    antiword \
    unrtf \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir \
    flask==3.0.3 \
    gunicorn==22.0.0 \
    openai==1.68.0 \
    firecrawl-py==1.15.0 \
    PyPDF2==3.0.1 \
    python-docx==1.1.0 \
    requests==2.31.0 \
    python-dotenv==1.0.1 \
    werkzeug==3.0.3

# Copy application
COPY logic-layer/app.py .
COPY logic-layer/requirements.txt .
COPY data-layer/data_manager.py .

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "600", "--graceful-timeout", "600", "--workers", "1", "--threads", "4", "app:app"]

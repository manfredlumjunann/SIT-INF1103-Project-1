FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for document parsing and nginx
RUN apt-get update && apt-get install -y --no-install-recommends \
    antiword \
    unrtf \
    tesseract-ocr \
    poppler-utils \
    nginx \
    supervisor \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/supervisor

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
    werkzeug==3.0.3 \
    sqlalchemy==2.1.8 \
    "psycopg[binary]==3.3.8"

# Copy the application code: every layer's Python modules sit side by side in /app
COPY logic-layer/*.py /app/
COPY ai-layer/*.py /app/
COPY data-layer/*.py /app/
COPY io-layer/*.py /app/

# Copy IO layer files
COPY io-layer/nginx.conf /etc/nginx/conf.d/default.conf
COPY io-layer/index.html /usr/share/nginx/html/
COPY io-layer/styles.css /usr/share/nginx/html/
COPY io-layer/common.js /usr/share/nginx/html/
COPY io-layer/app.js /usr/share/nginx/html/
COPY io-layer/login.html /usr/share/nginx/html/
COPY io-layer/login.js /usr/share/nginx/html/
COPY io-layer/theme.js /usr/share/nginx/html/

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose nginx port
EXPOSE 80

# supervisor runs nginx and gunicorn; the app creates its database tables when it starts
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

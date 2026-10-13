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
    werkzeug==3.0.3

# Copy logic layer application
COPY logic-layer/app.py /app/
COPY data-layer/data_manager.py /app/

# Create data directory
RUN mkdir -p /app/data

# Copy IO layer files
COPY io-layer/nginx.conf /etc/nginx/conf.d/default.conf
COPY io-layer/user_index.html /usr/share/nginx/html/
COPY io-layer/admin_index.html /usr/share/nginx/html/
COPY io-layer/styles.css /usr/share/nginx/html/
COPY io-layer/common.js /usr/share/nginx/html/
COPY io-layer/app.js /usr/share/nginx/html/
COPY io-layer/admin.js /usr/share/nginx/html/
COPY io-layer/login.html /usr/share/nginx/html/
COPY io-layer/login.js /usr/share/nginx/html/
COPY io-layer/theme.js /usr/share/nginx/html/

# Remove default nginx index
RUN rm -f /usr/share/nginx/html/index.html

# Copy supervisor configuration
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose nginx port
EXPOSE 80

# Use supervisor to manage both nginx and gunicorn
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

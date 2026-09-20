FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY datacleaner ./datacleaner
COPY server ./server
COPY docs ./docs
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8000
# One worker on purpose: datasets live in this process's memory (see README "Scaling").
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]

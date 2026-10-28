FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py ./
COPY lib ./lib
COPY services ./services
COPY routes ./routes
COPY templates ./templates
COPY static ./static

RUN useradd --create-home --uid 10001 darts \
    && mkdir -p /data && chown darts:darts /data
USER darts

ENV DARTS_DATA_DIR=/data \
    DARTS_HOST=0.0.0.0 \
    DARTS_PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y git curl && \
    curl -fsSL https://get.docker.com | sh && \
    rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory /app/project

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]

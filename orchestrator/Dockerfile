FROM python:3.12-slim

RUN useradd -m -s /bin/bash trader
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

USER trader
CMD ["python", "scheduler.py"]

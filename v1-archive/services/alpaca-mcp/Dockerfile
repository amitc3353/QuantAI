FROM python:3.12-slim

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash trader
WORKDIR /app

# Clone official Alpaca MCP server
RUN git clone https://github.com/alpacahq/alpaca-mcp-server.git . && \
    pip install --no-cache-dir -r requirements.txt

USER trader
CMD ["python", "alpaca_mcp_server.py"]

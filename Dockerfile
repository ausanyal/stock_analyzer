FROM python:3.11-slim

WORKDIR /app

# Copy the StockSage Quant Pro application
COPY stock-analyzer/ /app/stock-analyzer/

# Cloud Run injects PORT (defaults to 8080 in cloud, 8765 locally)
ENV PORT=8080
ENV HOST=0.0.0.0
EXPOSE 8080

CMD ["python3", "/app/stock-analyzer/server.py"]

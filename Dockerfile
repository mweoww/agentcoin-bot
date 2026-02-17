FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .
# Setelah COPY requirements.txt, tambahin:
RUN echo "X_HANDLE=$X_HANDLE" >> .env && \
    echo "X_AUTH_TOKEN=$X_AUTH_TOKEN" >> .env && \
    echo "X_CT0=$X_CT0" >> .env && \
    echo "ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN" >> .env
    
# Run the miner
CMD ["python", "mine.py"]

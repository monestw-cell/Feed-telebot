FROM python:3.10-slim

# Install system dependencies for any potential compiling requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose port for Render environment compatibility
EXPOSE 8080

CMD ["python", "main.py"]

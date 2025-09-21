# Use the official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Create and set working directory
WORKDIR /app

# Install system dependencies (needed for some Python libs)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source code
COPY . .

# Expose the port Cloud Run will use
EXPOSE 8080

# Start FastAPI app with Uvicorn
# Cloud Run provides $PORT automatically
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

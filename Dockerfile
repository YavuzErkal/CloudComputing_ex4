FROM --platform=linux/amd64 python:3.12

# Thanks to the next line python logs from inside the contaier are not buffered and can be seen in stdout
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container
WORKDIR /app

# Install kubectl CLI inside the container
RUN apt-get update && apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    chmod +x kubectl && mv kubectl /usr/local/bin/

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run python scheduler.py
CMD ["python", "scheduler.py"]


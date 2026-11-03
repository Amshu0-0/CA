# Build the CA3 producer image.
FROM python:3.11-slim

# Use /app as the working folder inside the container.
WORKDIR /app

# Install the Python library used to send messages to Kafka.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the producer program, and the CSV file that it replays.
# The file name must match DATASET_FILE in the ConfigMap pipeline-config.
ARG DATASET_FILE=Friday-Morning-5000-mixed-bot.csv
COPY producer.py .
COPY ${DATASET_FILE} .

# Run as a normal user with a fixed numeric ID (1000), so Kubernetes can verify "runAsNonRoot: true".
RUN useradd --uid 1000 --create-home appuser && chown -R appuser /app
USER 1000

# The port where Prometheus reads the producer's numbers (PRODUCER_METRICS_PORT in the ConfigMap pipeline-config).
EXPOSE 8001

# Start the producer. Without --trace-id it replays the dataset (once, or forever with REPLAY_FOREVER=true).
CMD ["python", "-u", "producer.py"]

# Build the producer image: the producer, the setup program bootstrap.py (the Job "bootstrap" runs it), and the file that the producer replays.
# The build context is the repository root (producer/Dockerfile.dockerignore keeps everything else out), because the replay file is
# in NLP-pipeline/bundle.
FROM python:3.11-slim

# Use /app as the working folder inside the container.
WORKDIR /app

# Install the Python libraries: the one that sends messages to Kafka, the metrics, and "cryptography" for bootstrap.py.
COPY NLP-pipeline/producer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the programs, and the file that the producer replays.
# The file name must match DATASET_FILE in the ConfigMap pipeline-config.
ARG DATASET_FILE=bgl-replay.log
COPY NLP-pipeline/producer/producer.py NLP-pipeline/producer/bootstrap.py ./
COPY NLP-pipeline/bundle/${DATASET_FILE} .

# Run as a normal user with a fixed numeric ID (1000), so Kubernetes can verify "runAsNonRoot: true".
RUN useradd --uid 1000 --create-home appuser && chown -R appuser /app
USER 1000

# The port where Prometheus reads the producer's numbers (PRODUCER_METRICS_PORT in the ConfigMap pipeline-config).
EXPOSE 8001

# Start the producer. Without --trace-id it replays the file once.
CMD ["python", "-u", "producer.py"]

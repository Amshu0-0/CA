FROM python:3.11-slim

# Use /app as the working folder inside the container
WORKDIR /app

# Copy the producer program into the container
COPY producer.py .

# Copy the CSV file that the producer replays.
# Its name comes from config.yml: Ansible passes it in as a build argument.
ARG DATASET_FILE
COPY ${DATASET_FILE} .

# Install the Python library used to send messages to Kafka
RUN pip install kafka-python

# Create a normal user and give it access to the files in /app
RUN useradd --create-home appuser && chown -R appuser /app

# Run the container as the normal user instead of root
USER appuser

# Start the producer when the container starts
CMD ["python", "-u", "producer.py"]

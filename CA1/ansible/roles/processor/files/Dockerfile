# Use a small Python 3.11 image as the base.
FROM python:3.11-slim

# Put the application files inside /app.
WORKDIR /app

# Copy the processor program into the container.
COPY processor.py .

# Install the Python packages the processor needs to connect to Kafka and MongoDB.
RUN pip install kafka-python pymongo

# Create a non-root user and give it ownership of /app.
# The processor will run as this user instead of root.
RUN useradd --create-home appuser && chown -R appuser /app

# Run the container as the non-root user.
USER appuser

# Start the processor and use -u so logs are printed immediately instead of being buffered.
CMD ["python", "-u", "processor.py"]

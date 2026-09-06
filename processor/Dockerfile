FROM python:3.11-slim

# Use /app as the working folder inside the container
WORKDIR /app

# Copy processor program into the container
COPY processor.py .

# Install the Python libraries the processor needs
RUN pip install kafka-python pymongo

# Create a normal user called appuse and give that user access to the /app folder
RUN useradd --create-home appuser && chown -R appuser /app

# Run the container as appuser instead of root
USER appuser

# Start the processor when the container starts
CMD ["python","-u", "processor.py"]

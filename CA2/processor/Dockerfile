# Build the CA2 processor image.
# The base image is pinned to a Python minor version, and the Python packages are pinned in requirements.txt.
FROM python:3.11-slim

# Put the application files inside /app.
WORKDIR /app

# Install the packages first, so Docker can reuse this layer when only processor.py changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the processor program into the container.
COPY processor.py .

# Run as a normal user with a fixed numeric ID (1000).
# The number matters: Kubernetes can only verify "runAsNonRoot: true" when the image user is numeric.
RUN useradd --uid 1000 --create-home appuser && chown -R appuser /app
USER 1000

# The port of the health and metrics server (the processor's ClusterIP Service points here).
EXPOSE 8000

# Start the processor. -u prints logs immediately instead of buffering them.
CMD ["python", "-u", "processor.py"]

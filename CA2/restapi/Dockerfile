# Build the CA2 REST API image.
FROM python:3.11-slim

# Put the application files inside /app.
WORKDIR /app

# Install Flask, the MongoDB driver, and gunicorn (the production web server).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the REST API program into the container.
COPY rest_app.py .

# Run as a normal user with a fixed numeric ID (1000), so Kubernetes can verify "runAsNonRoot: true".
RUN useradd --uid 1000 --create-home appuser && chown -R appuser /app
USER 1000

# The port is chosen by REST_PORT in the ConfigMap; 8080 is the value this project uses.
EXPOSE 8080

# Start gunicorn: 2 worker processes with 2 threads each.
# Each worker creates its own MongoDB connection after it starts (PyMongo must not be shared across a fork).
# --worker-tmp-dir /dev/shm keeps gunicorn's heartbeat files in memory, so the container needs no writable disk.
# "exec" makes gunicorn the main process, so it receives Kubernetes' SIGTERM and stops cleanly.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${REST_PORT} --workers 2 --threads 2 --worker-tmp-dir /dev/shm --access-logfile - rest_app:app"]

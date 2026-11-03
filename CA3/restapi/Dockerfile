# Build the CA3 REST API image.
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

# The port is chosen by REST_PORT in the ConfigMap; 8443 is the value this project uses. The port speaks HTTPS only.
# 9102 (REST_METRICS_PORT) is where Prometheus reads the numbers: plain HTTP, no data from the database, only Prometheus may reach it.
EXPOSE 8443 9102

# Start gunicorn: ONE worker process with 8 threads, speaking HTTPS. One process on purpose: the numbers that Prometheus reads live
# in the memory of the process, so with several processes each of them would show only part of the traffic. The eight threads answer
# eight requests at the same time (a request mostly waits for MongoDB, so threads are enough). The program writes its own request log
# (one JSON line per request), so gunicorn's access log is off.
# --certfile and --keyfile are the server certificate and key that Kubernetes mounts from the Secret restapi-tls. The ":?" makes
# the container stop with a clear message if either setting is missing: there is no way to start the API without TLS.
# The worker creates its MongoDB connection after it starts (PyMongo must not be shared across a fork).
# --worker-tmp-dir /dev/shm keeps gunicorn's heartbeat files in memory, so the container needs no writable disk.
# "exec" makes gunicorn the main process, so it receives Kubernetes' SIGTERM and stops cleanly.
CMD ["sh", "-c", ": \"${REST_TLS_CERT_FILE:?REST_TLS_CERT_FILE is not set}\" \"${REST_TLS_KEY_FILE:?REST_TLS_KEY_FILE is not set}\"; exec gunicorn --bind 0.0.0.0:${REST_PORT} --certfile ${REST_TLS_CERT_FILE} --keyfile ${REST_TLS_KEY_FILE} --workers 1 --threads 8 --worker-tmp-dir /dev/shm rest_app:app"]

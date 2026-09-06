FROM python:3.11-slim
WORKDIR /app
COPY processor.py .
RUN pip install kafka-python pymongo
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
CMD ["python", "processor.py"]

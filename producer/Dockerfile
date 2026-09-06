FROM python:3.11-slim
WORKDIR /app
COPY producer.py .
COPY Friday-WorkingHours-Morning_pcap_ISCX.csv .
RUN pip install kafka-python
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
CMD ["python", "producer.py"]

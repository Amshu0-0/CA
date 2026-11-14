# Build the processor image: the processor, the NLP project's package (logad), PyTorch for the CPU, and the trained models.
# The build context is the repository root (processor/Dockerfile.dockerignore keeps everything else out), because the image needs
# NLP/src (the package) and NLP-pipeline/bundle (the models) next to the program.
# The base image is pinned to a Python minor version, and the Python packages are pinned in requirements.txt (PyTorch is not: see below).
FROM python:3.11-slim

# Put the application files inside /app.
WORKDIR /app

# PyTorch for the CPU only, from PyTorch's own package index. The default wheel carries gigabytes of GPU libraries that these machines
# cannot use. It is installed first so that installing logad below finds it and does not fetch the GPU build.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.1,<3"

# Install the packages first, so Docker can reuse this layer when only the program changes.
COPY NLP-pipeline/processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The NLP project's package. pip installs its other requirements too (numpy, pandas, pyarrow, drain3, tqdm, pyyaml).
COPY NLP/pyproject.toml NLP/README.md /nlp/
COPY NLP/src /nlp/src
RUN pip install --no-cache-dir /nlp

# The processor: the program, the part that does the thinking (engine.py), and the part that writes to MongoDB (sink.py).
COPY NLP-pipeline/processor/processor.py NLP-pipeline/processor/engine.py NLP-pipeline/processor/sink.py ./

# The trained models and the settings that go with them: drain.pkl, model.pt, rules.json, llm_rule_based_decisions.csv, settings.json.
# scripts/export-nlp-bundle.py collects them from the NLP project (BUNDLE_DIR in processor/processor.yaml says where they are).
COPY NLP-pipeline/bundle /bundle

# Run as a normal user with a fixed numeric ID (1000).
# The number matters: Kubernetes can only verify "runAsNonRoot: true" when the image user is numeric.
RUN useradd --uid 1000 --create-home appuser && chown -R appuser /app
USER 1000

# The port of the health and metrics server (the processor's ClusterIP Service points here).
EXPOSE 8000

# Start the processor. -u prints logs immediately instead of buffering them.
CMD ["python", "-u", "processor.py"]

# Makes the programs importable for the tests: the processor's engine, and the NLP project's package (logad).
# The tests need no cluster, no Kafka and no MongoDB: they use fakes for those. Run them with:  make test   (or: python3 -m pytest NLP-pipeline/tests)

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
REPO = PIPELINE.parent

for folder in (REPO / "NLP" / "src", PIPELINE / "processor", PIPELINE / "producer", PIPELINE / "restapi", PIPELINE / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

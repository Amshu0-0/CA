# Makes the programs importable for the tests: the processor's engine, and the NLP project's package (logad).
# The tests need no cluster, no Kafka and no MongoDB: they use fakes for those. Run them with:  make test   (or: python3 -m pytest NLP-pipeline/tests)

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PIPELINE = HERE.parent
REPO = PIPELINE.parent

for folder in (REPO / "NLP" / "src", PIPELINE / "processor", PIPELINE / "producer", PIPELINE / "restapi", PIPELINE / "scripts"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


# The real bundle/ folder is what  make export-bundle  made on this computer (the trained models and the replay file, and what the images are
# built from). The tests work in folders of their own; one that reads it makes a test depend on whether the export was run (4 tests did, and
# failed on a computer that had run it), and one that writes into it can leave a mix behind. So: after the whole run it must be as before.
def real_bundle_state():
    folder = PIPELINE / "bundle"
    return None if not folder.exists() else sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir())


@pytest.fixture(scope="session", autouse=True)
def the_tests_leave_the_real_bundle_alone():
    before = real_bundle_state()
    yield
    assert real_bundle_state() == before, "a test changed the real NLP-pipeline/bundle/ folder; tests must work in a folder of their own (tmp_path)"

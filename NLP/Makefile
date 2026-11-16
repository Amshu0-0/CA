# =============================================================================
# One target per pipeline stage. Override the experiment with CONFIG=..., e.g.
#   make smoke                         # whole pipeline on the 2k-line sample
#   make preprocess logbert            # full BGL: train and evaluate LogBERT
#   make all CONFIG=configs/bgl.yaml   # everything, in order
# =============================================================================

CONFIG ?= configs/bgl.yaml
PY     ?= python
# Output directory of the experiment in CONFIG (paths.output_dir).
OUT    ?= outputs/bgl

.PHONY: help install data data-2k preprocess logbert logbert-train logbert-eval \
        compare analyze results restore figures demo test lint format smoke all

help:            ## List the available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install:         ## Install the package with all optional dependencies
	$(PY) -m pip install -r requirements.txt

data:            ## Download the full BGL dataset (~700 MB unpacked)
	$(PY) scripts/download_data.py --dataset bgl

data-2k:         ## Download Loghub's 2,000-line BGL sample
	$(PY) scripts/download_data.py --dataset bgl_2k

preprocess:      ## Drain templates, chronological split, sliding windows
	$(PY) scripts/preprocess.py --config $(CONFIG)

logbert-train:   ## Train LogBERT on normal training windows
	$(PY) scripts/train_logbert.py --config $(CONFIG)

logbert-eval:    ## Tune g/r on validation, evaluate on test (both masking modes)
	$(PY) scripts/evaluate_logbert.py --config $(CONFIG)
	$(PY) scripts/evaluate_logbert.py --config $(CONFIG) --set logbert.detection.full_coverage=true

logbert: logbert-train logbert-eval  ## LogBERT end to end

compare:         ## Results table of both LogBERT variants
	$(PY) scripts/compare.py --config $(CONFIG)

analyze:         ## Error analysis tables for the report
	$(PY) scripts/analyze.py --config $(CONFIG)

results:         ## Copy the deliverables (metrics, predictions, analysis, checkpoint) into results/
	mkdir -p results/analysis results/logbert results/predictions
	cp $(OUT)/results/* results/
	-cp $(OUT)/predictions/*.parquet results/predictions/
	-cp $(OUT)/analysis/* results/analysis/
	-cp $(OUT)/logbert/model.pt $(OUT)/logbert/history.json $(OUT)/logbert/*_tuning_grid.csv results/logbert/

restore:         ## Copy the committed results/ back into $(OUT), e.g. to run the demo without retraining
	@test ! -e $(OUT)/logbert/model.pt || { echo "$(OUT) already holds a run; pick another OUT=..."; exit 1; }
	mkdir -p $(OUT)/results $(OUT)/predictions $(OUT)/analysis $(OUT)/logbert
	cp results/*.json results/comparison.csv $(OUT)/results/
	cp results/predictions/* $(OUT)/predictions/
	cp results/analysis/* $(OUT)/analysis/
	cp results/logbert/* $(OUT)/logbert/

figures:         ## Draw the figures from a run into docs/figures/ (needs the docs extra)
	$(PY) scripts/make_figures.py --config $(CONFIG)

demo:            ## Launch the Gradio demo
	$(PY) app/demo.py --config $(CONFIG)

test:            ## Run the test suite
	$(PY) -m pytest

lint:            ## Static checks (ruff lint + formatting)
	ruff check .
	ruff format --check .

format:          ## Auto-format the code
	ruff format .

smoke:           ## Whole pipeline on the 2k-line sample (CPU, ~1 minute)
	$(MAKE) data-2k
	$(MAKE) preprocess logbert compare analyze CONFIG=configs/bgl_2k.yaml

all: preprocess logbert compare analyze  ## Every stage, in order

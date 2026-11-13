# =============================================================================
# One target per pipeline stage. Override the experiment with CONFIG=..., e.g.
#   make smoke                         # whole pipeline on the 2k-line sample
#   make preprocess logbert            # full BGL, Approach A
#   make all CONFIG=configs/bgl.yaml   # everything, in order
# =============================================================================

CONFIG ?= configs/bgl.yaml
PY     ?= python

.PHONY: help install data data-2k preprocess logbert logbert-train logbert-eval \
        rules rule-guided keyword compare analyze report-tables demo test lint format smoke all

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

logbert-train:   ## Approach A: train LogBERT on normal training windows
	$(PY) scripts/train_logbert.py --config $(CONFIG)

logbert-eval:    ## Approach A: tune g/r on validation, evaluate on test (both masking modes)
	$(PY) scripts/evaluate_logbert.py --config $(CONFIG)
	$(PY) scripts/evaluate_logbert.py --config $(CONFIG) --set logbert.detection.full_coverage=true

logbert: logbert-train logbert-eval  ## Approach A end to end

rules:           ## Approach B: induce, verify and rank rules (needs the generator LLM)
	$(PY) scripts/induce_rules.py --config $(CONFIG)

rule-guided:     ## Approach B: zero-shot, few-shot, rule-based and keyword conditions
	$(PY) scripts/run_rule_guided.py --config $(CONFIG)

keyword:         ## Approach B: only the keyword baseline (no LLM needed once rules exist)
	$(PY) scripts/run_rule_guided.py --config $(CONFIG) --conditions keyword

compare:         ## Comparison table of every evaluated method
	$(PY) scripts/compare.py --config $(CONFIG)

analyze:         ## Error analysis tables for the report
	$(PY) scripts/analyze.py --config $(CONFIG)

demo:            ## Launch the Gradio demo
	$(PY) app/demo.py --config $(CONFIG)

test:            ## Run the test suite
	$(PY) -m pytest

lint:            ## Static checks (ruff lint + formatting)
	ruff check .
	ruff format --check .

format:          ## Auto-format the code
	ruff format .

smoke:           ## Approach A end to end on the 2k-line sample (CPU, ~1 minute)
	$(MAKE) data-2k
	$(MAKE) preprocess logbert compare analyze CONFIG=configs/bgl_2k.yaml

all: preprocess logbert rules rule-guided compare analyze  ## Every stage, in order

"""logad: log anomaly detection on BGL with LogBERT and rule-guided LLMs.

The package is organised by pipeline stage:

* ``logad.config``      - typed experiment configuration loaded from YAML
* ``logad.utils``       - logging, seeding, timing and JSON helpers
* ``logad.data``        - reading BGL, Drain template mining, splitting, windowing
* ``logad.logbert``     - Approach A: LogBERT reproduction
* ``logad.rules``       - Approach B: LogRules-style rule induction + keyword baseline
* ``logad.llm``         - Approach B: LLM clients and zero/few-shot/rule-based prompting
* ``logad.evaluation``  - shared window-level metrics and error analysis
"""

__version__ = "0.1.0"

"""logad: log anomaly detection on BGL with LogBERT.

The package is organised by pipeline stage:

* ``logad.config``      - typed experiment configuration loaded from YAML
* ``logad.utils``       - logging, seeding, timing and JSON helpers
* ``logad.data``        - reading BGL, Drain template mining, splitting, windowing
* ``logad.logbert``     - LogBERT: vocabulary, model, training and detection
* ``logad.evaluation``  - window-level metrics, result tables and error analysis
* ``logad.demo``        - the back end of the interactive demo
"""

__version__ = "0.1.0"

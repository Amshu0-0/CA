"""Data layer: reading raw BGL logs and turning them into model-ready windows.

Pipeline (run by ``scripts/preprocess.py``)::

    raw BGL.log
      -> bgl.read_bgl            parse fields, split off the alert tag (label)
      -> drain.TemplateExtractor map each message to a Drain template ID
      -> split.chronological_split   60/20/20 train/val/test by time
      -> windows.sliding_windows     5-minute windows, 1-minute step
      -> processed.save_processed    parquet/CSV files under data/processed/
"""

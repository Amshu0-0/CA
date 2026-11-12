"""Approach B: talking to LLMs.

* ``client``   backends (OpenAI-compatible API, local Hugging Face model) with an
               on-disk response cache
* ``prompts``  loads the prompt templates stored in ``prompts/*.txt``
* ``parsing``  robustly extracts JSON / labels from free-form model output
"""

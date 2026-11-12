"""Approach B: LogRules-style rules (Huang et al., NAACL Findings 2025).

* ``rule``       the Rule data structure (text + keywords + label) and its I/O
* ``induction``  induce rules with an LLM from labeled training templates, then
                 verify them on the training set and rank them by usage
* ``keyword``    the no-LLM keyword baseline that applies the rules as plain
                 string matching
"""

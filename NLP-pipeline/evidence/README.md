# Evidence

The saved output of the runs goes in this folder, and it is published with the repository. Nothing in it may hold a public address, an EC2 host name, an AWS account number, a home-folder path or a password.

Save the output of a command with one of these, from the folder `NLP-pipeline`:

| What | How |
|---|---|
| What the detectors say on your computer (no cluster) | `make local-replay LOCAL_ARGS="--save evidence/local-replay.json"` |
| The answer of the REST API to the smoke test | `make smoke SMOKE_ARGS="--save evidence/smoke-response.json"` |
| What the detectors said in the cluster | `make replay RESULTS_ARGS="--windows 10 --compare evidence/local-replay.json --json evidence/cluster-summary.json"` |
| The text of any command | add `2>&1 \| tee evidence/<name>.txt` at the end of the command |

Before a text file is committed, run `make scrub FILES="evidence/<name>.txt"`. It replaces public addresses, host names, account numbers and home-folder paths, and says how many it replaced. `make verify` reads every file in this folder with the same test and fails if it still finds something.

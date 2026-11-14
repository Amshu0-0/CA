# NLP log pipeline: BGL system-log anomaly detection on the CA3 cluster

This folder is the CA3 pipeline with its data and its processing replaced. Where CA3 stored and classified network flows, this one replays the system-log lines of the BlueGene/L supercomputer (BGL) and runs the detectors of the NLP project (`../NLP`, *Comparing BERT-Based and Rule-Guided Language Models for System Log Anomaly Detection*) on them. The cluster, the security and the monitoring are the ones of CA3: Terraform and Ansible build three machines with Kubernetes (k3s); Kafka with mutual TLS; MongoDB with TLS and one account per program; a REST API over HTTPS with a login; NetworkPolicies; Pod Security `restricted`; Prometheus, Grafana and Loki.

```text
 replay file          producer Job           Kafka                  processor                          MongoDB          REST API
 bgl-replay.log  -->  "producer-replay"  -->  topic system-logs  --> Drain templates               -->  events,    -->  /summary, /runs,
 (30,000 BGL lines)   (mutual TLS)            (ONE partition)        keyword rules      (B4)            windows,         /windows, /events
                                                                      saved LLM decisions (B3)           runs             HTTPS + login
                                                                      LogBERT on 5-minute windows (A+)   (TLS)
```

## Quick start

```bash
cd NLP-pipeline
make export-bundle      # Step 1: the trained models and the log file to replay, from your NLP project
make local-replay       # Step 2: the detectors on your computer, no cluster: the numbers the cluster must reproduce
                        # Step 3: push, wait for the three images, make them public, make check-images
make cluster-up         # Step 4: AWS: network, three machines, Kubernetes
make up                 #         the stack
make smoke              # Step 5: one log line through everything
make replay             # Step 6: the whole replay file, and what each detector said
make teardown           # Step 8: everything away, and the proof
```

`make` with no argument lists every command. The details, what to expect and what to do when it does not work are below.

## What replaced what

| CA3 (network flows) | This pipeline (system logs) |
|---|---|
| One CSV row of network traffic per message | One BGL log line per message, plus an `end` marker per replay |
| Producers that send flows as fast as they can, and a load generator | One Job, `producer-replay`, that sends the lines of `bgl-replay.log` at 500 lines per second and ends |
| A processor that labels a flow with rules and inserts it | A processor that runs the NLP project's detectors, cuts the lines into windows, and stores lines, windows and replays |
| Several processor pods, autoscaling, three partitions | **One** processor pod, one partition, no autoscaler (a window is made of lines that follow each other in time, so one reader keeps them in order) |
| `GET /stats`, `/alerts`, `/events/<id>` | The same three, plus `/runs`, `/summary`, `/windows` and `/events` with filters |
| A dashboard for flows and for scaling | A dashboard for replays, windows, the three detectors, LogBERT's time per window, and the lines that needed care |
| Kafka, MongoDB, TLS, logins, NetworkPolicies, RBAC, the 13 Secrets made inside the cluster, Prometheus, Loki, Grafana | **Unchanged** apart from names |

## The NLP project's detectors in the pipeline

| In the pipeline | NLP project condition | What runs in the processor | Files (in `bundle/`, inside the processor image) |
|---|---|---|---|
| `keyword` | B4: keyword rules, no LLM | The top 30 verified rules, applied to the Drain template of a line | `rules.json` |
| `llm` | B3: rule-guided LLM | The **saved decision** of the LLM for the template of a line. The LLM itself does not run in the cluster. A template with no saved decision counts as "not flagged" and is counted | `llm_rule_based_decisions.csv` |
| `logbert` | A+: LogBERT, full-coverage masking | LogBERT on each complete 5-minute window (sliding by 1 minute), with the g, r and mask ratio that the NLP project chose on its validation period, and its seed | `model.pt`, `drain.pkl`, `settings.json` |

A window is anomalous when it holds at least one line with an alert tag. The tag is the ground truth, it is stored for the evaluation only, and no detector sees it. A window is flagged by `keyword` or `llm` when any of its lines is, and by `logbert` when, in some chunk of the window, more than r masked keys are not among LogBERT's g best guesses.

The NLP project's own numbers on its whole test period (5,742 windows, 495 anomalous), from `NLP/results/comparison.csv`. They are a **reference**: the replay file is a 30,000-line slice of that period, so the cluster's numbers differ from these.

| Detector | Precision | Recall | F1 |
|---|---:|---:|---:|
| A+ LogBERT, full coverage | 0.184 | 0.897 | 0.305 |
| B3 rule-guided LLM | 0.489 | 0.943 | 0.644 |
| B4 keyword rules | 1.000 | 0.915 | 0.956 |

What has to agree is the cluster with `make local-replay`: the same programs, on the same file. `make replay` compares them.

## Run it

The steps are in order. Each ends with a line that says whether it worked.

### Before you start

- The folder `NLP-pipeline` and the folder `NLP` sit side by side in the repository (the scripts and the tests look for `../NLP`).
- Steps 1 and 2 run in the **Python environment of your NLP project**: the one in which `make preprocess` and the training worked. Check it with
  `python3 -c "import torch, pandas, pyarrow, yaml, drain3, numpy; print('ok')"`.
- `make test` also needs the packages of the three programs: `pip install -r requirements-dev.txt` (from this folder, in the same environment).
- Steps 4 to 8 need what CA3 needed: Terraform, Ansible, `kubectl`, the AWS CLI with a dedicated IAM user, `ssh-keygen` (see `../CA3/README.md`, *Prerequisites*). **No Docker**: GitHub builds the images.
- The folder name `NLP-pipeline` can be changed. The cluster's resources are named by `infra/config.yml` (`project_tag`, `name_prefix`), not by the folder.

### Step 1. Collect the models and the log file (`make export-bundle`)

It gathers into `bundle/` what the processor and the producer need: the Drain parser, LogBERT, the keyword rules, the saved LLM decisions, the settings (g, r, mask ratio, seed, window, number of rules) and `bgl-replay.log`, a run of 30,000 consecutive lines of the **test period** (the period the detectors were not tuned on), placed where the windows are most evenly mixed between anomalous and normal. Nothing in `../NLP` is changed.

```bash
cd NLP-pipeline
make export-bundle
```

It needs `NLP/data/processed/bgl/` (`drain.pkl`, `messages.parquet`, `windows.parquet`). If that folder is not on this computer, make it first with `make data` and `make preprocess` in `NLP` (the raw log is about 700 MB), or copy the folder from the computer where you ran the NLP project.

The trained files are taken from `NLP/results/` (the run of your report, made with `configs/bgl_cpu.yaml`). A run that is only in `NLP/outputs/` is taken with `make export-bundle BUNDLE_ARGS="--prefer-outputs"`, a run made with another configuration with `make export-bundle NLP_CONFIG=configs/bgl.yaml`. If a file exists in both places and they differ, the script says which one it took.

Expected, at the end:

```text
  Drain check:      the parser gives the template that messages.parquet holds for N of M lines (K lines match no template)
  the bundle loads: the models, the rules and the decisions belong together (the processor's own loader accepted them)

Next: make local-replay   (the same detectors on this file, without a cluster)
```

A few lines may differ from `messages.parquet` (Drain's tree grows while it reads the log, and the processor uses the finished tree); the script says so and by how many. **More than 2% is a warning: stop and find out why**, because `drain.pkl` and `messages.parquet` may then come from different runs of `make preprocess`.

### Step 2. The numbers on your computer (`make local-replay`, `make test`)

```bash
cd NLP-pipeline
make local-replay LOCAL_ARGS="--save evidence/local-replay.json"
```

This runs the processor's own detector code (`processor/engine.py`) on `bgl-replay.log`, with dictionaries in place of Kafka and MongoDB, and prints the same table that `make results` prints for the cluster: for each detector, true and false positives and negatives, precision, recall, F1, and LogBERT's milliseconds per window. It also checks that the windows the engine builds are exactly the windows that `logad.data.windows.sliding_windows` of the NLP project builds from the same file. LogBERT scores every window; the NLP project measured about 27 ms per window on 4 CPU cores (156 s for 5,742 windows), and the export prints how many windows the replay file has.

```bash
cd NLP-pipeline
make test
```

About 150 tests, about half a minute, no cluster needed (they use stand-ins for Kafka and MongoDB, and the real models of `NLP/results/`).

### Step 3. The images (push, wait, make public)

The three images (`nlp-producer`, `nlp-processor`, `nlp-restapi`) are built by GitHub, because the models are baked into them.

```bash
cd ~/Desktop/CA
git add NLP-pipeline .github/workflows/nlp-pipeline-images.yml
git status --short
git commit -m "NLP log pipeline: BGL logs and detectors on the CA3 stack"
git push
```

Open the repository on GitHub, then *Actions*, then *NLP pipeline images*: three jobs (producer, processor, restapi) must end green. Before an image is pushed the workflow tries it (the processor image loads the models, the producer image reads the replay file), so an image that cannot do its first step is never pushed. If the workflow stops with "a file of the bundle is missing", run Step 1 again and add `NLP-pipeline/bundle` to the commit.

A package pushed for the first time is **private**, and the cluster pulls without a login. For each of the three packages: GitHub, your profile picture, *Your profile*, *Packages*, the package, *Package settings*, *Change visibility*, *Public*. Then:

```bash
cd NLP-pipeline
make check-images
```

Expected: `RESULT: PASS - all 3 images can be pulled without a login`.

The images are rebuilt when `NLP-pipeline/{producer,processor,restapi,bundle}`, `NLP-pipeline/VERSION`, `NLP/src` or the workflow change, and by hand (*Actions*, *Run workflow*). The tag is the content of `VERSION` and is written in five manifests; `make verify` fails if they disagree.

### Step 4. The cluster (`make cluster-up`, `make up`)

```bash
cd NLP-pipeline
make cluster-up
```

Terraform builds 19 AWS resources (network, firewall, key pair, three `t3.medium` machines), then Ansible installs k3s. The key pair is made by the command. CA3's Terraform took 2 min 28 s for the same resources.

```bash
cd NLP-pipeline
make up
```

This applies the whole stack and waits until every part is running. The Job `bootstrap` makes the 13 passwords and certificates inside the cluster; nothing is created on your computer. The processor needs a minute or two after its pod starts: it loads PyTorch and the models before it says "ready". The two exporters can restart once or twice while Kafka and MongoDB start; that is normal. Expected, at the end: `Stack is up. Next:  make smoke ...`.

### Step 5. One line through everything (`make smoke`)

```bash
cd NLP-pipeline
make smoke SMOKE_ARGS="--save evidence/smoke-response.json"
```

A Job sends one tagged log line through producer, Kafka, processor, MongoDB and REST API. Expected: `RESULT: PASS - line smoke-... went producer -> Kafka ... -> processor (detectors) -> MongoDB -> REST API in N s`. The line is classified like every other line (Drain template, keyword rules, saved LLM decision), so a pass also shows that the models are in the image and agree with each other. `make logs-trace TRACE=<id>` follows the same id through the logs of the programs.

### Step 6. The replay (`make replay`)

```bash
cd NLP-pipeline
make replay RESULTS_ARGS="--windows 10 --compare evidence/local-replay.json --json evidence/cluster-summary.json"
```

The producer sends the 30,000 lines (about a minute), then the processor scores the windows (minutes; LogBERT needs a fraction of a second per window), and `make replay` waits for it. It prints the table of the detectors, the first 10 windows that hold an alert line with what each detector said about them, the NLP project's own numbers for comparison, and two checks:

- the cluster built exactly the windows that the NLP code expects from the file (`bundle/replay-info.json`);
- the cluster and your computer give the same numbers for every detector. Keyword rules and saved LLM decisions must be identical. LogBERT can differ by a window or two between a Mac (ARM) and the cluster (x86), because rounding can move a masked key across the top-g line; the script then says so and does not call it a failure.

It ends with `RESULT: PASS - the replay run-... is complete`. `make results` prints the same again at any time, for the newest replay or one named with `make results RESULTS_ARGS="--run run-..."`.

### Kill the processor during a replay

This is the check of the restart design: if the processor dies in the middle of a replay, the finished result must be the same as without the crash. Open two terminal windows.

Window 1:

```bash
cd NLP-pipeline
make replay RESULTS_ARGS="--compare evidence/local-replay.json"
```

Window 2, as soon as window 1 says that the producer is sending (within the first 30 seconds):

```bash
cd NLP-pipeline
KUBECONFIG=.secrets/kubeconfig.yaml kubectl -n nlp delete pod -l app.kubernetes.io/name=processor
```

Kubernetes starts a new processor pod (the Deployment uses `Recreate`, so there is never a moment with two). It needs a minute or two to load the models. The producer keeps sending, and Kafka holds the lines. The new processor continues where the old one stopped: it commits a Kafka offset only after the documents are written, and never past the oldest line that an open window still needs, so Kafka hands those lines out again; the documents have fixed ids, so writing them a second time changes nothing; and LogBERT's masks are seeded by the position of the chunk in the window, so a window scored again gets the same answer. Expected: window 1 ends with the same numbers as the replay without the crash, no window missing and none twice. `processor_redelivered_lines_total` (Prometheus, and *delivered twice (skipped)* in the panel *Lines that needed care* of the dashboard) counts the lines that Kafka handed out again after a restart and that the processor skipped.

`tests/test_restart.py` crashes the processor at every possible place against fakes of Kafka and MongoDB and compares the result with an uninterrupted run. That proves the logic. The check above is the same thing on the real cluster.

### Step 7. The checks

Run them after `make replay`, in this order, from the folder `NLP-pipeline`:

| Command | What it shows | Ends with |
|---|---|---|
| `make rest-check` | The REST API answers from this computer on all three nodes, HTTPS only, login required | `RESULT: PASS - 3 of 3 nodes answered correctly ...` |
| `make metrics-check` | Prometheus, Loki and Grafana receive the numbers and the logs, and the dashboard is loaded | `RESULT: PASS - N of N monitoring checks passed` |
| `make netpol-check` | 169 real connections (13 kinds of pod, 13 targets) behave as the NetworkPolicies say | `RESULT: PASS - all 169 connections behave as the policies say` |
| `make netpol-control` | The same test with the policies deleted, then put back: proves the policies do the blocking | `RESULT: PASS - without the policies every connection worked ...` |
| `make heal-check` | Delete a REST pod, MongoDB and Kafka, and stop and start the processor, one at a time; each comes back and no data is lost. Do not run it during a replay | `RESULT: PASS - 4 of 4 recovery tests passed` |
| `make exposure-check` | Only the intended ports of the machines answer from this computer | `RESULT: PASS - on all 3 nodes only ... answer` |
| `make rbac-check` | What each service account may and may not do | ten lines of yes and no |

Look at the dashboard with `make grafana-login` (prints the user and the password; do not paste them anywhere) and, in a second terminal window that stays open, `make grafana`, then open http://localhost:3000.

### Step 8. Take everything away (`make teardown`)

```bash
cd NLP-pipeline
make teardown
```

It deletes the stack, destroys the machines and the network, and then asks AWS whether anything tagged `Project=CS5287-NLP` or anything that costs money is left. It ends with `RESULT: CLEAN` or `RESULT: LEFTOVERS FOUND`. **A running cluster costs money by the hour** (three `t3.medium` machines, about $0.15 an hour together), so run it when you are done. It does not touch the CA3 cluster, which has its own tag and its own Terraform state.

## Calling the API

The REST API is the one published part of the stack: HTTPS on port 30080 of any of the three machines, from the administrator's address only. Every address except the two probes needs a login.

| Endpoint | Answer |
|---|---|
| `GET /health`, `GET /ready` | Probes, no login. `/ready` says "ready" when MongoDB answers |
| `GET /stats` | How many documents each collection (`events`, `windows`, `runs`) holds |
| `GET /runs` | The replays, newest first, with status and number of lines and windows |
| `GET /summary?run=` | For each detector: true and false positives and negatives, precision, recall, F1, false-positive rate, on the windows of a run; LogBERT's time per window |
| `GET /windows?run=&flagged=&limit=&skip=` | The windows of a run in time order. `flagged=truth`, `logbert`, `llm` or `keyword` keeps those that have that verdict 1 |
| `GET /events?run=&alert=1&limit=&skip=` | The log lines of a run in order. `alert=1` keeps the lines that carry an alert tag |
| `GET /alerts` | Up to 50 log lines with an alert tag, from the latest run |
| `GET /events/<trace id>` | The stored line of one smoke test (`404` with a JSON body when there is none) |

`run` is optional everywhere: without it the newest run is used. The certificate is signed by the private CA of the stack and carries the name `restapi.nlp.test`, so a call to a machine's address uses `--resolve`. `make smoke` prints the exact command with the address filled in. `USER` and `PASSWORD` are the two values of the Secret `restapi-login`:

```bash
cd NLP-pipeline
KUBECONFIG=.secrets/kubeconfig.yaml kubectl -n nlp get secret pki-ca -o jsonpath='{.data.ca\.crt}' | base64 --decode > ca.crt
curl -s --cacert ca.crt --resolve restapi.nlp.test:30080:<node-address> -u USER:PASSWORD https://restapi.nlp.test:30080/summary
```

`make rest-check` and `make results` read the login from the Secret by themselves and never print the password. The REST API reaches MongoDB as an account that may only **read** the database `nlp`, so even a flaw in the program cannot write or delete data.

## What the dashboard shows

`make grafana` opens a dashboard of 27 charts in eight rows:

| Row | What is in it |
|---|---|
| *Right now* | Is a replay running, lines sent per second, lines waiting in Kafka, lines stored per second |
| *The replay (producer)* | Lines sent per second, how long the producer waits for Kafka |
| *Kafka and the processor* | Consumer lag, lines per second, the time from send to stored, how long the processor waits for MongoDB |
| *The detectors* | Windows scored per minute, windows flagged per minute by each of the three detectors, LogBERT's seconds per window, *Lines that needed care* (no template known, too late for any window, out of order, delivered twice, not a valid message), log lines held in memory by open windows |
| *MongoDB* | Database writes per second, open connections |
| *REST API* | Requests per second, errors per second, how long an answer takes |
| *Is everything alive?* | Does Prometheus read every program, is the processor connected, seconds since the last stored line, producer send errors |
| *Logs (Loki)* | Log lines per second by program, errors and warnings of all programs, and *Follow one log line*: the box *Trace ID* at the top searches the logs of every program for one id |

During a replay the log's own clock runs faster than the wall clock: the producer sends 500 lines per second, and the full BGL log has 4.7 million lines over about seven months, far fewer than one line per second on average. So the panels show a replay as a burst, and the windows are scored much faster than they happened.

## Why it is built this way

| Choice | Reason |
|---|---|
| One topic partition, one processor pod, strategy `Recreate`, no autoscaler | A window is made of lines that follow each other in time. A partition keeps its order, one reader keeps one window in one place, and `Recreate` guarantees there is never a second processor. A second pod in the same consumer group would get no partition anyway |
| Windows are cut by the **time of the log lines**, not by the clock | The NLP project evaluated on windows of event time (5 minutes, sliding by 1 minute). The processor's `Windower` gives exactly those windows (`tests/test_windower.py` compares them with `logad.data.windows.sliding_windows` on random logs) |
| Offsets committed by hand, after the documents are written; documents with fixed ids | A crash can repeat work but cannot lose or duplicate results |
| The LLM's **saved decisions**, not the LLM | The decisions are per template and were made once (`results/rule_guided/`). Running a 7B model in the cluster would need a GPU or minutes per template, and would not change the verdicts |
| The models are baked into the processor image (`/bundle`) | The pod needs no volume and no download, and the image and the models cannot drift apart. The cost: a new export needs a new image |
| LogBERT scores a window in chunks of 16 masked copies (`SCORE_BATCH`) | The library's default (128) needed about 2 GB for a window of 40,000 lines; 16 keeps the memory flat (measured about 900 MB for a window of 100,000 lines on a test machine) |
| Lines whose template Drain does not know are counted, not dropped | They get no keyword or LLM verdict, they still count for LogBERT (as an unknown key), and `processor_unseen_lines_total` shows how many there were |
| Images are built by GitHub for amd64 only | The machines are amd64, and nothing here runs the images on a Mac. PyTorch under emulation would be very slow |

## Security

Everything that CA3 had is still there, with the same manifests and the same code apart from names: mutual TLS for Kafka (the client certificate decides who may connect), TLS for MongoDB (`requireTLS`) with one account per program and the least rights each needs, HTTPS and a login for the REST API, 13 Secrets made inside the cluster by the Job `bootstrap` (no password or key on your computer or in the repository), Pod Security `restricted` on the namespace, 25 NetworkPolicies (one that denies everything and 24 that allow the connections that are needed), and service accounts with the smallest roles (the processor and the producer cannot read Secrets; only `bootstrap` can create them). The k3s setting that encrypts Secrets in the cluster database is the same as in CA3. **It was not checked again for this cluster.**

What it does not cover (the same list as CA3, which also says why):

- No rotation of passwords or certificates while the data stays; the only way to new ones is `make down` and `make up`.
- No Kafka ACLs: every client with a certificate signed by the CA can use every topic (there is one).
- MongoDB clients use passwords, not certificates. One Secret value (the login of the MongoDB metrics reader) is in an environment variable; every other Secret is a read-only file.
- Monitoring traffic is plain HTTP inside the cluster. Grafana and Prometheus are not published; they are reached with `kubectl port-forward`.
- The root file system of the containers is writable, and there is no admission policy beyond Pod Security.
- The NetworkPolicies trust labels: someone who can create pods in the namespace can give a pod a trusted label. Two policy files name the machines' subnet (`10.20.1.0/24`) and the address of the Kubernetes API Service (`10.43.0.1/32`), so they are not portable to another network.

## Known limitations

1. **The replay is a slice, not the whole test period.** 30,000 lines of a test period of millions. The numbers are a consistency check between the laptop and the cluster, and they differ from the report's numbers. They do not re-evaluate the detectors.
2. **The `llm` detector is a table lookup.** It reproduces condition B3 only for the templates that the NLP run classified. In the `bgl_cpu` run those are the 472 templates of the test period, which covers every line of the replay file, because the file is a slice of that period. A line whose template Drain does not know has no decision and counts as "not flagged"; the table that `make results` prints says how many lines that was.
3. **LogBERT in the cluster is not bit-for-bit the offline evaluation.** In the NLP project's evaluation all chunks of all windows go through one call, and the random mask of a chunk is seeded by `(seed, the number of the chunk in that call)`. The processor scores one window per call, so a chunk is numbered inside its window and gets another random mask. Model, g, r, mask ratio and seed are the same, but the verdicts of some windows can differ from the report. The check that counts is `make results` with `--compare`, which compares the cluster with the same program on your computer.
4. **One processor, no scaling.** The speed of a replay is set by LogBERT on two CPUs per pod and by `LINES_PER_SECOND` (500). A bigger replay file takes proportionally longer, and there is no way to add a second reader.
5. **One Kafka broker and one MongoDB, no replication.** Their disks are `local-path` volumes on one machine: a pod can be replaced without losing data, a machine cannot. `make heal-check` deletes pods, not machines.
6. **Nothing on the cluster side had been run on a cluster when this was written (13 November 2026).** It is described from the files and checked by tests with fakes. `evidence/` holds the output of whatever was run since (see *Testing and verification*).
7. **Terraform's state is local** (`infra/terraform/`, ignored by Git) and holds the administrator's address. If it is lost, `terraform destroy` cannot find the resources; the cleanup scan asks AWS and not the state file, so it still can.
8. **Only a Mac was the operator's computer** in CA3; Linux and Windows were not tried.

## Testing and verification

The state when this was written, 13 November 2026. What was run later is in `evidence/`, with its output.

| What | How | Where it was run |
|---|---|---|
| The programs: windows, restart after a crash at every point, sink, producer, REST API, results tables, export of the bundle, the image workflow, the dashboard, the consistency checks | `make test`: 156 tests, about 25 seconds | Claude's sandbox, with fakes for Kafka and MongoDB. **Run it on your computer too (Step 2)** |
| The files agree with each other (images and tags, ports, settings, Secrets, NetworkPolicies against Services, Prometheus targets, rights, names used by the scripts, evidence free of addresses) | `make verify`: 13 checks, each shown to fail on 25 deliberate mistakes | Claude's sandbox, with the built-in manifest reader (no `kubectl` there). **Run it on your computer: it then uses `kubectl kustomize`** |
| The detectors on the replay file | `make local-replay` | Not yet: needs your `data/processed/bgl` |
| Manifests against the Kubernetes schemas | `make validate` (needs `kubeconform`) | Not yet |
| Images, cluster, stack, smoke test, replay, restart under a replay, REST API, monitoring, NetworkPolicies, healing, teardown | Steps 3 to 8 | **Not run yet.** Save the output in `evidence/` as you go (`evidence/README.md` says how) |

**Not tested** (at the time of writing): your real `drain.pkl` and `messages.parquet` (the export script's last check tells you if they belong together); the `.dockerignore` allowlists under the real image builder; the GitHub workflow; the CPU PyTorch image; a real Kafka, MongoDB and k3s; whether MongoDB counts the processor's upserts as `update` operations (the dashboard and `make metrics-check` count `insert|update`); whether Prometheus finds every number the dashboard asks for (`make metrics-check` checks it); LogBERT's verdicts on ARM against x86; the speed of a replay; and everything under *Known limitations*.

## If something does not work

| If this happens | Do this |
|---|---|
| `make export-bundle` says `drain.pkl ... was not found` | The processed data is not on this computer. Run `make data` and `make preprocess` in `NLP`, or copy `NLP/data/processed/bgl` from the computer where you ran them |
| `make export-bundle` stops with "does not belong to the model" | `drain.pkl` and `model.pt` come from different preprocessing runs. Use the pair that was made together: the processed data that you trained on |
| `make export-bundle` says `WARNING: for N of M lines ... Drain's template differs` | More than 2% of the lines get another template than `messages.parquet` has. Do not go on. Run `make preprocess` again and export again |
| The workflow stops with "a file of the bundle is missing" | Run `make export-bundle`, then `git add NLP-pipeline/bundle`, commit and push |
| `make check-images` says an image cannot be pulled | The package is still private. Make it public (Step 3) |
| A pod is in `ImagePullBackOff` | The same: the package is private, or the tag in the manifest is not one that was pushed. `make verify` checks the tags against `VERSION` |
| The cluster runs an old model after you exported again | The image tag did not change, so a node kept its copy. Change `VERSION` and the tag in the five manifests together (`make images-info` lists them), push, and wait for the build |
| `make up` stops with an error | Read the first error. Then `make diagnose` (read-only: states, events, logs). It is safe to run `make up` again |
| The processor pod is `Running` but not ready for minutes | It is loading PyTorch and the models; `make logs` shows it. If it is killed with `OOMKilled`, `make diagnose` says so: the memory limit in `processor/processor.yaml` is too low for the window that it was scoring |
| `make replay` says the producer Job did not finish | Its last log lines are printed above that message. Then `make diagnose` |
| `make replay` waits and then says the replay is not complete | The processor is behind or stopped. `make status` and `make logs`. The numbers shown are those of the windows scored so far; `make results` prints them again later |
| The comparison at the end of `make replay` shows a difference in the keyword or `llm` rows | That must not happen: the same file, the same rules and the same decisions. Keep the output, and check that `make local-replay` used the same `bundle/` that went into the images (the images are built from the repository, `bundle/` on your computer may be newer) |
| The comparison shows a difference of a window or two in `logbert` | Expected between a Mac and the cluster (Step 6) |
| `make scan` after `make cluster-down` finds something | It names it. Delete it in the AWS console, or run `make scan SCAN_ARGS="--all-regions"` if it may be in another region |

## AI assistance

The programs, manifests, tests, scripts and this document of `NLP-pipeline` were written by Claude (Anthropic) in one working session on 13 November 2026, at the request of the student, starting from the student's own CA3 project and NLP project. The NLP project (`../NLP`) and its results are the student's and were not changed. Claude ran the tests and the consistency checks in a sandbox that has no Docker, Kafka, MongoDB, Kubernetes or AWS; **Claude ran nothing on a cluster**. What was run on a real machine is in `evidence/`, and what is not there was not run.

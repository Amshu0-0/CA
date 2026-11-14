# The NLP log pipeline: the CA3 cluster, with the NLP project's BGL system-log data and detectors in the place of the network flows.
# Commands build the cluster and the stack, send the log file through it, and show what the detectors said.
#
#   Before the cluster (on your computer):
#   make test          run the tests of the programs (no cluster, no Kafka, no MongoDB needed)
#   make export-bundle collect the trained models and the log file to replay from your NLP project into bundle/ (README, Step 1)
#   make local-replay  run the SAME detectors on the replay file here, without a cluster: the numbers the cluster must reproduce
#   make check-images  check that the three images on ghcr.io can be pulled without a login (what the cluster does)
#
#   On the cluster:
#   make cluster-up    Terraform builds the network and 3 machines, Ansible installs Kubernetes (k3s) on them
#   make up            apply the stack (kubectl apply -k .), wait until it is running. The passwords and certificates are made inside the cluster
#   make smoke         send ONE tagged log line through producer, Kafka, processor, MongoDB and REST API
#   make replay        send the whole replay file through the pipeline, wait for the processor, and print what each detector said
#   make results       print what each detector said about the newest replay (it can be run again at any time)
#   make rest-check    call the REST API from this computer (HTTPS, with the login), through every node
#
#   Afterwards:
#   make down          delete the stack, including the disks and the Secrets
#   make cluster-down  destroy the machines and the network
#   make scan          look in AWS for anything left from this project (after cluster-down it must find nothing)
#   make teardown      make down, make cluster-down, then make scan: everything removed, and the proof
#
#   More checks:
#   make dry-run       ask the cluster what the next "make up" would change, without changing anything
#   make validate      check every manifest against the Kubernetes schemas (needs no cluster)
#   make verify        check that manifests, programs and cluster code agree with each other (needs no cluster)
#   make netpol-check  test what each kind of pod can and cannot reach (the NetworkPolicy test)
#   make crash-test    kill the processor in the middle of a replay and check that the finished result is the same
#   make heal-check    delete pods on purpose and check that Kubernetes brings them back with their data
#   make metrics-check ask Prometheus, Loki and Grafana if the numbers and the logs arrive and the dashboard is loaded
#   make logs-trace    follow one trace id through the logs of every program (TRACE=<id>, the smoke test prints one)
#   make grafana       open the dashboard at http://localhost:3000 (log in with: make grafana-login)
#   make prometheus    open Prometheus at http://localhost:9090
#
# Needs: terraform, ansible, kubectl, bash and ssh-keygen, plus AWS credentials, for the cluster targets;
# kubeconform for "make validate"; python3 with PyYAML for "make verify"; the Python environment of your NLP project (logad, torch,
# pytest, pymongo, kafka-python, flask) for "make test", "make export-bundle" and "make local-replay".
# Every recipe sits on its rule line after a semicolon, so this file has no TAB characters.

NS        ?= nlp
KUBECTL   ?= kubectl
TERRAFORM ?= terraform
PYTHON    ?= python3
TF         = $(TERRAFORM) -chdir=infra/terraform

# Plain text from Terraform: no colour codes. Saved evidence stays readable and grep can find the lines.
TF_CLI_ARGS ?= -no-color
export TF_CLI_ARGS

# Extra options for the scripts, for example:  make smoke SMOKE_ARGS="--save evidence/smoke-response.json"
SMOKE_ARGS   ?=
HEAL_ARGS    ?=
REST_ARGS    ?=
METRICS_ARGS ?=
RESULTS_ARGS ?=
BUNDLE_ARGS  ?=
NLP_CONFIG   ?= configs/bgl_cpu.yaml
LOCAL_ARGS   ?=
TRACE        ?=
SCAN_ARGS    ?=
CRASH_ARGS   ?=
FILES        ?=

# How long "make replay" waits for the producer to finish, and then for the processor to finish the windows (seconds).
REPLAY_TIMEOUT ?= 900
RESULTS_WAIT   ?= 1800

# The stack targets use the cluster that "make cluster-up" built (its kubeconfig lives in .secrets/).
# To use another cluster instead, run for example:  make up KUBECONFIG=$HOME/.kube/config
# A KUBECONFIG that is left over in your terminal is ignored on purpose: a second copy of this repository must use its own cluster.
KUBECONFIG = $(CURDIR)/.secrets/kubeconfig.yaml
export KUBECONFIG

.DEFAULT_GOAL := help
.PHONY: help validate verify test export-bundle local-replay check-images cluster-key cluster-check cluster-plan cluster-up cluster-status cluster-down \
    context up status get-all logs diagnose rbac-check rest-check exposure-check images-info down \
    smoke replay results crash-test netpol-check netpol-control heal-check scan scrub teardown dry-run \
    metrics-check logs-trace grafana grafana-login prometheus

help: ; @printf '%s\n' \
    "  Before the cluster (on your computer, in the Python environment of your NLP project)" \
    "  make test            run the tests of the programs (no cluster needed)" \
    "  make export-bundle   collect the trained models and the replay file from your NLP project into bundle/ (README, Step 1)" \
    "  make local-replay    run the same detectors on the replay file here: the numbers that the cluster must reproduce" \
    "  make check-images    check that the three images on ghcr.io can be pulled without a login" \
    "" \
    "  Cluster (AWS)" \
    "  make cluster-up      build the network and 3 machines (Terraform), then install Kubernetes (Ansible)" \
    "  make cluster-plan    show what Terraform would change (after cluster-up the answer must be: No changes)" \
    "  make cluster-check   static checks of the cluster code: terraform fmt and validate, ansible syntax" \
    "  make cluster-status  show the nodes, all pods, and node CPU and memory" \
    "  make cluster-down    destroy the machines and the network" \
    "  make scan            look in AWS for anything left from this project (after cluster-down it must find nothing)" \
    "  make teardown        make down, then cluster-down, then scan: remove everything and prove it" \
    "" \
    "  Stack (on the cluster)" \
    "  make up              apply the whole stack (the passwords and certificates are made inside the cluster) and wait until it is running" \
    "  make smoke           send one tagged log line through producer, Kafka, processor, MongoDB and REST API" \
    "  make replay          send the whole replay file through the pipeline, wait for the processor, and print what each detector said" \
    "  make results         print what each detector said about the newest replay (more: make results RESULTS_ARGS=\"--windows 10\")" \
    "  make rest-check      call the REST API from this computer (HTTPS, with the login), through the node port of every node" \
    "  make dry-run         ask the cluster what the next make up would change, without changing anything (after make up: all unchanged)" \
    "  make status          show everything in the namespace" \
    "  make get-all         show everything in every namespace (kubectl get all -A)" \
    "  make logs            show the processor logs" \
    "  make diagnose        explain why a pod is not ready: states, events and logs (read-only)" \
    "  make exposure-check  show which ports of the nodes answer from this computer" \
    "  make rbac-check      prove the access rules with kubectl auth can-i" \
    "  make crash-test      kill the processor in the middle of a replay and check that nothing is lost or changed (about 4 minutes)" \
    "  make netpol-check    test what each kind of pod can reach (169 connections, 156 when no replay runs; compared with the policies)" \
    "  make netpol-control  the same test, then again with the policies deleted: proves the policies do the blocking" \
    "  make heal-check      delete pods on purpose and check that they come back with their data (run it when no replay is running)" \
    "  make metrics-check   ask Prometheus, Loki and Grafana if the numbers and logs arrive and the dashboard is loaded (run it after make replay)" \
    "  make logs-trace      follow one trace id through the logs of every program:  make logs-trace TRACE=smoke-1113-170000-ab12" \
    "  make grafana         open the dashboard at http://localhost:3000 (leave the window open; log in with: make grafana-login)" \
    "  make grafana-login   print the Grafana user name and password (they live in the Secret grafana-admin)" \
    "  make prometheus      open Prometheus at http://localhost:9090 (leave the window open)" \
    "  make down            delete everything: workloads, disks, Secrets, namespace" \
    "  make scrub           remove public addresses, host names and account numbers from saved evidence files:  make scrub FILES=\"evidence/a.txt evidence/b.txt\"" \
    "" \
    "  Checks that need no cluster" \
    "  make validate        check every manifest against the Kubernetes schemas" \
    "  make verify          check manifests, programs and cluster code against each other" \
    "  make images-info     list every container image the manifests use"

# ---------------------------------------------------------------- before the cluster

# The tests start no Kafka, no MongoDB and no Kubernetes: they use stand-ins, and the real models of your NLP project (the committed
# NLP/results/ next to this folder) for the tests that need them. About 150 tests, about half a minute. Run it from this folder, in a
# Python environment that has the packages of requirements-dev.txt (pip install -r requirements-dev.txt).
test: ; $(PYTHON) -m pytest tests -q

# Collects, from your NLP project, everything the processor and the producer need into bundle/: drain.pkl, model.pt, rules.json,
# llm_rule_based_decisions.csv, settings.json and the replay file bgl-replay.log. Run it in the environment where "make preprocess" and
# the training of your NLP project were done. The committed results of your NLP project come from configs/bgl_cpu.yaml (see
# NLP/results/README.md), so that is the configuration used here. For a run that you made with another one:
#   make export-bundle NLP_CONFIG=configs/bgl.yaml BUNDLE_ARGS="--prefer-outputs"
# The other options are explained at the top of scripts/export-nlp-bundle.py, for example:
#   make export-bundle BUNDLE_ARGS="--nlp-root ../NLP --lines 30000"
export-bundle: ; $(PYTHON) scripts/export-nlp-bundle.py --config $(NLP_CONFIG) $(BUNDLE_ARGS)

# The same detectors, the same windows, the same documents as the processor in the cluster, with the replay file as input and dictionaries
# instead of Kafka and MongoDB. It prints the table that "make results" prints for the cluster; the two must show the same numbers.
local-replay: ; $(PYTHON) scripts/local-replay.py $(LOCAL_ARGS)

# The cluster pulls the three images from ghcr.io without a login, so the packages must be public (README, Step 3).
check-images: ; python3 scripts/check-images.py

# ---------------------------------------------------------------- the cluster

cluster-key: ; bash scripts/make-key.sh

cluster-check: cluster-key ; $(TF) init -input=false > /dev/null && $(TF) fmt -check -recursive && $(TF) validate && \
    cd infra/ansible && ansible-playbook -i syntax-inventory.ini --syntax-check site.yml

cluster-plan: cluster-key ; $(TF) init -input=false > /dev/null && $(TF) plan -input=false

cluster-up: cluster-key ; $(TF) init -input=false > /dev/null && \
    $(TF) apply -input=false -auto-approve && \
    $(TF) output -raw ansible_inventory > infra/ansible/inventory.ini && \
    cd infra/ansible && ansible-playbook site.yml && \
    echo "Cluster is up. Next: make up   (the kubeconfig is $(KUBECONFIG))"

cluster-status: ; $(KUBECTL) get nodes -o wide && $(KUBECTL) get pods -A && \
    ($(KUBECTL) top nodes || echo "(metrics-server needs about a minute after the cluster starts; run: kubectl top nodes)")

cluster-down: ; $(TF) init -input=false > /dev/null && $(TF) destroy -input=false -auto-approve && \
    rm -f infra/ansible/inventory.ini infra/ansible/known_hosts "$(CURDIR)/.secrets/kubeconfig.yaml" && \
    echo "Cluster destroyed."

# Looks in AWS for anything that is left from this project: everything tagged Project=<project_tag> (infra/config.yml), and everything that costs money.
# Exit code 0 = nothing is left. Options, for example:  make scan SCAN_ARGS="--all-regions"
scan: ; python3 scripts/cleanup-scan.py $(SCAN_ARGS)

# Everything away, and the proof. The stack is deleted only if the cluster still answers; the machines and the network are always destroyed;
# then the scan waits up to 3 minutes for AWS's tag index to catch up. The command fails if anything is left.
teardown: ; if $(KUBECTL) --request-timeout=10s get namespace kube-system > /dev/null 2>&1; then \
        $(MAKE) --no-print-directory down || echo "make down did not finish. The machines are destroyed next, and that removes the stack with them."; \
        echo; echo "What is left inside Kubernetes (no nlp namespace, no volumes):"; $(KUBECTL) get namespace; $(KUBECTL) get pv; echo; \
    else echo "The cluster does not answer, so there is no stack to delete: going on to destroy the machines."; fi ; \
    $(MAKE) --no-print-directory cluster-down && python3 scripts/cleanup-scan.py --wait 180 $(SCAN_ARGS)

# Asks the cluster's API server what "kubectl apply -k ." would change, and changes nothing (a server-side dry run: the cluster's own
# validation and admission rules run, including Pod Security). Run it after "make up": every object must say "unchanged".
# "make dry-run DRY_RUN=client" is the form that the assignment's tip names: kubectl validates and prints, and sends nothing.
DRY_RUN ?= server
dry-run: context ; $(KUBECTL) apply -k . --dry-run=$(DRY_RUN)

# ---------------------------------------------------------------- checks that need no cluster

validate: ; $(KUBECTL) kustomize . | kubeconform -strict -summary - && \
    kubeconform -strict -summary producer/job-replay.yaml producer/smoke

verify: ; python3 scripts/verify.py

images-info: ; @grep -rhoE 'image: [^ ]+' --include='*.yaml' . | sort | uniq -c | sort -rn

# ---------------------------------------------------------------- the stack

context: ; @if [ ! -f "$(KUBECONFIG)" ]; then echo "There is no cluster yet: $(KUBECONFIG) does not exist. Run:  make cluster-up"; exit 1; fi ; \
    echo "kubectl context: $$($(KUBECTL) config current-context)   (kubeconfig: $(KUBECONFIG))"

# Nothing has to be prepared before this: the Job "bootstrap" in the stack makes the passwords and certificates (as Secrets) inside the cluster.
# The pods that need them start by themselves a short while after the Job ends. The two exporters can restart once or twice while Kafka and
# MongoDB are still starting (an exporter that cannot reach its target at the start stops, and Kubernetes starts it again); that is normal.
# The processor needs a minute or two: its image holds PyTorch and the trained models, and it loads them before it says "ready".
# A Job's pod template cannot be changed once it exists, so if
# kubectl refuses to apply a changed bootstrap Job, the finished Job is deleted and the stack applied again (the Job then finds all 13 Secrets
# and changes nothing).
# The replay is not part of this: "make replay" starts it, when you want the data to go through.
up: context ; ( $(KUBECTL) apply -k . || { echo "kubectl reported an error. If it says that the Job bootstrap cannot be changed, the finished Job is replaced and the stack applied again:" ; \
      $(KUBECTL) -n $(NS) delete job bootstrap --ignore-not-found && $(KUBECTL) apply -k . ; } ) && \
    ( $(KUBECTL) -n $(NS) wait --for=condition=complete job/bootstrap --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status statefulset/mongo --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status statefulset/kafka --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status deployment/processor --timeout=600s && \
      $(KUBECTL) -n $(NS) rollout status deployment/restapi --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/kafka-exporter --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status deployment/mongodb-exporter --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status deployment/prometheus --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/loki --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status daemonset/alloy --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/grafana --timeout=300s && \
      echo "Stack is up. Next:  make smoke   (one log line through everything),  then  make replay   (the whole file)" ) || \
    { echo "The stack is not up yet. Read the first error above, then run:  make diagnose   (it is safe to run make up again)"; exit 1; }

status: ; $(KUBECTL) -n $(NS) get all,pvc,configmap,secret,networkpolicy,serviceaccount,role,rolebinding -o wide

get-all: ; $(KUBECTL) get all -A

logs: ; $(KUBECTL) -n $(NS) logs deployment/processor --tail=40

diagnose: ; NS=$(NS) KUBECTL=$(KUBECTL) bash scripts/diagnose.sh

rest-check: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/rest-check.py $(REST_ARGS)

exposure-check: ; @echo "kubectl get svc -A --field-selector spec.type!=ClusterIP     (every Service that is reachable from outside the cluster)" ; \
    $(KUBECTL) get svc -A --field-selector 'spec.type!=ClusterIP' && echo && \
    TERRAFORM=$(TERRAFORM) python3 scripts/exposure-check.py

rbac-check: ; @echo "pipeline-viewer   can list pods?       $$($(KUBECTL) auth can-i list pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-viewer   can delete pods?     $$($(KUBECTL) auth can-i delete pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-operator can start a replay?  $$($(KUBECTL) auth can-i create jobs.batch --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "pipeline-operator can scale the API?   $$($(KUBECTL) auth can-i patch deployments.apps --subresource=scale --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "pipeline-operator can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "processor pod     can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):processor -n $(NS))" ; \
    echo "bootstrap Job     can create secrets?  $$($(KUBECTL) auth can-i create secrets --as=system:serviceaccount:$(NS):bootstrap -n $(NS))" ; \
    echo "bootstrap Job     can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):bootstrap -n $(NS))" ; \
    echo "bootstrap Job     can change secrets?  $$($(KUBECTL) auth can-i update secrets --as=system:serviceaccount:$(NS):bootstrap -n $(NS))" ; \
    echo "bootstrap Job     can delete pods?     $$($(KUBECTL) auth can-i delete pods --as=system:serviceaccount:$(NS):bootstrap -n $(NS))"

# The tests need the running stack ("make up"). Whatever they create is removed again when they end.
smoke: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/smoke-test.py $(SMOKE_ARGS)

# The replay: the old Job is deleted (a Job cannot be run twice), the Job in producer/job-replay.yaml is applied, and the command waits until the
# producer has sent every line of the file and the end marker. Then results.py waits until the processor has scored the last window of THIS
# replay (it finds the replay's name in the Job's log) and prints the table. Nothing is deleted from the database: every replay has its own name.
replay: context ; $(KUBECTL) -n $(NS) delete job producer-replay --ignore-not-found --wait=true && \
    $(KUBECTL) apply -f producer/job-replay.yaml && \
    echo "The producer is sending the replay file to Kafka (about a minute). Waiting for it..." && \
    ( $(KUBECTL) -n $(NS) wait --for=condition=complete job/producer-replay --timeout=$(REPLAY_TIMEOUT)s || \
      { echo "The producer Job did not finish. Its log:"; $(KUBECTL) -n $(NS) logs job/producer-replay -c producer --tail=15; echo "Run:  make diagnose"; exit 1; } ) && \
    echo "All lines are in Kafka. The processor is scoring the windows now (minutes). Waiting for it..." && \
    NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/results.py --replay-job --wait $(RESULTS_WAIT) $(RESULTS_ARGS)

# What each detector said about the newest replay (or the one named with --run). It needs the stack and the REST API, and changes nothing.
results: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/results.py $(RESULTS_ARGS)

# The kill test: starts a replay, kills the processor when it has stored part of it, waits for the new pod and the end of the replay, and compares.
# A replay is over in about a minute and a half, so the kill is made by the program (see the top of scripts/crash-test.py).
crash-test: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/crash-test.py $(CRASH_ARGS)

# The producer is a Job, so its metrics port exists only during a replay: without a producer the table has 156 connections, not 169.
netpol-check: ; NS=$(NS) KUBECTL=$(KUBECTL) python3 scripts/netpol-check.py

netpol-control: ; NS=$(NS) KUBECTL=$(KUBECTL) python3 scripts/netpol-check.py --control

heal-check: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/heal-check.py $(HEAL_ARGS)

# The monitoring tools answer only inside the cluster, so these ask from inside their pods (kubectl exec ... wget http://localhost:...).
# Nothing is changed. The Grafana password is read inside the pod and never printed.
metrics-check: ; NS=$(NS) KUBECTL=$(KUBECTL) python3 scripts/metrics-check.py $(METRICS_ARGS)

logs-trace: ; NS=$(NS) KUBECTL=$(KUBECTL) python3 scripts/metrics-check.py --trace "$(TRACE)" $(METRICS_ARGS)

# To look at Grafana or Prometheus in your browser: kubectl port-forward goes through the Kubernetes API (no node port, no firewall hole).
# It runs until you press Ctrl-C, so it needs its own terminal window.
grafana: context ; @echo "Grafana: open http://localhost:3000   Log in with the user and password from:  make grafana-login   (stop with Ctrl-C)" ; \
    $(KUBECTL) -n $(NS) port-forward service/grafana 3000:3000

prometheus: context ; @echo "Prometheus: open http://localhost:9090   (stop with Ctrl-C)" ; \
    $(KUBECTL) -n $(NS) port-forward service/prometheus 9090:9090

grafana-login: ; @echo "user:     $$($(KUBECTL) -n $(NS) get secret grafana-admin -o jsonpath='{.data.username}' | base64 --decode)" ; \
    echo "password: $$($(KUBECTL) -n $(NS) get secret grafana-admin -o jsonpath='{.data.password}' | base64 --decode)"

# Evidence files are published with the repository. This rewrites the files that you name, in place, without public addresses, EC2 host names,
# 12-digit account numbers and home-folder paths (scripts/scrub-evidence.py says what it replaces; "make verify" checks the result).
scrub: ; python3 scripts/scrub-evidence.py $(FILES)

down: ; $(KUBECTL) delete namespace nlp-intruder --ignore-not-found --wait=false ; $(KUBECTL) delete -k . --ignore-not-found --wait=true && echo "Deleted. Check:  kubectl get namespace $(NS)   (it should say NotFound)"

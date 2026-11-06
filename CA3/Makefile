# CA3: two commands build the cluster and run the stack on it, and two more remove them.
#
#   make cluster-up    Terraform builds the network and 3 machines, Ansible installs Kubernetes (k3s) on them
#   make up            apply the stack (kubectl apply -k .), wait until it is running. The passwords and certificates are made inside the cluster
#   make rest-check    call the REST API from this computer (HTTPS, with the login), through every node
#   make down          delete the stack, including the disks and the Secrets
#   make cluster-down  destroy the machines and the network
#   make scan          look in AWS for anything left from this project (after cluster-down it must find nothing)
#   make teardown      make down, make cluster-down, then make scan: everything removed, and the proof
#   make dry-run       ask the cluster what the next "make up" would change, without changing anything
#   make validate      check every manifest against the Kubernetes schemas (needs no cluster)
#   make verify        check that manifests, programs, cluster code and CA3 requirements agree (needs no cluster)
#   make smoke         send one tagged event through producer, Kafka, processor, MongoDB and REST API
#   make netpol-check  test what each kind of pod can and cannot reach (the NetworkPolicy test)
#   make heal-check    delete pods on purpose and check that Kubernetes brings them back with their data
#   make metrics-check ask Prometheus, Loki and Grafana if the numbers and the logs arrive and the dashboard is loaded
#   make logs-trace    follow one event id through the logs of every program (TRACE=<id>, the smoke test prints one)
#   make grafana       open the dashboard at http://localhost:3000 (log in with: make grafana-login)
#   make prometheus    open Prometheus at http://localhost:9090
#   make scale-test    switch the producer load on, let the autoscalers work, switch it off again, and measure all of it (about 12 minutes)
#   make scale-report  draw the table, the goals and the chart of the last scale-test again (needs no cluster)
#   make goals         list the goals in slo.json; change one with  make goals GOAL_ARGS="scale-up 120 --why 'one sentence'"
#   make drill         delete one pod in each tier (Kafka, processor, MongoDB, REST API, producer) and measure how the platform recovers (about 25 minutes)
#   make drill-report  draw the table, the goals and the chart of the last drill again (needs no cluster)
#   make rest-evidence call every REST endpoint and save request, response, logs and metrics as evidence
#   make operator-steps  do the manual steps of runbook.md once: read the logs, scale the REST API up and back, read the certificate dates
#
# Needs: terraform, ansible, kubectl, bash and ssh-keygen, plus AWS credentials, for the cluster targets;
# kubeconform for "make validate"; python3 with PyYAML for "make verify".
# Every recipe sits on its rule line after a semicolon, so this file has no TAB characters.

NS        ?= ca3
KUBECTL   ?= kubectl
TERRAFORM ?= terraform
TF         = $(TERRAFORM) -chdir=infra/terraform

# Plain text from Terraform: no colour codes. Saved evidence stays readable and grep can find the lines.
TF_CLI_ARGS ?= -no-color
export TF_CLI_ARGS

# Extra options for the test scripts, for example:  make smoke SMOKE_ARGS="--save evidence/step6-smoke-response.json"
SMOKE_ARGS ?=
HEAL_ARGS  ?=
REST_ARGS  ?=
METRICS_ARGS ?=
SCALE_ARGS ?=
GOAL_ARGS ?=
DRILL_ARGS ?=
OPERATOR_ARGS ?=
TRACE      ?=
SCAN_ARGS  ?=
FILES      ?=

# The stack targets use the cluster that "make cluster-up" built (its kubeconfig lives in .secrets/).
# To use another cluster instead, run for example:  make up KUBECONFIG=$HOME/.kube/config
# A KUBECONFIG that is left over in your terminal is ignored on purpose: a second copy of this repository must use its own cluster.
KUBECONFIG = $(CURDIR)/.secrets/kubeconfig.yaml
export KUBECONFIG

.DEFAULT_GOAL := help
.PHONY: help validate verify cluster-key cluster-check cluster-plan cluster-up cluster-status cluster-down \
    context up status get-all logs diagnose rbac-check rest-check exposure-check scale-up scale-down images-info down \
    smoke netpol-check netpol-control heal-check scan scrub teardown dry-run \
    metrics-check logs-trace grafana grafana-login prometheus scale-test scale-report goals drill drill-report rest-evidence operator-steps

help: ; @printf '%s\n' \
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
    "  make dry-run         ask the cluster what the next make up would change, without changing anything (after make up: all unchanged)" \
    "  make status          show everything in the namespace" \
    "  make get-all         show everything in every namespace (kubectl get all -A)" \
    "  make logs            show the processor logs" \
    "  make diagnose        explain why a pod is not ready: states, events and logs (read-only)" \
    "  make rest-check      call the REST API from this computer (HTTPS, with the login), through the node port of every node" \
    "  make exposure-check  show which ports of the nodes answer from this computer" \
    "  make rbac-check      prove the access rules with kubectl auth can-i" \
    "  make smoke           send one tagged event through producer, Kafka, processor, MongoDB and REST API" \
    "  make netpol-check    test what each kind of pod can reach (169 connections, compared with the policies)" \
    "  make netpol-control  the same test, then again with the policies deleted: proves the policies do the blocking" \
    "  make heal-check      delete pods on purpose and check that they come back with their data" \
    "  make metrics-check   ask Prometheus, Loki and Grafana if the numbers and logs arrive and the dashboard is loaded" \
    "  make logs-trace      follow one event id through the logs of every program:  make logs-trace TRACE=smoke-1a2b3c4d" \
    "  make grafana         open the dashboard at http://localhost:3000 (leave the window open; log in with: make grafana-login)" \
    "  make grafana-login   print the Grafana user name and password (they live in the Secret grafana-admin)" \
    "  make prometheus      open Prometheus at http://localhost:9090 (leave the window open)" \
    "  make scale-test      the scaling test: load on with one producer pod, then the autoscaler, then the load off; measures all of it (about 12 minutes)" \
    "  make scale-report    draw the table, the goals and the chart of the last scale-test again (needs no cluster)" \
    "  make goals           list the goals in slo.json (change one:  make goals GOAL_ARGS=\"scale-up 120 --why 'one sentence'\")" \
    "  make drill           the resilience drill: delete one pod in each tier and measure how the platform recovers (about 25 minutes; one tier:  make drill DRILL_ARGS=\"--only restapi\")" \
    "  make drill-report    draw the table, the goals and the chart of the last drill again (needs no cluster)" \
    "  make rest-evidence   call every REST endpoint and save request, response, logs and metrics as evidence (evidence/step7-rest-evidence.txt)" \
    "  make operator-steps  the manual steps of runbook.md, done once: read the logs, scale the REST API up and back, read the certificate dates (about 2 minutes)" \
    "  make scale-up        switch the producer load on by hand (the autoscaler adds producer pods while the CPU is busy)" \
    "  make scale-down      switch the producer load off by hand (the autoscaler shrinks the producers back to one pod)" \
    "  make down            delete everything: workloads, disks, Secrets, namespace" \
    "  make scrub           remove public addresses, host names and account numbers from saved evidence files:  make scrub FILES=\"evidence/a.txt evidence/b.txt\"" \
    "" \
    "  Checks that need no cluster" \
    "  make validate        check every manifest against the Kubernetes schemas" \
    "  make verify          check manifests, programs, cluster code and CA3 requirements against each other" \
    "  make images-info     list every container image the manifests use"

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
        echo; echo "What is left inside Kubernetes (no ca3 namespace, no volumes):"; $(KUBECTL) get namespace; $(KUBECTL) get pv; echo; \
    else echo "The cluster does not answer, so there is no stack to delete: going on to destroy the machines."; fi ; \
    $(MAKE) --no-print-directory cluster-down && python3 scripts/cleanup-scan.py --wait 180 $(SCAN_ARGS)

# Asks the cluster's API server what "kubectl apply -k ." would change, and changes nothing (a server-side dry run: the cluster's own
# validation and admission rules run, including Pod Security). Run it after "make up": every object must say "unchanged".
# "make dry-run DRY_RUN=client" is the form that the assignment's tip names: kubectl validates and prints, and sends nothing.
DRY_RUN ?= server
dry-run: context ; $(KUBECTL) apply -k . --dry-run=$(DRY_RUN)

# ---------------------------------------------------------------- checks that need no cluster

validate: ; $(KUBECTL) kustomize . | kubeconform -strict -summary - && \
    for d in producer/load producer/loadtest producer/loadtest-one; do $(KUBECTL) kustomize $$d | kubeconform -strict -summary - || exit 1; done && \
    kubeconform -strict -summary producer/smoke

verify: ; python3 scripts/verify_manifests.py

images-info: ; @grep -rhoE 'image: [^ ]+' --include='*.yaml' . | sort | uniq -c | sort -rn

# ---------------------------------------------------------------- the stack

context: ; @if [ ! -f "$(KUBECONFIG)" ]; then echo "There is no cluster yet: $(KUBECONFIG) does not exist. Run:  make cluster-up"; exit 1; fi ; \
    echo "kubectl context: $$($(KUBECTL) config current-context)   (kubeconfig: $(KUBECONFIG))"

# Nothing has to be prepared before this: the Job "bootstrap" in the stack makes the passwords and certificates (as Secrets) inside the cluster.
# The pods that need them start by themselves a short while after the Job ends. The two exporters can restart once or twice while Kafka and
# MongoDB are still starting (an exporter that cannot reach its target at the start stops, and Kubernetes starts it again); that is normal.
# A Job's pod template cannot be changed once it exists, so if
# kubectl refuses to apply a changed bootstrap Job, the finished Job is deleted and the stack applied again (the Job then finds all 13 Secrets
# and changes nothing).
up: context ; ( $(KUBECTL) apply -k . || { echo "kubectl reported an error. If it says that the Job bootstrap cannot be changed, the finished Job is replaced and the stack applied again:" ; \
      $(KUBECTL) -n $(NS) delete job bootstrap --ignore-not-found && $(KUBECTL) apply -k . ; } ) && \
    ( $(KUBECTL) -n $(NS) wait --for=condition=complete job/bootstrap --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status statefulset/mongo --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status statefulset/kafka --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status deployment/processor --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/restapi --timeout=300s && \
      $(KUBECTL) -n $(NS) wait --for=condition=complete job/producer-replay --timeout=600s && \
      $(KUBECTL) -n $(NS) rollout status deployment/kafka-exporter --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status deployment/mongodb-exporter --timeout=420s && \
      $(KUBECTL) -n $(NS) rollout status deployment/prometheus --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/loki --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status daemonset/alloy --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/grafana --timeout=300s && \
      echo "Stack is up. Call the REST API from this computer with:  make rest-check   and check the monitoring with:  make metrics-check" ) || \
    { echo "The stack is not up yet. Read the first error above, then run:  make diagnose   (it is safe to run make up again)"; exit 1; }

status: ; $(KUBECTL) -n $(NS) get all,pvc,configmap,secret,hpa,networkpolicy,serviceaccount,role,rolebinding -o wide

get-all: ; $(KUBECTL) get all -A

logs: ; $(KUBECTL) -n $(NS) logs deployment/processor --tail=40

diagnose: ; NS=$(NS) KUBECTL=$(KUBECTL) bash scripts/diagnose.sh

rest-check: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/rest-check.py $(REST_ARGS)

exposure-check: ; @echo "kubectl get svc -A --field-selector spec.type!=ClusterIP     (every Service that is reachable from outside the cluster)" ; \
    $(KUBECTL) get svc -A --field-selector 'spec.type!=ClusterIP' && echo && \
    TERRAFORM=$(TERRAFORM) python3 scripts/exposure-check.py

rbac-check: ; @echo "pipeline-viewer   can list pods?       $$($(KUBECTL) auth can-i list pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-viewer   can delete pods?     $$($(KUBECTL) auth can-i delete pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-operator can scale producers? $$($(KUBECTL) auth can-i patch deployments.apps --subresource=scale --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "pipeline-operator can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "processor pod     can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):processor -n $(NS))" ; \
    echo "bootstrap Job     can create secrets?  $$($(KUBECTL) auth can-i create secrets --as=system:serviceaccount:$(NS):bootstrap -n $(NS))" ; \
    echo "bootstrap Job     can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):bootstrap -n $(NS))" ; \
    echo "bootstrap Job     can change secrets?  $$($(KUBECTL) auth can-i update secrets --as=system:serviceaccount:$(NS):bootstrap -n $(NS))" ; \
    echo "bootstrap Job     can delete pods?     $$($(KUBECTL) auth can-i delete pods --as=system:serviceaccount:$(NS):bootstrap -n $(NS))"

# The tests need the running stack ("make up"). Whatever they create is removed again when they end.
smoke: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/smoke-test.py $(SMOKE_ARGS)

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
# 12-digit account numbers and home-folder paths (scripts/scrub-evidence.py says what it replaces; section 9 of make verify checks the result).
scrub: ; python3 scripts/scrub-evidence.py $(FILES)

# The scaling test switches the load on in two steps (one producer pod, then the autoscaler free), switches it off, and measures the way
# up and the way down. It switches the load off by itself, also when it stops early or you press Ctrl-C. Results go to evidence/step6-scale-*.
# Options, for example:  make scale-test SCALE_ARGS="--before 30 --after 30"   (the whole list:  python3 scripts/scale-test.py --help)
scale-test: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/scale-test.py $(SCALE_ARGS)

# Draws the table, the goals (slo.json) and the chart of the last scale-test again from the saved files. Needs no cluster.
scale-report: ; python3 scripts/scalereport.py $(SCALE_ARGS)

# Lists the goals in slo.json, or changes the number (with its reason) or only the reason of one:  make goals GOAL_ARGS="scale-up 120 --why 'one sentence'"
goals: ; python3 scripts/set-goal.py $(GOAL_ARGS)

# The resilience drill deletes ONE pod in each tier, one tier after the other, and watches the platform bring it back (Step 7).
# Options, for example:  make drill DRILL_ARGS="--only restapi"   (the whole list:  python3 scripts/drill.py --help)
drill: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/drill.py $(DRILL_ARGS)

# Draws the table, the goals (slo.json) and the chart of the last drill again from the saved files. Needs no cluster.
drill-report: ; python3 scripts/drillreport.py $(DRILL_ARGS)

# Calls every endpoint of the REST API and saves the requests, the answers, the log lines and the numbers that belong to them.
rest-evidence: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/rest-evidence.py $(REST_ARGS)

# The manual steps of runbook.md, done once (logs, scale the REST API from 2 pods to 3 and back, certificate end dates). Nothing is deleted.
# If it stops in the middle, the REST API is put back to 2 pods first.
operator-steps: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/operator-steps.py $(OPERATOR_ARGS)

# The load generator and its autoscaler are part of the stack. They only send while the load is switched on.
scale-up: ; $(KUBECTL) apply -k producer/loadtest

scale-down: ; $(KUBECTL) apply -k producer/load

down: ; $(KUBECTL) delete namespace ca3-intruder --ignore-not-found --wait=false ; $(KUBECTL) delete -k . --ignore-not-found --wait=true && echo "Deleted. Check:  kubectl get namespace $(NS)   (it should say NotFound)"

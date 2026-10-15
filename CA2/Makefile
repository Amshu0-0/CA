# CA2: two commands build the cluster and run the stack on it, and two more remove them.
#
#   make cluster-up    Terraform builds the network and 3 machines, Ansible installs Kubernetes (k3s) on them
#   make up            create the Secret, apply the stack, wait until it is running
#   make rest-check    call the REST API from this computer, through every node
#   make down          delete the stack, including the disks and the Secret
#   make cluster-down  destroy the machines and the network
#   make validate      check every manifest against the Kubernetes schemas (needs no cluster)
#   make verify        check that manifests, programs, cluster code and CA2 requirements agree (needs no cluster)
#   make smoke         send one tagged event through producer, Kafka, processor, MongoDB and REST API
#   make netpol-check  test what each kind of pod can and cannot reach (the NetworkPolicy test)
#   make heal-check    delete pods on purpose and check that Kubernetes brings them back with their data
#   make scale-test    scaling test: 1 producer pod, then the autoscaler adds pods; measured before and after (about 10 minutes)
#   make scale-report  rebuild the results table and chart from the saved samples of the last scaling test
#
# Needs: terraform, ansible, kubectl, bash, openssl and ssh-keygen, plus AWS credentials, for the cluster targets;
# kubeconform for "make validate"; python3 with PyYAML for "make verify".
# Every recipe sits on its rule line after a semicolon, so this file has no TAB characters.

NS        ?= ca2
KUBECTL   ?= kubectl
TERRAFORM ?= terraform
TF         = $(TERRAFORM) -chdir=infra/terraform

# Plain text from Terraform: no colour codes. Saved evidence stays readable and grep can find the lines.
TF_CLI_ARGS ?= -no-color
export TF_CLI_ARGS

# Extra options for the test scripts, for example:  make smoke SMOKE_ARGS="--save evidence/step6-smoke-response.json"
SMOKE_ARGS ?=
HEAL_ARGS  ?=
SCALE_ARGS ?=

# The stack targets use the cluster that "make cluster-up" built (its kubeconfig lives in .secrets/).
# To use another cluster instead, run for example:  make up KUBECONFIG=$HOME/.kube/config
KUBECONFIG ?= $(CURDIR)/.secrets/kubeconfig.yaml
export KUBECONFIG

.DEFAULT_GOAL := help
.PHONY: help validate verify cluster-key cluster-check cluster-plan cluster-up cluster-status cluster-down \
    context secrets up status get-all logs diagnose rbac-check rest-check exposure-check scale-up scale-down images-info down \
    smoke netpol-check netpol-control heal-check scale-test scale-report

help: ; @printf '%s\n' \
    "  Cluster (AWS)" \
    "  make cluster-up      build the network and 3 machines (Terraform), then install Kubernetes (Ansible)" \
    "  make cluster-plan    show what Terraform would change (after cluster-up the answer must be: No changes)" \
    "  make cluster-check   static checks of the cluster code: terraform fmt and validate, ansible syntax" \
    "  make cluster-status  show the nodes, all pods, and node CPU and memory" \
    "  make cluster-down    destroy the machines and the network" \
    "" \
    "  Stack (on the cluster)" \
    "  make up              create the Secret, apply the whole stack and wait until it is running" \
    "  make status          show everything in the namespace" \
    "  make get-all         show everything in every namespace (kubectl get all -A)" \
    "  make logs            show the processor logs" \
    "  make diagnose        explain why a pod is not ready: states, events and logs (read-only)" \
    "  make rest-check      call the REST API from this computer, through the node port of every node" \
    "  make exposure-check  show which ports of the nodes answer from this computer" \
    "  make rbac-check      prove the access rules with kubectl auth can-i" \
    "  make smoke           send one tagged event through producer, Kafka, processor, MongoDB and REST API" \
    "  make netpol-check    test what each kind of pod can reach (30 connections, compared with the policies)" \
    "  make netpol-control  the same test, then again with the policies deleted: proves the policies do the blocking" \
    "  make heal-check      delete pods on purpose and check that they come back with their data" \
    "  make scale-up        start the load generator and its autoscaler (the scaling demonstration)" \
    "  make scale-down      stop the load generator and its autoscaler" \
    "  make scale-test      scaling test: 1 producer pod, then the autoscaler adds pods; measured before and after (about 10 minutes)" \
    "  make scale-report    rebuild the results table and chart from the saved samples of the last scaling test" \
    "  make down            delete everything: workloads, disks, Secret, namespace" \
    "" \
    "  Checks that need no cluster" \
    "  make validate        check every manifest against the Kubernetes schemas" \
    "  make verify          check manifests, programs, cluster code and CA2 requirements against each other" \
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

# ---------------------------------------------------------------- checks that need no cluster

validate: ; $(KUBECTL) kustomize . | kubeconform -strict -summary - && kubeconform -strict -summary producer/scaling producer/smoke

verify: ; python3 scripts/verify_manifests.py

images-info: ; @grep -rhoE 'image: [^ ]+' --include='*.yaml' . | sort | uniq -c | sort -rn

# ---------------------------------------------------------------- the stack

context: ; @if [ ! -f "$(KUBECONFIG)" ]; then echo "There is no cluster yet: $(KUBECONFIG) does not exist. Run:  make cluster-up"; exit 1; fi ; \
    echo "kubectl context: $$($(KUBECTL) config current-context)   (kubeconfig: $(KUBECONFIG))"

secrets: ; bash scripts/create-secrets.sh

up: context secrets ; $(KUBECTL) apply -k . && \
    ( $(KUBECTL) -n $(NS) rollout status statefulset/mongo --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status statefulset/kafka --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/processor --timeout=300s && \
      $(KUBECTL) -n $(NS) rollout status deployment/restapi --timeout=300s && \
      $(KUBECTL) -n $(NS) wait --for=condition=complete job/producer-replay --timeout=600s && \
      echo "Stack is up. Call the REST API from this computer with:  make rest-check" ) || \
    { echo "The stack is not up yet. Read the first error above, then run:  make diagnose   (it is safe to run make up again)"; exit 1; }

status: ; $(KUBECTL) -n $(NS) get all,pvc,configmap,hpa,networkpolicy,serviceaccount,role,rolebinding -o wide

get-all: ; $(KUBECTL) get all -A

logs: ; $(KUBECTL) -n $(NS) logs deployment/processor --tail=40

diagnose: ; NS=$(NS) KUBECTL=$(KUBECTL) bash scripts/diagnose.sh

rest-check: ; TERRAFORM=$(TERRAFORM) python3 scripts/rest-check.py

exposure-check: ; @echo "kubectl get svc -A --field-selector spec.type!=ClusterIP     (every Service that is reachable from outside the cluster)" ; \
    $(KUBECTL) get svc -A --field-selector 'spec.type!=ClusterIP' && echo && \
    TERRAFORM=$(TERRAFORM) python3 scripts/exposure-check.py

rbac-check: ; @echo "pipeline-viewer   can list pods?       $$($(KUBECTL) auth can-i list pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-viewer   can delete pods?     $$($(KUBECTL) auth can-i delete pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-operator can scale producers? $$($(KUBECTL) auth can-i patch deployments.apps --subresource=scale --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "pipeline-operator can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "processor pod     can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):processor -n $(NS))"

# The tests need the running stack ("make up"). Whatever they create is removed again when they end.
smoke: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/smoke-test.py $(SMOKE_ARGS)

netpol-check: ; NS=$(NS) KUBECTL=$(KUBECTL) python3 scripts/netpol-check.py

netpol-control: ; NS=$(NS) KUBECTL=$(KUBECTL) python3 scripts/netpol-check.py --control

heal-check: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/heal-check.py $(HEAL_ARGS)

# The scaling test starts the load itself and removes it again when it ends, also when it stops early. Options: SCALE_ARGS="--help".
scale-test: ; NS=$(NS) KUBECTL=$(KUBECTL) TERRAFORM=$(TERRAFORM) python3 scripts/scale-test.py $(SCALE_ARGS)

# Needs no cluster: it reads evidence/step7-scale-samples.csv and writes the table and the chart again.
scale-report: ; python3 scripts/scalereport.py

scale-up: ; $(KUBECTL) apply -f producer/scaling/

scale-down: ; $(KUBECTL) delete -f producer/scaling/ --ignore-not-found

down: ; $(KUBECTL) delete -f producer/scaling/ --ignore-not-found ; $(KUBECTL) delete namespace ca2-intruder --ignore-not-found --wait=false ; $(KUBECTL) delete -k . --ignore-not-found --wait=true && echo "Deleted. Check:  kubectl get namespace $(NS)   (it should say NotFound)"

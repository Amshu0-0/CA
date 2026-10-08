# CA2: two commands build the cluster and run the stack on it, and two more remove them.
#
#   make cluster-up    Terraform builds the network and 3 machines, Ansible installs Kubernetes (k3s) on them
#   make up            create the Secret, apply the stack, wait until it is running
#   make down          delete the stack, including the disks and the Secret
#   make cluster-down  destroy the machines and the network
#   make validate      check every manifest against the Kubernetes schemas (needs no cluster)
#   make verify        check that manifests, programs, cluster code and CA2 requirements agree (needs no cluster)
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

# The stack targets use the cluster that "make cluster-up" built (its kubeconfig lives in .secrets/).
# To use another cluster instead, run for example:  make up KUBECONFIG=$HOME/.kube/config
KUBECONFIG ?= $(CURDIR)/.secrets/kubeconfig.yaml
export KUBECONFIG

.DEFAULT_GOAL := help
.PHONY: help validate verify cluster-key cluster-check cluster-plan cluster-up cluster-status cluster-down \
    context secrets up status logs rbac-check scale-up scale-down images-info down

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
    "  make logs            show the processor logs" \
    "  make rbac-check      prove the access rules with kubectl auth can-i" \
    "  make scale-up        start the load generator and its autoscaler (the scaling demonstration)" \
    "  make scale-down      stop the load generator and its autoscaler" \
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

validate: ; $(KUBECTL) kustomize . | kubeconform -strict -summary - && kubeconform -strict -summary producer/scaling

verify: ; python3 scripts/verify_manifests.py

images-info: ; @grep -rhoE 'image: [^ ]+' --include='*.yaml' . | sort | uniq -c | sort -rn

# ---------------------------------------------------------------- the stack

context: ; @if [ ! -f "$(KUBECONFIG)" ]; then echo "There is no cluster yet: $(KUBECONFIG) does not exist. Run:  make cluster-up"; exit 1; fi ; \
    echo "kubectl context: $$($(KUBECTL) config current-context)   (kubeconfig: $(KUBECONFIG))"

secrets: ; bash scripts/create-secrets.sh

up: context secrets ; $(KUBECTL) apply -k . && \
    $(KUBECTL) -n $(NS) rollout status statefulset/mongo --timeout=300s && \
    $(KUBECTL) -n $(NS) rollout status statefulset/kafka --timeout=300s && \
    $(KUBECTL) -n $(NS) rollout status deployment/processor --timeout=300s && \
    $(KUBECTL) -n $(NS) rollout status deployment/restapi --timeout=300s && \
    $(KUBECTL) -n $(NS) wait --for=condition=complete job/producer-replay --timeout=600s && \
    echo "Stack is up. REST API on every node at port 30080:  curl http://<node-ip>:30080/health"

status: ; $(KUBECTL) -n $(NS) get all,pvc,configmap,hpa,networkpolicy,serviceaccount,role,rolebinding -o wide

logs: ; $(KUBECTL) -n $(NS) logs deployment/processor --tail=40

rbac-check: ; @echo "pipeline-viewer   can list pods?       $$($(KUBECTL) auth can-i list pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-viewer   can delete pods?     $$($(KUBECTL) auth can-i delete pods --as=system:serviceaccount:$(NS):pipeline-viewer -n $(NS))" ; \
    echo "pipeline-operator can scale producers? $$($(KUBECTL) auth can-i patch deployments.apps --subresource=scale --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "pipeline-operator can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):pipeline-operator -n $(NS))" ; \
    echo "processor pod     can read secrets?    $$($(KUBECTL) auth can-i get secrets --as=system:serviceaccount:$(NS):processor -n $(NS))"

scale-up: ; $(KUBECTL) apply -f producer/scaling/

scale-down: ; $(KUBECTL) delete -f producer/scaling/ --ignore-not-found

down: ; $(KUBECTL) delete -f producer/scaling/ --ignore-not-found ; $(KUBECTL) delete -k . --ignore-not-found --wait=true && echo "Deleted. Check:  kubectl get namespace $(NS)   (it should say NotFound)"

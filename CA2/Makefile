# CA2: one command to apply the whole stack, one to delete it.
#
#   make validate    check every manifest against the Kubernetes schemas (needs no cluster)
#   make verify      check that manifests, programs and the CA2 requirements agree (needs no cluster)
#   make up          create the Secret, apply the stack, wait until it is running
#   make status      show everything in the namespace
#   make down        delete everything, including the disks and the Secret
#
# Needs: kubectl pointing at the cluster (kubectl config current-context), kubeconform for "make validate",
# and PyYAML for "make verify". Every recipe sits on its rule line after a semicolon, so this file has no TAB characters.

NS      ?= ca2
KUBECTL ?= kubectl

.DEFAULT_GOAL := help
.PHONY: help validate verify secrets up status logs rbac-check scale-up scale-down images-info down

help: ; @printf '%s\n' \
    "  make validate     check every manifest against the Kubernetes schemas (no cluster needed)" \
    "  make verify       check manifests, programs and CA2 requirements against each other (no cluster needed)" \
    "  make secrets      create the MongoDB credentials Secret (generated on first use)" \
    "  make up           apply the whole stack and wait until it is running" \
    "  make status       show everything in the namespace" \
    "  make logs         show the processor logs" \
    "  make rbac-check   prove the access rules with kubectl auth can-i" \
    "  make scale-up     start the load generator and its autoscaler (the scaling demonstration)" \
    "  make scale-down   stop the load generator and its autoscaler" \
    "  make images-info  list every container image the manifests use" \
    "  make down         delete everything: workloads, disks, Secret, namespace"

validate: ; $(KUBECTL) kustomize . | kubeconform -strict -summary - && kubeconform -strict -summary producer/scaling

verify: ; python3 scripts/verify_manifests.py

secrets: ; bash scripts/create-secrets.sh

up: secrets ; $(KUBECTL) apply -k . && \
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

images-info: ; @grep -rhoE 'image: [^ ]+' --include='*.yaml' . | sort | uniq -c | sort -rn

down: ; $(KUBECTL) delete -f producer/scaling/ --ignore-not-found ; $(KUBECTL) delete -k . --ignore-not-found --wait=true && echo "Deleted. Check:  kubectl get namespace $(NS)   (it should say NotFound)"

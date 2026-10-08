NS ?= ca2
KUBECTL ?= kubectl

.DEFAULT_GOAL := help

.PHONY: help validate secrets up status logs down

help:
	@echo "make validate  - validate all Kubernetes manifests"
	@echo "make secrets   - create MongoDB credentials"
	@echo "make up        - deploy the CA2 stack"
	@echo "make status    - show CA2 resources"
	@echo "make logs      - show processor logs"
	@echo "make down      - delete the CA2 stack"

validate:
	$(KUBECTL) kustomize . | kubeconform -strict -summary -

secrets:
	bash scripts/create-secrets.sh

up: secrets
	$(KUBECTL) apply -k .

status:
	$(KUBECTL) -n $(NS) get all,pvc,configmap,serviceaccount -o wide

logs:
	$(KUBECTL) -n $(NS) logs deployment/processor --tail=40

down:
	$(KUBECTL) delete -k . --ignore-not-found --wait=true

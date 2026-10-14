#!/usr/bin/env bash

# Prints, in one go, what you need to see why the stack is not coming up:
# the nodes, every pod that is not ready, its conditions and events, and the log lines of each of its containers.
# It only reads: it changes nothing, and it prints no Secret (describe and logs never show Secret values).
#
#   make diagnose
#
# Pods that run normally are left out on purpose. If nothing is wrong you will see "(none)".

NS="${NS:-ca2}"
KUBECTL="${KUBECTL:-kubectl}"

section() { printf '\n===== %s =====\n' "$1"; }

section "nodes"
"$KUBECTL" get nodes -o wide 2>&1

section "all pods in namespace $NS"
"$KUBECTL" -n "$NS" get pods -o wide 2>&1

# A pod is "not fully ready" when its READY column is like 0/1, or its STATUS is not Running (Completed jobs are fine).
section "pods in every namespace that are not fully ready"
notready=$("$KUBECTL" get pods -A --no-headers 2>/dev/null | awk '$4 != "Completed" { split($3, r, "/"); if (r[1] != r[2] || $4 != "Running") print }')
if [ -n "$notready" ]; then echo "$notready"; else echo "(none)"; fi

section "volumes (PersistentVolumeClaims) in namespace $NS"
"$KUBECTL" -n "$NS" get pvc 2>&1

section "the last 25 events in namespace $NS (newest last)"
"$KUBECTL" -n "$NS" get events --sort-by=.lastTimestamp 2>&1 | tail -25

section "which pods stand behind each Service"
"$KUBECTL" -n "$NS" get endpointslices 2>&1

section "node CPU and memory"
"$KUBECTL" top nodes 2>&1

# For every pod of the namespace that is not fully ready: why (conditions and events), and what its containers printed.
for pod in $("$KUBECTL" -n "$NS" get pods --no-headers 2>/dev/null | awk '$3 != "Completed" { split($2, r, "/"); if (r[1] != r[2] || $3 != "Running") print $1 }'); do
  section "pod $pod: state of its containers, conditions and events"
  "$KUBECTL" -n "$NS" describe pod "$pod" 2>&1 | awk '/^(Node|Status|Controlled By):/ {print} /^(Init Containers|Containers):/ {show = 1} /^Volumes:/ {show = 0} /^Events:/ {show = 1} show'

  section "pod $pod: last log lines of every container, init containers included"
  "$KUBECTL" -n "$NS" logs "$pod" --all-containers --prefix --tail=25 2>&1 | head -100

  section "pod $pod: log lines of the previous run, if the container was restarted"
  "$KUBECTL" -n "$NS" logs "$pod" --all-containers --prefix --previous --tail=25 2>&1 | head -100
done

printf '\n===== end of the diagnosis =====\n'

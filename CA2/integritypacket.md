# Integrity Packet — CA2

**Owner: Amshu Wagle. I own this outcome.**

## Outcome

I moved the CA0/CA1 intrusion-detection pipeline (producer → Kafka → processor → MongoDB → REST API) onto Kubernetes, declaratively. Terraform and Ansible build a three-machine k3s cluster on AWS (as in CA1). One command, `kubectl apply -k .`, creates the whole stack (37 Kubernetes objects) and one command, `make down`, removes it. On top of the stack I added what CA2 asks for: network isolation (11 NetworkPolicies), access rules (RBAC), a Secret that reaches the pods as files, one published port, autoscalers for the producers and the processors with a before/after measurement, and a clean-up that is proved by a scan of AWS.

The goal was the same as in CA1: prove that the *deployment* is repeatable and that each property holds, not only that the pipeline ran once. This packet states each claim, what I assumed, the evidence I saved, how anyone can repeat the check, what failed on the way and what is still uncertain. [README.md](README.md) explains how to run everything; this packet explains why I believe it works.



## Claims

### C1. The cluster is built from code, and building it a second time changes nothing

- **Assumptions:** A1 (AWS rights), A2 (my public address can change), A9 (Terraform's state file).
- **Evidence:** [step4-plan.txt](evidence/step4-plan.txt) and [step4-cluster-up.txt](evidence/step4-cluster-up.txt) (the first build), [step4-plan-run2.txt](evidence/step4-plan-run2.txt) and [step4-cluster-up-run2.txt](evidence/step4-cluster-up-run2.txt) (the second run), [step4-nodes.txt](evidence/step4-nodes.txt) and [step5-cluster-facts.txt](evidence/step5-cluster-facts.txt) (three nodes `Ready`: one control plane and two workers, versions, namespaces), [step6-address-change-excerpt.txt](evidence/step6-address-change-excerpt.txt) (my internet address changed while the cluster ran, and only the three firewall rules that use it were updated).
- **Validation:** `make cluster-up`, then `make cluster-up` again, then `make cluster-status`. `make validate` and `make verify` check the Terraform and Ansible files without AWS.
- **Result:** the first run created 19 resources and Ansible changed 5, 5 and 7 things on the three machines; the second run planned no change and Ansible reported `changed=0` on every machine.
- **Risk that remains:** one region, one AWS account, and macOS as the operator's computer. A newer k3s release was not tried: the version and the installer's checksum are pinned.

The lines that prove it:

<!-- from: evidence/step4-cluster-up.txt -->
```text
Apply complete! Resources: 19 added, 0 changed, 0 destroyed.
ca2-agent-1                : ok=10   changed=5    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
ca2-agent-2                : ok=9    changed=5    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
ca2-server                 : ok=15   changed=7    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
```

<!-- from: evidence/step4-cluster-up-run2.txt -->
```text
No changes. Your infrastructure matches the configuration.
Apply complete! Resources: 0 added, 0 changed, 0 destroyed.
ca2-agent-1                : ok=8    changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
ca2-agent-2                : ok=7    changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
ca2-server                 : ok=13   changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
```

### C2. The stack is created by one command, the second run changes nothing, and one command removes it

- **Assumptions:** A3 (k3s ships the parts the stack needs), A7 (the images are public).
- **Evidence:** [step5-up-run1.txt](evidence/step5-up-run1.txt) (the first apply, then the wait until everything runs), [step5-up-run2.txt](evidence/step5-up-run2.txt) (the second apply), [step5-down.txt](evidence/step5-down.txt) (the namespace and every disk are gone), [step5-up-run3.txt](evidence/step5-up-run3.txt) and [step5-rest-check-run3.txt](evidence/step5-rest-check-run3.txt) (it comes back and works again), [step5-status.txt](evidence/step5-status.txt) and [step5-get-all.txt](evidence/step5-get-all.txt) (every pod `Running`, the replay Job `Completed`), [step5-experiment-service-links.txt](evidence/step5-experiment-service-links.txt) (an experiment that turned one safety setting off to see what it protects, AI-log row 3).
- **Validation:** `make up`, `make up` again, `make down`, `make up` again. `make validate` checks the manifests against the Kubernetes schemas: [step6-validate.txt](evidence/step6-validate.txt).
- **Result:** the first `make up` ended with the stack running; the second said created 0, configured 0, unchanged 37; after `make down` the namespace said `NotFound`; the third `make up` took 41 s and `make rest-check` passed again.
- **Risk that remains:** the Secret is made by a script before the apply, so a bare `kubectl apply -k .` on an empty cluster leaves three workloads waiting for it (README, known limitation 13).

The lines that prove it:

<!-- from: evidence/step5-up-run2.txt -->
```text
objects created: 0   configured: 0   unchanged: 37
```

<!-- from: evidence/step5-down.txt -->
```text
Error from server (NotFound): namespaces "ca2" not found
```

### C3. One tagged event travels through every stage, and the REST API answers a request from outside the cluster

- **Assumptions:** A8 (the dataset), A10 (who may reach the REST API).
- **Evidence:** [step6-smoke.txt](evidence/step6-smoke.txt) (the run), [step6-rest-request.txt](evidence/step6-rest-request.txt) (the request and `HTTP 200`), [step6-smoke-response.json](evidence/step6-smoke-response.json) (the complete answer), [step5-rest-check.txt](evidence/step5-rest-check.txt) (a call through each of the three nodes; MongoDB holds 5,000 flows).
- **Validation:** `make smoke` and `make rest-check`; by hand: `curl http://<node-address>:30080/stats`.
- **Result:** event `smoke-1014-165801-c52d` was stored by Kafka in partition 0 at offset 1664, logged by the processor with the same partition and offset, stored in MongoDB and returned by `GET /events/<id>` with HTTP 200, 35 s after it was sent. A count of stored rows would also pass on old data; a trace id that did not exist a second ago cannot.
- **Risk that remains:** the dataset has only 5,000 rows (A8), and every replay of it adds 5,000 duplicate records (README, known limitation 12).

The lines that prove it:

<!-- from: evidence/step6-smoke.txt -->
```text
RESULT: PASS - event smoke-1014-165801-c52d went producer -> Kafka (partition 0, offset 1664) -> processor -> MongoDB -> REST API in 35 s
```

<!-- from: evidence/step6-rest-request.txt -->
```text
response:  HTTP 200, 2207 bytes, 1.038715 s
```

<!-- from: evidence/step5-rest-check.txt -->
```text
RESULT: PASS - 3 of 3 nodes answered correctly and MongoDB holds 5000 flows
```

### C4. Only the intended connections work between pods, and the NetworkPolicies are the reason

- **Assumptions:** A3 (k3s enforces NetworkPolicy).
- **Evidence:** [step6-netpol-check.txt](evidence/step6-netpol-check.txt) (30 real connections from six throw-away pods, each compared with the answer the policies predict), [step6-netpol-control.txt](evidence/step6-netpol-control.txt) (the same test with all policies deleted, then put back), [step6-netpol-list.txt](evidence/step6-netpol-list.txt) (the 11 policies as the cluster holds them), [`policies/networkpolicies.yaml`](policies/networkpolicies.yaml).
- **Validation:** `make netpol-check` and `make netpol-control` (the second one opens the namespace for about a minute, so it needs a quiet moment).
- **Result:** 13 connections were allowed and 17 blocked, exactly as predicted. Without the policies every connection was accepted within 1 s; with them back, the original answers returned within 2 s. A "blocked" answer could also mean a broken network, and the control experiment is what rules that out.
- **Risk that remains:** policies decide by pod label, so anyone who may create a pod can give it a label (README, known limitation 3). A connection counts as blocked when it fails within 3 s, so a dropped and a rejected connection look the same.

The lines that prove it:

<!-- from: evidence/step6-netpol-check.txt -->
```text
RESULT: PASS - all 30 connections behave as the policies say (13 allowed, 17 blocked)
```

<!-- from: evidence/step6-netpol-control.txt -->
```text
RESULT: PASS - without the policies every connection worked, with them only the allowed ones do: the policies are what blocks
```

### C5. The MongoDB password is not in Git, it reaches the pods as read-only files, the access rules give the least rights, and pods cannot call the Kubernetes API

- **Assumptions:** A12 (`.secrets/` stays on my computer and out of Git), A3 (k3s encrypts Secrets and enforces the settings used here).
- **Evidence:** [step6-secrets.txt](evidence/step6-secrets.txt) (the files in the pod are `-r--r-----`, the pod's environment holds the two file paths and not the password, the pod has no API token, only one Service is published), [step6-rbac.txt](evidence/step6-rbac.txt) (five questions and real requests sent as each account with `kubectl --as`), [step5-ailog-checks.txt](evidence/step5-ailog-checks.txt) (MongoDB refuses a client without a password; a privileged pod is refused by Pod Security), [`scripts/create-secrets.sh`](scripts/create-secrets.sh).
- **Validation:** `make rbac-check`; the `kubectl exec` commands are written at the top of each block in [step6-secrets.txt](evidence/step6-secrets.txt). `make verify` fails if a manifest holds a literal password and, on a computer that has a `.secrets/` folder, if any long value from it appears in any file of the repository.
- **Result:** the viewer can list pods and cannot delete them; the operator can scale producers and cannot read Secrets; the processor's account cannot read Secrets; the pod's environment says `MONGO_PASSWORD_FILE=/etc/secrets/mongo/password`, never the password.
- **Risk that remains:** the processor and the REST API both use the MongoDB administrator account, and nothing inside the cluster is encrypted in transit (README, known limitation 4). Encryption of Secrets at rest was read back on a single k3s server by Claude (AI-log row 17), not on the AWS cluster.

The lines that prove it:

<!-- from: evidence/step6-rbac.txt -->
```text
pipeline-viewer   can list pods?       yes
pipeline-viewer   can delete pods?     no
pipeline-operator can scale producers? yes
pipeline-operator can read secrets?    no
processor pod     can read secrets?    no
```

<!-- from: evidence/step6-secrets.txt -->
```text
MONGO_PASSWORD_FILE=/etc/secrets/mongo/password
MONGO_USER_FILE=/etc/secrets/mongo/username
```

<!-- from: evidence/step5-ailog-checks.txt -->
```text
Unauthorized: Command listDatabases requires authentication
```

### C6. Only three ports answer from outside, and only to my address

- **Assumptions:** A1, A2.
- **Evidence:** [step5-firewall.txt](evidence/step5-firewall.txt) (the security group read back from AWS: seven inbound rules and nothing open to everyone, then every port tried from my computer on all three machines), [`infra/terraform/security.tf`](infra/terraform/security.tf).
- **Validation:** `make exposure-check`.
- **Result:** ports 22 and 30080 answer on all three machines, 6443 only on the server, and the other eight ports probed are dropped.
- **Risk that remains:** the check runs from my address only. The rules between the machines (6443, 10250, 8472/udp, ping) cannot be probed from outside.

The line that proves it:

<!-- from: evidence/step5-firewall.txt -->
```text
RESULT: PASS - on all 3 nodes only 22 (SSH), 6443 (Kubernetes API, server only), 30080 (REST API node port) answer; the other 8 ports are dropped
```

### C7. The stack recovers from four failures that I caused on purpose, and keeps its data

- **Assumptions:** A5 (a deleted pod keeps its disk; a lost machine does not).
- **Evidence:** [step6-heal.txt](evidence/step6-heal.txt).
- **Validation:** `make heal-check` (about 2.5 minutes).
- **Result:** a REST pod was replaced in 16 s while 0 of 32 calls failed; an event sent while the processor was away waited in Kafka and was stored 10 s after the processor came back; `mongo-0` and `kafka-0` were back in 12 s and 22 s on the same disks, with all 5,002 records and all 5,002 messages.
- **Risk that remains:** losing a whole machine was not tested. The disks are directories on one machine (README, known limitation 1).

The lines that prove it:

<!-- from: evidence/step6-heal.txt -->
```text
  PASS  rest       one REST pod deleted, 2 ready again after 16 s, 0 of 32 calls failed
  PASS  processor  an event sent while the processor was away reached MongoDB 10 s after the processor started
  PASS  mongo      mongo-0 deleted, a new pod was ready after 12 s with the same disk, all 5002 records still there
  PASS  kafka      kafka-0 deleted, a new pod was ready after 22 s with the same disk and the same messages, and a new event went all the way through
RESULT: PASS - 4 of 4 recovery tests passed
```

### C8. The producers scale from 1 to 5 pods under an autoscaler, and messages per second were measured before and after

- **Assumptions:** A4 (metrics-server gives CPU numbers), A6 (the topic has three partitions).
- **Evidence:** [step7-scale-results.md](evidence/step7-scale-results.md) (the before/after table, the autoscaler's steps, a reading of the numbers), [step7-scale-chart.svg](evidence/step7-scale-chart.svg), [step7-scale-samples.csv](evidence/step7-scale-samples.csv) (the raw measurements, one row every 5 s), [step7-scale-summary.json](evidence/step7-scale-summary.json), [step7-scale-hpa.txt](evidence/step7-scale-hpa.txt) (`kubectl describe hpa`), [step7-scale-producer-log.txt](evidence/step7-scale-producer-log.txt), [step7-scale-run.txt](evidence/step7-scale-run.txt) (everything the test printed), [step7-update-to-1.0.1.txt](evidence/step7-update-to-1.0.1.txt) (the image that the fixed test used). The failed first run is kept in `step7-run1-oom-*` (see *Failures found and fixed*, L).
- **Validation:** `make scale-test` (about 5 minutes) and `make scale-report`.
- **Result:** the autoscaler took the producers from 1 to 5 pods in 40 s and the processors from 1 to 3. Messages per second rose from 656 to 2,369 (what the producers send, x3.6), from 625 to 2,399 (what Kafka receives, x3.8) and from 737 to 1,138 (what MongoDB stores, x1.5). The rest waited in Kafka: 117,483 messages at the end of the window, so a new tagged event needed 46.1 s instead of 0.1 s to reach the REST API.
- **Risk that remains:** five pods gave less than five times the rate of one pod and I did not measure why (the results file says "no cause is claimed"). The 'before' window was not a steady state, because the processors' autoscaler added pods during it. The producers shrinking again was not demonstrated: the test removes the autoscaler and the load together. Both autoscalers use CPU only; the backlog in Kafka would be a better signal and I did not build that.

The lines that prove it (copied from the results table):

<!-- from: evidence/step7-scale-results.md -->
```text
| Measurement | Before: 1 producer pod, no autoscaler | After: 5 producer pods, autoscaler working | Change |
|---|---:|---:|---:|
| Producers send, messages per second (all pods together, reported by the pods) | 656 | 2,369 | x3.6 |
| Kafka receives, messages per second (growth of the topic) | 625 | 2,399 | x3.8 |
| MongoDB stores, messages per second (growth of the record count) | 737 | 1,138 | x1.5 |
| Backlog in Kafka at the end of the window, messages | 310 | 117,483 |  |
```

### C9. The clean-up leaves nothing behind in AWS, and the scan that proves it can see

- **Assumptions:** A1 (my IAM user may not call `tag:GetResources`), A9 (the scan asks AWS, not the state file).
- **Evidence:** [step8-scan-1-before-teardown.txt](evidence/step8-scan-1-before-teardown.txt) (with the cluster up, the scan must find it), [step8-teardown-1.txt](evidence/step8-teardown-1.txt) (stack deleted, 19 resources destroyed, scan clean), [step8-teardown-2.txt](evidence/step8-teardown-2.txt) (the same for the rebuilt cluster, then a scan of every region), [step8-teardown-3-again.txt](evidence/step8-teardown-3-again.txt) (a third `make teardown` with nothing left), [step8-scan-2-cluster-up.txt](evidence/step8-scan-2-cluster-up.txt) (the scan finding the rebuilt cluster, with new resource ids).
- **Validation:** `make teardown`, `make scan`, `make scan SCAN_ARGS="--all-regions"`.
- **Result:** the scan said `LEFTOVERS FOUND` while the cluster existed and `CLEAN` after each destroy, in this region and in all 17 enabled regions; a repeated `make teardown` changed nothing and exited with 0.
- **Risk that remains:** the optional check of the tag index was skipped every time (my IAM user is not allowed to call it, and the result line says so). A free resource made by hand without the tag, such as an empty network, is invisible to the scan.

The lines that prove it:

<!-- from: evidence/step8-scan-1-before-teardown.txt -->
```text
RESULT: LEFTOVERS FOUND - the resources listed above are still in AWS.
```

<!-- from: evidence/step8-teardown-2.txt -->
```text
Destroy complete! Resources: 19 destroyed.
RESULT: CLEAN - nothing tagged Project=CS5287-CA2 is left, and no billable resources are running in us-east-2.
RESULT: CLEAN - nothing tagged Project=CS5287-CA2 is left, and no billable resources are running in any region.
```

<!-- from: evidence/step8-teardown-3-again.txt -->
```text
exit code of the second make teardown: 0
```

### C10. The repository alone is enough to rebuild everything

- **Assumptions:** A11 (the reviewer has the tools of the README's *Prerequisites*).
- **Evidence:** [step8-rebuild-cluster-up.txt](evidence/step8-rebuild-cluster-up.txt) and [step8-rebuild-cluster-up-run2.txt](evidence/step8-rebuild-cluster-up-run2.txt) (a new cluster from a fresh clone, then a second run), [step8-rebuild-stack.txt](evidence/step8-rebuild-stack.txt) (the stack, the REST check, the smoke test and the isolation test on it), [step8-rebuild-cluster-facts.txt](evidence/step8-rebuild-cluster-facts.txt), [step8-zip-check.txt](evidence/step8-zip-check.txt) (the ZIP that GitHub offers, unzipped without git: the commit named inside it is the commit of `main`, and `make verify` passed).
- **Validation:** clone the repository into an empty folder, then `make verify`, `make cluster-up` (twice), `make up` (twice), `make smoke`, `make netpol-check`, `make teardown`.
- **Result:** the rebuilt cluster passed every check that was repeated on it.
- **Risk that remains:** one operating system. `make heal-check` and `make scale-test` were not repeated on the rebuilt cluster; they ran on the first one.

The lines that prove it:

<!-- from: evidence/step8-rebuild-stack.txt -->
```text
objects created: 0   configured: 0   unchanged: 37
RESULT: PASS - 3 of 3 nodes answered correctly and MongoDB holds 5000 flows
RESULT: PASS - event smoke-1015-213651-de66 went producer -> Kafka (partition 1, offset 1676) -> processor -> MongoDB -> REST API in 30 s
RESULT: PASS - all 30 connections behave as the policies say (13 allowed, 17 blocked)
```

<!-- from: evidence/step8-zip-check.txt -->
```text
commit named inside the ZIP: a4cf3b401fdfd0bb03679eca68da202663e96647
commit of main on GitHub:    a4cf3b401fdfd0bb03679eca68da202663e96647
folders named .git in the unzipped copy: 0
```

### C11. Every object has a label and a description, and a dry run shows what `make up` would change before it changes anything

- **Assumptions:** none beyond A3.
- **Evidence:** [step8-rebuild-labels.txt](evidence/step8-rebuild-labels.txt) (38 objects carry the label and a description; the two without are made by Kubernetes itself), [step8-dry-run-server.txt](evidence/step8-dry-run-server.txt) and [step8-dry-run-client.txt](evidence/step8-dry-run-client.txt) (35 objects `configured`, 2 `unchanged`, none created, and nothing changed in the cluster).
- **Validation:** `kubectl -n ca2 get all,pvc,configmap,networkpolicy,serviceaccount,role,rolebinding,secret -l app.kubernetes.io/part-of=ca2-pipeline`; `make dry-run` and `make dry-run DRY_RUN=client`. `make verify` fails when an object has no label or no description.
- **Result:** the 35 `configured` were the objects that were about to get a description, which is what the dry run is for.
- **Risk that remains:** a dry run shows that the files are accepted, not that pods start. The sentence in `make help`, "after make up: all unchanged", was not tested on the AWS cluster.

### C12. The documentation matches the repository, and nothing private was published

- **Assumptions:** A11 (the reviewer's tools), A12.
- **Evidence:** [step9-verify.txt](evidence/step9-verify.txt) (`make verify` on my computer). Its section 9 checks that the three documents exist and that the screenshot is a PNG image; that no placeholder is left; that every link stays inside `CA2/` and exists; that every file name and every `make` command in the documents is real; that every quoted line is a line of the file named above it; that the object, policy and AI-log counts in the documents are right; that every file in `evidence/` is listed in this packet; that no evidence file or document holds a public address, an account number, a home folder or a key; and that no value from my `.secrets/` folder is in a file that Git would publish. [step9-tool-versions.txt](evidence/step9-tool-versions.txt) holds the tool versions behind the README's *Prerequisites* table. The same check at each step, from [step3-verify.txt](evidence/step3-verify.txt) (46 checks) to [step8-verify.txt](evidence/step8-verify.txt) (61), is kept to show how it grew; section 9 adds 11 checks.
- **Validation:** `make verify` (needs `kubectl` and Python 3 with PyYAML; no cluster and no AWS).
- **Result:** the last line of [step9-verify.txt](evidence/step9-verify.txt) is the count of passed and failed checks.
- **Risk that remains:** `make verify` shows that the files agree with each other; it cannot tell whether a sentence is well written or whether I understood it. Its secrets check only knows the secrets that exist on the computer that runs it. The old public addresses of my terminated machines are still in earlier commits of the Git history (AI log, row 45).

## Assumptions

Each assumption is written so that it can be tested. The last column says what happened.

| # | Assumption | Why it matters | How it was tested | What happened |
|---|---|---|---|---|
| A1 | I work as a dedicated IAM user (not root) who may create and delete EC2 and VPC resources in `us-east-2`, but may not call `tag:GetResources`. | Wider or narrower rights would change what the build and the scan can do. | `make cluster-up` created everything; `make scan` reported its tag-index check as skipped. | Held. The missing permission is named in the scan's result line, not hidden. |
| A2 | My public internet address can change while the cluster exists. | The firewall lets SSH, the Kubernetes API and the REST API in from one address; an address typed into a file goes stale (the CA1 limitation). | Terraform looks the address up on every run (`admin_cidr: auto`). It really changed on October 14, during Step 6. | Held. `make cluster-up` changed only the three rules that use the address, and the request that had failed (`HTTP 000`) was answered again (`HTTP 200`): [step6-address-change-excerpt.txt](evidence/step6-address-change-excerpt.txt). |
| A3 | k3s provides what the stack needs: DNS, metrics-server, local-path storage, encrypted Secrets and a NetworkPolicy controller that enforces the policies. | A policy that is not enforced only looks like security. | 30 real connections and a control experiment (C4); the autoscalers read metrics-server (C8); the disks are `local-path` volumes (C7). | Held. |
| A4 | metrics-server reports CPU numbers about a minute after pods start. | Both autoscalers are blind without them. | The scaling test refuses to measure until the autoscaler has a number. | It stopped the first run, whose pods were killed before they had a number, and passed after the fix (failure L below). |
| A5 | A deleted pod keeps its data (the new pod uses the same disk on the same machine); a lost machine does not. | It decides what "self-healing" can honestly mean here. | `make heal-check` deletes `mongo-0` and `kafka-0` and compares the disks and the data. | Held for pods. Losing a machine was not tested (README, known limitation 1). |
| A6 | The topic has three partitions, so more than three processors would not help. | It sets the maximum of the processors' autoscaler. | The processors' autoscaler stopped at 3 pods. | Held by design. Four processors were not tried. |
| A7 | The three application images are public on `ghcr.io` and can be pulled without a login. | A private image needs a pull secret, which is one more secret in the cluster. | Every pod started on a fresh cluster with no pull secret (C2, C10). | Held. |
| A8 | The 5,000-row dataset is enough to show that the pipeline is correct, not that it scales. | CA0 noted that the full set has about 191,000 rows. | Not tested at full size. The scaling test produced hundreds of thousands of messages, but they are copies of the same 5,000 rows. | Open (README, known limitation 9). |
| A9 | Terraform's local state file is the only memory of what it created, and it can be lost or belong to another folder. | Without it `terraform destroy` cannot find the resources. | `make scan` asks AWS directly. It was tested in both directions: it finds the cluster while it exists and nothing after the destroy (C9). | Held. |
| A10 | Only my address needs to reach the REST API. | The REST API has no login of its own. | The rule `rest_api_cidr` defaults to my address; the ports were probed from outside (C6). | Held. The missing login is a known limitation (5). |
| A11 | A reviewer has `make`, `kubectl` and Python 3 with PyYAML (and, to rebuild everything, Terraform, Ansible and AWS credentials). | `make verify` needs no cluster, but it needs these. | If PyYAML is missing, `make verify` stops and prints the command that installs it. The ZIP route was tried with no git and no clone (C10). | Held on macOS only. |
| A12 | `.secrets/` stays on my computer and out of Git, and the MongoDB password is random and used only here. | It is the base of the secrets claim (C5). | `.gitignore` lists `.secrets/` (`make verify` checks the line), and `make verify` searches the repository for the values in `.secrets/`. | Held. |

## Failures found and fixed

A packet that shows only success is not evidence. These are the failures that mattered most, in the order they happened. The AI log ([`ai-guidance-log.md`](ai-guidance-log.md), second table) has all 19 mistakes with the way each was found. Each row follows the same path: what I expected (the baseline), what the test showed (the failure), the correction, the evidence, and what is left.

| Mistake | What I expected, and what the test showed | The correction | Evidence, and what is left |
|---|---|---|---|
| G | MongoDB reads its administrator password from a mounted Secret file. The files belong to root with mode 0400 and the official image switches to the user `mongodb` (999) before it reads them, so MongoDB could not have read its own password. Found by reading the image's startup script before the first deploy. | `fsGroup: 999` and mode 0440. `make verify` fails if a Secret volume cannot be read by the pod's group. Claude broke the new rules nine different ways on purpose, in its own environment, and the check caught all nine. | [step5-ailog-checks.txt](evidence/step5-ailog-checks.txt): the files belong to group 999, `mongod` runs as user 999, a client without a password is refused. Left: tied to this image version. |
| H | A second `kubectl apply` says `unchanged`. It said `configured` for Kafka and MongoDB, which looks like a failed idempotency test. Found by applying twice to a real k3s API server before the first deploy. | The disk templates spell out the values that Kubernetes fills in itself; `make verify` checks it. | [step5-up-run2.txt](evidence/step5-up-run2.txt): unchanged 37. Left: nothing known. |
| J | The workers would answer "refused" on port 6443. They were silent, so the port check printed `FAIL` although the firewall was right. I could not test this before, because there was no cluster. Found by the first `make exposure-check` on the real cluster. | Port 6443 is not judged on the workers, because only the server runs the Kubernetes API. Every other port is judged as before. | [step5-firewall.txt](evidence/step5-firewall.txt). Left: the firewall still lets 6443 through to the workers; nothing listens there. |
| L | The load generator sends as fast as its CPU limit allows. It queued rows in memory faster than Kafka accepted them, passed its 256Mi limit and was killed by Kubernetes (`OOMKilled`, exit code 137, 13 to 14 s after each start). The autoscaler never got a CPU number, the scaling test stopped itself after 90 s, and the first run measured nothing. | The producer waits for Kafka after every 500 rows (`flush()`); image 1.0.0 became 1.0.1. | The failed run is kept in `step7-run1-oom-*`, with [the diagnosis](evidence/step7-run1-oom-diagnosis.txt). [step7-fix-check.txt](evidence/step7-fix-check.txt): one pod for 90 s at 48 to 49 MiB and 0 restarts. Then the saved run, [step7-scale-results.md](evidence/step7-scale-results.md). Left: other batch sizes were not tried. |
| M | The results text says only what was measured. The first version wrote that the workers' 77% CPU meant CPU was not the limit and that the producers were waiting for something else, "for example Kafka". Nothing measured that. It also did not say that the 'before' window was not a steady state. Found by re-reading the results against the saved samples. | The report generator says what was measured and that the cause was not measured; it adds a row for the control-plane node and a note when the processors changed during the 'before' window. The files were drawn again from the saved samples; there was no new run. | [step7-scale-results.md](evidence/step7-scale-results.md) ("so no cause is claimed"). Left: the cause is still unknown. |
| N | Every object is labelled and described (a tip of the assignment). Five ServiceAccounts had no labels and 35 of the 37 objects had no description. Found by an audit of the 37 rendered objects; the new `make verify` check failed on the Step 7 manifests; my dry run counted the change. | A patch that changes only labels and annotations (it compares every object before and after); the script that makes the Secret labels and annotates it too; `make verify` has a check. | [step8-dry-run-server.txt](evidence/step8-dry-run-server.txt) (35 `configured`), [step8-rebuild-labels.txt](evidence/step8-rebuild-labels.txt) (38 labelled and described). Left: nothing known. |
| P | A fresh copy of the repository uses its own cluster file. The Makefile said `KUBECONFIG ?=`, which gives way to a `KUBECONFIG` already set in the terminal, and earlier steps had told me to export one. Found by re-reading the blocks while planning the rebuild; nothing had failed yet. | `KUBECONFIG = …`: the Makefile decides, and `make up KUBECONFIG=…` on the command line still wins. | The rebuilt cluster in [step8-rebuild-stack.txt](evidence/step8-rebuild-stack.txt) was built and used from a fresh clone. Left: tested on copies of the repository, not by setting a wrong variable on AWS. |
| Q | The AI said 37 labelled objects would be counted on the rebuilt cluster. There were 38: the extra one is the smoke-test Job, which stays for 30 minutes after `make smoke`. Found by my real run. | None needed in the repository; the AI log (row 42) explains the 38. | [step8-rebuild-labels.txt](evidence/step8-rebuild-labels.txt). Left: nothing. |
| R | The AI's paste-corruption check (a `grep` for stray characters in the files I had pasted) printed `clean` even when `grep` said `Operation timed out` on a file. A check that could not look must not say clean. Found by reading the terminal output of the Step 8 patch, where the error line stood directly above `clean`. | The check keeps the exit code of `grep` and says "could not read every file" when it could not look. | AI log, mistake R. Left: the same pattern could hide elsewhere; the new checks use exit codes. |

## Escalation path

This system is not self-healing in every case, and I do not want it to look as if it were. These are the places where I left a person in the loop on purpose, instead of letting code guess or retry:

- **A `make cluster-up` that stops half-way** is not rolled back or retried. Terraform's state shows what was created, and running `make cluster-up` again continues from there (C1). Nothing retries by itself, because a repeated failure (an AWS limit, for example) deserves a person who reads the message. `make scan` shows what AWS holds in the meantime.
- **A pod that does not become ready** stops `make up` with an error after its wait. Kubernetes restarts a crashed container by itself, but nothing decides to try something else. `make diagnose` prints the node states, the pods that are not ready, their events and logs (it only reads, and prints no Secret), and I decide what to do.
- **The scaling test stops itself when the cluster is in danger.** It removes its load and says why it stopped when it has sent 1,500,000 messages (about 3.6 GB against disks of 5Gi), after 25 minutes, when `kafka-0` or `mongo-0` restarts, on Ctrl-C, and when the autoscaler still has no CPU number after 90 s. The first run stopped this way (failure L). What to do next is a person's decision.
- **`make scan` has three outcomes**: exit 0 (nothing left), 1 (something is left) and 2 (it could not scan). It never says `CLEAN` when a required check did not run, and an optional check that was skipped is named in the result line. A leftover is for a person to read and remove (`make cluster-down`); the scan does not delete anything.
- **`make teardown` fails (non-zero exit) if anything is left**, so a half-clean state cannot be mistaken for done.
- **The autoscalers stop at their maximum** (5 producers, 3 processors). Nothing raises an alarm when the backlog in Kafka keeps growing (117,483 messages at the end of the scaling window). Watching the backlog, and deciding whether the topic needs more partitions, is a human decision.
- **A lost machine** is not handled automatically. Its disks are gone with it; a person decides whether to rebuild (`make cluster-up`, then `make up`) and the dataset is replayed (README, known limitation 1).
- **Who may create pods in the namespace** needs human review: the NetworkPolicies trust pod labels (known limitation 3), and closing that gap needs an admission policy.
- **Changing the MongoDB password** is not possible without deleting the database disk (README, *Secret setup*). That is a decision for a person.
- **Nothing here decides "my AWS bill is too high" or "I am done working".** `make teardown` is a separate, explicit command. It never runs on a timer.

## Risks that remain

The README lists thirteen known limitations. These are the ones I would fix first:

- **One Kafka broker and one MongoDB, with their disks on one machine.** Losing that machine loses the data (C7). A real system would replicate and use network storage.
- **No encryption inside the cluster, and one shared database account.** Kafka listens in plain text, and the processor and the REST API both use the MongoDB administrator account (C5).
- **The REST API has no login of its own.** The AWS firewall and the node port decide who can reach it (A10).
- **NetworkPolicies trust pod labels** (C4), so they protect against a pod that is not trusted, not against a person who may create pods.
- **A static IAM access key**, configured with `aws configure`, as in CA1. I use a dedicated user and not root, but a key is longer-lived than a session token and more sensitive if it leaks.
- **The measurements describe a small cluster.** The scaling figures come from three small machines that also run the control plane, a 5,000-row file replayed in a loop, a 'before' window that was not a steady state, a cause that was not measured, and a shrink that was not demonstrated (C8).
- **Every replay adds 5,000 duplicate records**, because the processor inserts without a unique key. A write with a key (an upsert) would fix it; I did not build it.
- **The Secret is made by a script, not by a manifest**, so a bare `kubectl apply -k .` on an empty cluster is not enough (C2). That keeps the password out of Git, which is the point.
- **Everything was tested on k3s from a Mac.** It was not tried on another Kubernetes distribution or another operating system.
- **A running cluster costs money** (about $0.15 an hour). `make teardown` stops it, and the scan proves it (C9).

## AI-assisted work

**Disclosure.** I used Claude (Anthropic) throughout CA2. Claude designed and wrote the first version of nearly everything in this folder: the manifests, the three programs, the Terraform and Ansible, the scripts and the Makefile, the checks in `make verify`, the README and this packet.


**Accepted, changed, rejected.**

- *Accepted:* all 45 recommendations are in the repository, and 36 of the rows record a test on my real cluster or my AWS account.
- *Changed after a test or a reading showed a problem:* 9 of the rows refer to a mistake. The recommendation as first written was wrong or incomplete, and the log shows the correction (for example the MongoDB password files, the port check on the workers, the load generator that ran out of memory, the missing labels and the `KUBECONFIG` default).
- *Rejected:* I did not reject a recommendation as a whole. What was replaced was 19 pieces of the AI's output that a test or a re-reading contradicted. I also left four things unbuilt, and the README names each as a limitation: a unique key in the processor (limitation 12), a metric for the autoscalers that is better than CPU (9), a rule that checks pod labels (3), and encryption inside the cluster (4).
- *Where the AI was weakest:* claims about the real cluster that were written before the cluster existed (failures J and Q) or before the evidence was in hand (S), text that said more than was measured (M), and checks of its own checks that said `clean` when they had not looked (K, R). I treat a sentence from the AI about the real world as unproven until it appears in a file in `evidence/`.
- *How the mistakes were found:* the log's second table says. Five of the 19 (J, K, L, Q, R) were first seen in my real runs. 


**Prompts and critiques.** I did not keep the prompts as separate files. The log holds, for each recommendation, the critique (what was tested and what was not) and the outcome; the git history shows the order of the work; and the failed attempts are kept in `evidence/` instead of being deleted.

## Ownership

I own this outcome. I ran every command whose output is in [`evidence/`](evidence/) on my own computer and my own AWS account, I saved that output, and I am responsible for what this repository says. If any part of it is wrong, that is on me, not on the tools I used to build it.


## Evidence index

Every file in [`evidence/`](evidence/) is listed here with the claim it supports. The files are the saved terminal output of real runs unless the last column says otherwise.

| File | Claim | What to look for |
|---|---|---|
| [step3-validate.txt](evidence/step3-validate.txt), [step4-validate.txt](evidence/step4-validate.txt), [step5-validate.txt](evidence/step5-validate.txt), [step6-validate.txt](evidence/step6-validate.txt) | C2 | `make validate` at each step: `kubeconform` finds 37 valid objects in the stack and every object of the on-demand folders valid |
| [step3-verify.txt](evidence/step3-verify.txt), [step4-verify.txt](evidence/step4-verify.txt), [step5-verify.txt](evidence/step5-verify.txt), [step6-verify.txt](evidence/step6-verify.txt), [step7-verify.txt](evidence/step7-verify.txt), [step8-verify.txt](evidence/step8-verify.txt) | C12 | `make verify` at each step: 46, 51, 54, 57, 58 and 61 checks passed |
| [step4-cluster-check.txt](evidence/step4-cluster-check.txt) | C1 | `make cluster-check`: the SSH key is made, and `terraform validate` and Ansible's syntax check pass before anything is built |
| [step4-plan.txt](evidence/step4-plan.txt), [step4-plan-run2.txt](evidence/step4-plan-run2.txt) | C1 | the Terraform plan before the first build (`19 to add`) and after it (`No changes`) |
| [step4-cluster-up.txt](evidence/step4-cluster-up.txt), [step4-cluster-up-run2.txt](evidence/step4-cluster-up-run2.txt) | C1 | the first build (19 resources, Ansible `changed=5/5/7`) and the second run (nothing changed) |
| [step4-nodes.txt](evidence/step4-nodes.txt), [step5-cluster-facts.txt](evidence/step5-cluster-facts.txt) | C1 | three nodes `Ready`, one `control-plane` and two `worker`; versions; the five namespaces |
| [step6-address-change-excerpt.txt](evidence/step6-address-change-excerpt.txt) | C1, A2 | my address changed; the plan says `0 to add, 3 to change, 0 to destroy`; the failed request (`HTTP 000`) then works (`HTTP 200`). An excerpt of my terminal log, with the two addresses replaced |
| [step5-up-run1.txt](evidence/step5-up-run1.txt), [step5-up-run2.txt](evidence/step5-up-run2.txt), [step5-up-run3.txt](evidence/step5-up-run3.txt), [step5-down.txt](evidence/step5-down.txt) | C2 | the first apply, the second (`unchanged: 37`), the delete (namespace `NotFound`, no disk) and the apply after it |
| [step5-status.txt](evidence/step5-status.txt), [step5-get-all.txt](evidence/step5-get-all.txt) | C2 | `kubectl get all` of the running stack: every pod `Running`, the replay Job `Completed` |
| [step5-experiment-service-links.txt](evidence/step5-experiment-service-links.txt) | C2 | an experiment: with Service links switched on, Kafka stops with exit code 1 (AI-log row 3) |
| [step5-ailog-checks.txt](evidence/step5-ailog-checks.txt) | C5 | checks for AI-log rows 4, 7, 12, 13 and 23: Pod Security refuses a privileged pod; MongoDB refuses a client without a password; owner and group of the password files |
| [step5-firewall.txt](evidence/step5-firewall.txt) | C6 | the seven inbound rules read back from AWS, then the port check from outside |
| [step5-rest-check.txt](evidence/step5-rest-check.txt), [step5-rest-check-run3.txt](evidence/step5-rest-check-run3.txt) | C3 | the REST API answers through each of the three nodes; MongoDB holds 5,000 flows. The second file is after the delete and re-create |
| [step6-smoke.txt](evidence/step6-smoke.txt), [step6-rest-request.txt](evidence/step6-rest-request.txt), [step6-smoke-response.json](evidence/step6-smoke-response.json) | C3 | the tagged event at every stage; the REST request with `HTTP 200`; the complete answer |
| [step6-netpol-check.txt](evidence/step6-netpol-check.txt), [step6-netpol-control.txt](evidence/step6-netpol-control.txt), [step6-netpol-list.txt](evidence/step6-netpol-list.txt) | C4 | the 30 connections; the control experiment; the 11 policies as the cluster holds them |
| [step6-rbac.txt](evidence/step6-rbac.txt), [step6-secrets.txt](evidence/step6-secrets.txt) | C5 | the access rules as questions and as real requests; the password as a mounted file, no API token in the pod, one published Service |
| [step6-heal.txt](evidence/step6-heal.txt) | C7 | four deliberate failures and four recoveries |
| [step7-scale-results.md](evidence/step7-scale-results.md), [step7-scale-chart.svg](evidence/step7-scale-chart.svg), [step7-scale-samples.csv](evidence/step7-scale-samples.csv), [step7-scale-summary.json](evidence/step7-scale-summary.json) | C8 | the before/after table, the chart, the raw samples (generated by `make scale-report`), when each phase started and ended |
| [step7-scale-hpa.txt](evidence/step7-scale-hpa.txt), [step7-scale-producer-log.txt](evidence/step7-scale-producer-log.txt), [step7-scale-run.txt](evidence/step7-scale-run.txt) | C8 | `kubectl describe hpa`, the producers' own log lines, everything the test printed |
| [step7-update-to-1.0.1.txt](evidence/step7-update-to-1.0.1.txt), [step7-fix-check.txt](evidence/step7-fix-check.txt) | C8, L | the update to image 1.0.1; one fixed pod for 90 s at 48 to 49 MiB |
| [step7-run1-oom-diagnosis.txt](evidence/step7-run1-oom-diagnosis.txt), [step7-run1-oom-run.txt](evidence/step7-run1-oom-run.txt), [step7-run1-oom-hpa.txt](evidence/step7-run1-oom-hpa.txt), [step7-run1-oom-producer-log.txt](evidence/step7-run1-oom-producer-log.txt), [step7-run1-oom-results.md](evidence/step7-run1-oom-results.md), [step7-run1-oom-chart.svg](evidence/step7-run1-oom-chart.svg), [step7-run1-oom-samples.csv](evidence/step7-run1-oom-samples.csv), [step7-run1-oom-summary.json](evidence/step7-run1-oom-summary.json) | C8, L | the failed first run, kept on purpose: `OOMKilled`, exit code 137, memory limit 256Mi |
| [step8-scan-1-before-teardown.txt](evidence/step8-scan-1-before-teardown.txt), [step8-scan-2-cluster-up.txt](evidence/step8-scan-2-cluster-up.txt) | C9 | the scan finding the cluster while it exists (`LEFTOVERS FOUND`), twice, with different resource ids |
| [step8-teardown-1.txt](evidence/step8-teardown-1.txt), [step8-teardown-2.txt](evidence/step8-teardown-2.txt), [step8-teardown-3-again.txt](evidence/step8-teardown-3-again.txt) | C9 | the destroy, the scan that says `CLEAN` (also for every region), and a repeated teardown that changes nothing |
| [step8-rebuild-cluster-up.txt](evidence/step8-rebuild-cluster-up.txt), [step8-rebuild-cluster-up-run2.txt](evidence/step8-rebuild-cluster-up-run2.txt), [step8-rebuild-stack.txt](evidence/step8-rebuild-stack.txt), [step8-rebuild-cluster-facts.txt](evidence/step8-rebuild-cluster-facts.txt) | C10 | the cluster and the stack built from a fresh clone, the tests on it, its versions and `kubectl get all -A` |
| [step8-zip-check.txt](evidence/step8-zip-check.txt) | C10 | the repository downloaded as a ZIP, unzipped without git: the commit inside it is the commit of `main` |
| [step8-dry-run-server.txt](evidence/step8-dry-run-server.txt), [step8-dry-run-client.txt](evidence/step8-dry-run-client.txt), [step8-rebuild-labels.txt](evidence/step8-rebuild-labels.txt) | C11 | what `make up` would have changed (35 `configured`, 2 `unchanged`); the 38 labelled and described objects |
| [step9-verify.txt](evidence/step9-verify.txt), [step9-tool-versions.txt](evidence/step9-tool-versions.txt) | C12 | `make verify` with its documentation and privacy checks; the tool versions behind the README's *Prerequisites* table |
| [images/](images/) | C1 to C5, C7 to C10 | screenshots of my terminal; the README shows each one under the heading where it belongs, with a caption that says which step it comes from. They were taken after the runs, from the saved scrollback of my terminal; the one for `kubectl get all -A` shows the output saved in [step8-rebuild-cluster-facts.txt](evidence/step8-rebuild-cluster-facts.txt). A picture repeats what the evidence files say and adds no claim of its own. `make verify` can check that `images/kubectl-get-all.png` is a PNG image; it cannot read a picture |

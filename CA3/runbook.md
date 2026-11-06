# Runbook: what a person does when a part of the pipeline breaks

This page is for the person who looks after the pipeline. The producer sends network-flow events to Kafka, the processor reads them and stores them in MongoDB, the REST API serves them, and Prometheus, Loki and Grafana watch all of it. Everything runs in the namespace `ca3` of a small k3s cluster on AWS.

Kubernetes does most of the work by itself: when a pod dies, it starts a new one. A person is needed when the new pod does not come back, when it comes back and fails again, or when something has to be changed on purpose (more pods, new certificates). This page lists those steps in the order a person does them. A command starts with `make` when the Makefile has a shortcut, and with `kubectl` when it has not. Every command needs the cluster to be up (`make cluster-up`, then `make up`).

What was tried, and what was not, is written down at the end (section 7). Where a step was run, the file with its output is named.

## 1. Look first: the same three questions every time

1. **Which pods are not as they should be?** Run `make status`. Look for a pod whose STATUS is not `Running` (a Job should say `Completed`), or whose READY column says `0/1`.
2. **Why?** Run `make diagnose`. For every pod that is not ready it shows the state, the events and the end of the log. It changes nothing.
3. **What did the program say?** Section 2.

What the STATUS words mean, and what to do:

| STATUS | What it means | What to do |
|---|---|---|
| `Pending` | no machine or no disk for the pod yet | `kubectl -n ca3 describe pod <name>`: the list of events at the bottom says why (not enough memory, a disk that is not bound) |
| `Init:0/1` | the small start-up program of the pod is still waiting for another part | it waits for Kafka (`wait-for-kafka`), for MongoDB (`wait-for-mongo`) or for both (`wait-for-kafka-and-mongo`); read it with `kubectl -n ca3 logs <name> -c <that name>` and fix the part it waits for first |
| `Running`, READY `0/1` | the program is up but the readiness check fails | `kubectl -n ca3 describe pod <name>` shows the failed check. The REST API pods are on purpose not ready while MongoDB is away |
| `CrashLoopBackOff` | the program stops right after it starts; Kubernetes waits longer before each new try (up to 5 minutes) | `kubectl -n ca3 logs <name> --previous` is the log of the run that just stopped |
| `ImagePullBackOff` | the container image cannot be fetched | wrong name or tag, or the image is not public: `make images-info` lists every image the manifests use |
| `Terminating` for a long time | the pod is shutting down (Kafka may take up to 120 seconds, MongoDB 60) | wait; only if it never ends, `kubectl -n ca3 delete pod <name> --force` |

## 2. Check the logs

The producer, the processor and the REST API write one JSON line per event, with the fields `ts`, `level`, `service` and `msg`. Kafka and MongoDB write their own formats, so their lines have no such fields.

| What you want | Command |
|---|---|
| the end of the processor log | `make logs` |
| the end of the log of any pod | `kubectl -n ca3 logs <pod> -c <container> --tail=50` (the container is called like the program: `kafka`, `mongo`, `processor`, `restapi`, `producer`) |
| the log of the run before a restart | `kubectl -n ca3 logs <pod> --previous` |
| all the error lines of the last minutes, from every pod, also from pods that are gone | Grafana, panel "Errors and warnings (all programs)"; or Explore with the data source Loki and the search `{namespace="ca3"} \|~ "(?i)(error\|exception\|traceback\|refused\|timed out)"` |
| one event through every program | `make smoke` sends a tagged event and prints its id; then `make logs-trace TRACE=<id>`; in Grafana type the id in the box "Follow one event" |

A pod that was deleted takes its own log with it. Loki keeps the lines for 24 hours, so the log of a pod that is gone can still be read, but only for a day (the same is true for the numbers in Prometheus). Take the screenshots of Grafana in that time.

`make operator-steps` runs the log commands once (and the two sections below) and saves the output in `evidence/step7-operator-steps.txt`.

## 3. Scale replicas

A replica is a means, not a goal. Decide first which number should get better, and look at it before and after:

| If this is the problem | Look at this number (Grafana, dashboard "CA3 pipeline") | What helps |
|---|---|---|
| events wait in Kafka for a long time | "Consumer lag: messages not read yet" | more processor pods (at most 3: the topic has 3 partitions, and one partition is read by one processor at a time) |
| the REST API answers slowly or with errors | "REST API: how long an answer takes" and "REST API: errors per second" | more REST API pods |
| a call is lost while a REST API pod is replaced | the goal `api-gap` in `slo.json` | at least 2 REST API pods (already the case) |

How each part is scaled:

- **REST API** (Deployment `restapi`, 2 pods in `restapi/restapi.yaml`, no autoscaler): `kubectl -n ca3 scale deployment/restapi --replicas=3`, and back with `--replicas=2`. A change made this way is not in the file, and `kubectl apply -k .` does not undo it (apply only sends what changed in the file). To keep a different number, change `replicas:` in the file.
- **Processor** (1 to 3 pods) and **load generator** (1 to 5 pods): the autoscalers in `processor/hpa.yaml` and `producer/load/hpa.yaml` own the number. A `kubectl scale` on them is undone within about 15 seconds. To change how many they may have, change `minReplicas` and `maxReplicas` in the file and run `kubectl apply -k .`.
- **Kafka and MongoDB** (one pod each): not scaled. A second Kafka broker or a MongoDB replica set needs a different set-up (see section 7).

`make scale-test` measures what the autoscalers do under load (the goals `scale-up`, `scale-down` and the others in `slo.json`). `make operator-steps` scales the REST API from 2 to 3 pods and back, once, and saves the output.

## 4. Certificates and passwords

Where they are: the Job `bootstrap` (`processor/bootstrap.py`) makes 13 Secrets inside the cluster when `make up` runs the first time: six passwords or logins, the certificate of a private certificate authority (the CA), and one certificate with its key for each of Kafka, MongoDB, the REST API, the producer, the processor and the Kafka metrics reader. Each program mounts only the Secrets it needs.

How to read what is needed to log in (print it only in your own terminal; do not paste it anywhere):

- Grafana: `make grafana-login`, then `make grafana` and open the address it prints.
- REST API: the user and password are in the Secret `restapi-login`; `make rest-check` reads them and calls the API for you.

How to see when a certificate ends: `make operator-steps` prints the end date of every certificate (the certificates are valid for 365 days, `VALID_DAYS` in `processor/bootstrap.py`). It reads only the certificate part of a Secret, never a key.

**Rotating a certificate: not run.** With this design it cannot be done while the data stays:

- the private key of the CA is never stored (the Job keeps it in memory and forgets it), so nobody can sign a new certificate for the old CA;
- the Job makes all 13 Secrets or none, and refuses when only some exist;
- the passwords are in the same Secrets, and MongoDB created its users once, when its disk was empty (the official image runs its set-up scripts only for an empty data directory), so a new password would not match the old disk.

The only way to new certificates today is `make down` (it deletes everything, also the disks) and `make up`; the replay Job then loads the sample flows again. A production system would use a tool such as cert-manager, which keeps the CA, renews every certificate before it ends, and lets the programs reload them. In this stack the change would be to keep the CA key somewhere safe and separate, and to keep the passwords out of the certificate Secrets. That is a limitation, written down on purpose.

## 5. Tier by tier: what the platform does, what a person checks

`make drill` deletes one pod in each tier with `kubectl delete pod <name>`, one tier after the other, and measures what the platform does. The measured numbers are in `evidence/step7-drill-results.md` and the second-by-second account in `evidence/step7-drill-timeline.txt`. The goals are in `slo.json` (`make goals` lists them with the verdict of the last run).

### Producer (Deployment `producer-load`, and the Jobs `producer-replay` and `producer-smoke`)

- **What the platform does:** the Deployment starts a new pod at once. Its start-up program waits until Kafka answers, then the producer connects over TLS.
- **What is lost for a moment:** events the load generator would have sent while it was gone. It replays the same sample file again and again, so nothing is owed. A pod of a Job that is deleted is replaced by its Job.
- **If the pod does not come back:** `kubectl -n ca3 logs <pod> -c producer`; the usual cause is Kafka (the producer needs its client certificate, Secret `kafka-client-producer`, and an answering broker). Grafana: "Producers sending", "Producer send errors per second".

### Kafka (StatefulSet `kafka`, disk `data-kafka-0`)

- **What the platform does:** the StatefulSet starts a new `kafka-0` when the old one is gone, with the same name and the same disk, so the messages are still there.
- **What stops for a moment:** nobody can send or read. The producers wait and try again, the processor loses its connection and joins the group again, an event sent by the smoke test waits in its Job.
- **If the pod does not come back:** `kubectl -n ca3 describe pod kafka-0` (is the disk bound? `kubectl -n ca3 get pvc`), `kubectl -n ca3 logs kafka-0 -c kafka`. Grafana: "Consumer lag: messages not read yet" grows while Kafka is away and falls when the processor catches up.

### Processor (Deployment `processor`, 1 to 3 pods)

- **What the platform does:** a new pod starts, waits for Kafka and MongoDB, and reads where the old one stopped. The processor saves its place in Kafka only after the records are stored, so an event can be stored twice after a crash, but never not at all.
- **If the pod crashes again and again:** `kubectl -n ca3 logs <pod> --previous`. The line "the batch was not stored" means MongoDB did not accept the records; look at MongoDB first. Kubernetes waits longer before each restart (up to 5 minutes), so a fixed MongoDB can take a few minutes to be noticed. Grafana: "Stored per second", "Processor waiting for MongoDB".

### MongoDB (StatefulSet `mongo`, disk `data-mongo-0`)

- **What the platform does:** a new `mongo-0` with the same disk; the records are still there.
- **What stops for a moment:** the processor cannot store (it stops and is restarted until MongoDB answers), and the REST API pods report "not ready" about 30 seconds after MongoDB is gone, so Kubernetes takes them out of the Service and the API cannot be reached from outside. They are ready again within about 10 seconds after MongoDB answers (the check runs every 10 seconds). With one MongoDB pod there is no way around this.
- **If the pod does not come back:** `kubectl -n ca3 describe pod mongo-0`, `kubectl -n ca3 logs mongo-0 -c mongo`, `kubectl -n ca3 get pvc`. Grafana: "Is everything alive?", "Open connections to MongoDB".

### REST API (Deployment `restapi`, 2 pods)

- **What the platform does:** a new pod starts when one is deleted; the other pod keeps answering, so a caller notices at most a call that was already on its way to the pod that went away (the goal `api-gap`).
- **If a pod does not become ready:** `kubectl -n ca3 describe pod <name>` shows the failed check. `/ready` asks MongoDB, so a pod that is not ready usually means a MongoDB problem. Grafana: "REST API: errors per second".

## 6. Prove that it is healthy again

After any fix, in this order:

1. `make status`: every pod `Running` and ready, every Job `Completed`.
2. `make smoke`: one tagged event goes through the producer, Kafka, the processor and MongoDB and comes back from the REST API.
3. `make rest-check`: the REST API answers over HTTPS with the login.
4. `make metrics-check`: Prometheus, Loki and Grafana answer and the dashboard is loaded.
5. `make heal-check`: deletes a pod and checks that it comes back with its data (the drill, `make drill`, does this in every tier and measures it).

`make rest-evidence` calls every REST endpoint and saves the request, the answer, the log lines and the numbers (`evidence/step7-rest-evidence.txt`).

## 7. What was not tested

- **A machine of the cluster failing.** The drill deletes pods, not machines. The disks of Kafka and MongoDB are local disks of one machine (k3s local-path), so if that machine is lost, its data is lost with it. Recovery would be `make cluster-up` for a new cluster and `make up`; this was not run.
- **A network failure** between the pods. The network policies are tested (`make netpol-check`), a network that breaks while the pipeline runs is not.
- **Rotating a certificate** (section 4), and **changing a password** while the data stays.
- **More than one Kafka broker or MongoDB node.** Both run as a single pod, so while one is being replaced the pipeline waits; no failover was built or tested.
- **A full disk** and **running out of memory** on a machine.
- **Timing on other hardware.** The numbers of `make drill` and `make scale-test` come from three `t3.medium` machines in one region on one day; another day or another size gives other numbers, which is why the goals in `slo.json` can be changed with `make goals`.

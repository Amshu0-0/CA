# Integrity Packet — CA3

**Owner: Amshu Wagle. I own this outcome.**

## Outcome

I took the CA2 pipeline (producer → Kafka → processor → MongoDB → REST API) and made it locked down, watched, able to grow under load, and tested by breaking it on purpose. Terraform and Ansible build a three-machine k3s cluster on AWS (`us-east-2`). One command, `kubectl apply -k .` (run by `make up`), creates the stack of 85 Kubernetes objects. `make teardown` removes the stack and the cluster, and then scans AWS to prove that nothing is left.

What CA3 adds, by grading category (the weights are the assignment's), and where this packet proves it:

| Category | What was built | Claims |
|---|---|---|
| Resilience drill (25%) | one pod deleted in each of five tiers (Kafka, processor, MongoDB, REST API, producer), with goals written as numbers; a runbook and the operator steps done once on the running stack; the video | C9, C10 |
| Observability (20%) | Prometheus, Grafana (one provisioned dashboard), Loki with Alloy; JSON logs that carry a `trace_id` | C1, C3 |
| Autoscaling (15%) | two autoscalers (producers 1 to 5, processors 1 to 3) and a scaling test with seven goals written as numbers | C4 |
| Security (15%) | 13 Secrets made inside the cluster and stored encrypted, TLS on Kafka and MongoDB, HTTPS and a login on the REST API, 25 NetworkPolicies, Pod Security `restricted`, access rules (RBAC), three open ports | C2, C5, C6, C7, C8 |
| Integrity Packet (15%) and documentation (10%) | this packet, [ai-guidance-log.md](ai-guidance-log.md), [README.md](README.md), and `make verify`, which checks that they agree with the repository | C13 |
| Rebuild and clean-up (CA3 asks for a repeatable, declarative setup) | second runs that change nothing; a rebuild from nothing; a scan of AWS before and after the teardown | C11, C12 |

The goal was not that the pipeline ran once, but that each property comes with evidence that would convince another engineer. So the opposite of each rule is tried (a plain connection to a TLS port, a request without a login, a pod deleted), a test has a control where that is possible (the NetworkPolicies are deleted and every connection opens), and the numbers are saved in `evidence/` as the terminal printed them. [README.md](README.md) explains how to run everything; this packet explains why I believe it works, and where it may not.

The demonstration video (2 min 58 s, unlisted on YouTube, link only, no video file in Git): <https://youtu.be/gmOuqr7P_TI>

## How to read a claim

Every claim below has the same lines: the assumptions it rests on (the table *Assumptions* has them), the evidence files, the command that makes the evidence, the result, the risk that remains, and *Verified by Amshu*. Then the lines that prove it, copied out of the evidence files.

- **Verified by Amshu** says only what I did myself: I ran the command on my own computer, against my own cluster or my own AWS account, and saved its output as the file named. It does not mean that I checked every sentence that Claude wrote around the evidence. The AI log says which checks were done by Claude alone, in a copy of the repository and not on my cluster.
- **The lines that prove it** are copied from the evidence files by a script. The file name is in the line above each box, the link under the box opens the whole file, and `make verify` checks that every quoted line is really a line of that file.
- Some evidence files come from different lifetimes of the cluster (the first build, a rebuild, the final stack). The packet says which one a file belongs to when it matters.

## Claims

### C1. One tagged event travels through every stage, and its id can be followed through the logs of three programs

- **Assumptions:** A3 (k3s provides the parts the stack needs), A4 (the images are public).
- **Evidence:** [step5-smoke.txt](evidence/step5-smoke.txt) and [step5-smoke-response.json](evidence/step5-smoke-response.json) (the smoke test, in six steps), [step5-trace.txt](evidence/step5-trace.txt) (the same id found in Loki), [step5-log-sample.txt](evidence/step5-log-sample.txt) (the newest line of each program and its labels) and the picture [images/step5-logs-one-event.png](images/step5-logs-one-event.png) (the three lines in Grafana). Earlier runs of the same test, on earlier lifetimes of the cluster: [step3-smoke.txt](evidence/step3-smoke.txt) and [step4-smoke.txt](evidence/step4-smoke.txt).
- **Validation:** `make smoke`, then `make logs-trace TRACE=<the id that the smoke test printed>`.
- **Result:** the event `smoke-1106-190230-548c` went from the producer to Kafka (partition 1, offset 1667), to the processor, into MongoDB and out of the REST API in 25 seconds. At that moment the consumer group was behind by 0 on every partition. The same id is in the log of the producer, of the processor and of the REST API.
- **Risk that remains:** one event proves the path, not that no event is lost under load (the scaling test and the drill count records for that). Kafka and MongoDB write their own log format and do not carry the id. The data are copies of the same 5,000 rows (A9).
- **Verified by Amshu:** I ran `make smoke` and `make logs-trace` on my cluster and saved their output in the files above. The picture is one I took in my own Grafana.

The lines that prove it:

<!-- from: evidence/step5-smoke.txt -->
```text
      trace id: smoke-1106-190230-548c
...
      Kafka stored the event in partition 1 at offset 1667 of topic network-flows
...
      answer: HTTP 200
...
RESULT: PASS - event smoke-1106-190230-548c went producer -> Kafka (partition 1, offset 1667) -> processor -> MongoDB -> REST API in 25 s
```

📄 [View the full text file: step5-smoke.txt](evidence/step5-smoke.txt)

<!-- from: evidence/step5-trace.txt -->
```text
  19:02:32.851  producer   info    {"ts":"2026-11-06T19:02:32.851+00:00","level":"info","service":"producer","msg":"sent","trace_id":"smoke-1106-190230-548c","topic":"network-flows","partition":1,"offset":1667}
...
  19:02:32.855  processor  info    {"ts":"2026-11-06T19:02:32.854+00:00","level":"info","service":"processor","msg":"stored","trace_id":"smoke-1106-190230-548c","topic":"network-flows","partition":1,"offset":1667}
...
  19:02:52.474  restapi    info    {"ts":"2026-11-06T19:02:52.474+00:00","level":"info","service":"restapi","msg":"request","method":"GET","path":"/events/smoke-1106-190230-548c","status":200,"duration_ms":9.3,"trace_id":"smoke-1106-190230-548c"}
...
RESULT: PASS - 3 log lines with this event id, from processor, producer, restapi: one id, one story across programs
```

📄 [View the full text file: step5-trace.txt](evidence/step5-trace.txt)

### C2. The REST API answers only over HTTPS, asks for a login, and refuses a client that does not trust the stack's own certificate authority

- **Assumptions:** A3, A11 (who can reach the node port).
- **Evidence:** [step4-rest-check.txt](evidence/step4-rest-check.txt) (all three nodes; the same check on the first cluster is [step3-rest-check.txt](evidence/step3-rest-check.txt) and on the rebuilt one [step3-rebuild-rest-check.txt](evidence/step3-rebuild-rest-check.txt)) and [step7-rest-evidence.txt](evidence/step7-rest-evidence.txt) (every endpoint with its request, its answer, the REST API's own log lines for it and what Prometheus counted, also for the refused requests).
- **Validation:** `make rest-check` (from my computer, to the node port of each of the three nodes) and `make rest-evidence`.
- **Result:** on each of the three nodes, `/health` and `/ready` answer without a login (Kubernetes asks them); `/stats` without a login and with a wrong password gives 401; a plain `http` request gets no answer; a client that does not trust the certificate authority is refused at the TLS handshake; with the login, `/stats` gives 200, `/alerts` gives 200 with 50 alerts, and an id that does not exist gives 404 with a JSON body. The last line is `PASS - 3 of 3 nodes`.
- **Risk that remains:** there is one shared login for all callers and no limit on wrong tries (not built; the password is compared in constant time). Basic authentication is only as safe as the TLS around it. The certificate is signed by the stack's own authority, so a client has to be given its certificate (the checks use it explicitly). The node port is open to my address only, and that is read from the firewall rule, not tried from a second address (A11).
- **Verified by Amshu:** I ran `make rest-check` and `make rest-evidence` from my computer against my cluster and saved the output.

The lines that prove it (the first node, then the result for all three):

<!-- from: evidence/step4-rest-check.txt -->
```text
node server (<public-ip>)
  GET /health  (no login)      -> 200 {"status":"ok"}
  GET /ready   (no login)      -> 200 {"status":"ready"}
  GET /stats   (no login)      -> 401 {"error":"login required"}
  GET /stats   (wrong password)-> 401 {"error":"login required"}
  GET /health  (plain http)    -> no answer, as it must be (RemoteDisconnected: Remote end closed connection without response)
  GET /health  (CA not trusted)-> refused by the client, as it must be (SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate)
  GET /stats   (with login)    -> 200 {"collection":"flows","database":"ca3","documents":5003}
  GET /alerts  (with login)    -> 200 count=50, first Label=Bot
  GET /events/no-such-id       -> 404 {"found":false,"trace_id":"no-such-id"}
...
RESULT: PASS - 3 of 3 nodes answered correctly (HTTPS only, login required) and MongoDB holds 5003 flows
```

📄 [View the full text file: step4-rest-check.txt](evidence/step4-rest-check.txt)

### C3. The monitoring sees every program, keeps the logs of all of them, and shows the numbers that the assignment names

- **Assumptions:** A3.
- **Evidence:** [step4-metrics-check.txt](evidence/step4-metrics-check.txt) (Prometheus, Loki and Grafana asked from inside their pods), [step4-grafana-after.png](evidence/step4-grafana-after.png) (the dashboard `CA3 pipeline`), [images/step6-grafana-scaling.png](images/step6-grafana-scaling.png) and [images/step7-grafana-recovery.png](images/step7-grafana-recovery.png) (the same dashboard during the scaling test and during the drill), the log searches [images/step5-logs-one-event.png](images/step5-logs-one-event.png), [images/step5-logs-warnings.png](images/step5-logs-warnings.png) and [images/step7-logs-errors.png](images/step7-logs-errors.png), and [step5-log-sample.txt](evidence/step5-log-sample.txt). The first version of the dashboard, with its mistake (failure N), is kept as [step4-grafana-before-1.pdf](evidence/step4-grafana-before-1.pdf) and [step4-grafana-before-2.pdf](evidence/step4-grafana-before-2.pdf).
- **Validation:** `make metrics-check`; `make grafana` opens the dashboard and `make logs-trace` searches the logs.
- **Result:** every one of the 7 Prometheus targets is up; the seven numbers named in the assignment exist (producer rate, consumer lag of the group `processor`, database inserts per second, events stored, REST requests per second, REST server errors per second, REST latency); Loki holds the logs of the 7 programs that the check asks for (12 programs in all), with level labels; Grafana has the dashboard and both data sources. The last line is `PASS - 4 of 4`.
- **Risk that remains:** the monitoring runs in the cluster it watches, so it is gone when the cluster is gone (the one outside check is `make rest-check`). Prometheus and Loki keep 24 hours on `emptyDir`, so a deleted pod takes its history. There is no Alertmanager: nothing sends a message when a number crosses a line. The metrics travel as plain HTTP inside the namespace. The numbers printed by the check are those of an idle stack; the load shows in the pictures of the scaling test and the drill.
- **Verified by Amshu:** I ran `make metrics-check` on my cluster and saved its output, and I took the dashboard and log pictures from my own Grafana.

The lines that prove it:

<!-- from: evidence/step4-metrics-check.txt -->
```text
[2/4] The numbers the assignment names exist
      producer rate, events per second (all producers)        0
      consumer lag of the group processor, messages           0
      database inserts per second (MongoDB's own count)       0
      events stored by the processor since it started         5000
      REST requests per second                                0.2545
      REST server errors per second (status 5xx)              0
      REST latency, 95% of the answers faster than (seconds)  0.0048
...
      programs with logs in Loki: alloy, bootstrap, grafana, kafka, kafka-exporter, loki, mongo, mongodb-exporter, processor, producer, prometheus, restapi
...
  PASS  prometheus every one of the 7 targets is up
  PASS  numbers    all 7 numbers exist
  PASS  loki       logs of 7 programs are in Loki, with level labels
  PASS  grafana    the dashboard is loaded and both data sources answer
...
RESULT: PASS - 4 of 4 monitoring checks passed
```

📄 [View the full text file: step4-metrics-check.txt](evidence/step4-metrics-check.txt)

### C4. The autoscalers add producer and processor pods under load and take them away afterwards, and each tier has a reason

- **Assumptions:** A3 (metrics-server), A5 (the topic has 3 partitions), A6 (CPU is the signal), A7 (the goals are my choice), A9 (the data are copies of 5,000 rows).
- **Evidence:** the scaling test, in [step6-scale-run.txt](evidence/step6-scale-run.txt) (the console), [step6-scale-results.md](evidence/step6-scale-results.md) (before and after, how pods were added and removed, the seven goals, what the numbers say), [step6-scale-chart.svg](evidence/step6-scale-chart.svg), [step6-scale-summary.json](evidence/step6-scale-summary.json), [step6-scale-samples.csv](evidence/step6-scale-samples.csv) and [step6-scale-goals.json](evidence/step6-scale-goals.json); `kubectl get hpa` every 15 seconds in [step6-scale-hpa-over-time.txt](evidence/step6-scale-hpa-over-time.txt) and `kubectl describe hpa` in [step6-scale-hpa.txt](evidence/step6-scale-hpa.txt); the producers' own log lines in [step6-scale-producer-log.txt](evidence/step6-scale-producer-log.txt); the pictures [images/kubectl-get-hpa.png](images/kubectl-get-hpa.png) and [images/step6-grafana-scaling.png](images/step6-grafana-scaling.png); the manifests [producer/load/hpa.yaml](producer/load/hpa.yaml) and [processor/hpa.yaml](processor/hpa.yaml); the goals in [slo.json](slo.json).
- **Validation:** `make scale-test` (about 7 minutes; it protects the disks and removes its load when it stops); `make scale-report` draws the table and the chart again from the saved samples.
- **Why these tiers.** In class the points were: replicas are a means, not an outcome; goals must be numbers that mean something for the project ("CPU utilization's easy, but it really isn't a great SLO"); scaling the wrong thing can slow things down; every extra copy costs. So each tier has a reason that comes from what the pipeline is for (an alert should be readable from the REST API soon after the traffic happened, and the pipeline should not fall behind), and the last column is checked by the test:

| Tier | Autoscaler | The reason | In this run |
|---|---|---|---|
| Producers (the load generator) | yes, 1 to 5, on CPU 50% | they stand for the many senders of traffic records, and a burst of senders is what the pipeline has to absorb; a producer in a tight loop is limited by its CPU | all 5 pods ready 41 s after the autoscaler was freed; back to 1 pod after 36 s |
| Processors | yes, 1 to 3, on CPU 70% | they decide how fast an event becomes readable, so they pay off a backlog; 3 is the ceiling because the topic has 3 partitions | backlog gone 54 s after the load stopped; a new event readable after 16.0 s (goal 60 s); back to 1 pod after 175 s |
| REST API | no, 2 fixed pods | it is the part a person uses; with two pods it met all three of its goals under full load and while a pod was replaced, so the numbers gave no reason for a third; two, so that one can be replaced while the other answers | 39 of 39 calls answered, 95% within 205 ms (goal 1,000 ms) |
| Kafka and MongoDB | no, 1 pod each | they keep state, and scaling them means replication, which is not built | CPU at 25% and 7% of their limits |

- **CPU, not the consumer lag.** In class either was accepted, but CPU "doesn't really tell you anything except your machine is busy" and a custom metric "means more". Both autoscalers here use CPU. For the producers that is the right signal. For the processors the lag would be better, because it says directly that events are waiting. I did not build it (it needs a metrics adapter, one more component to run and secure) and did not test it, so I cannot say how it would behave. The scaling test checks the lag-related goals, but the autoscaler does not use them. This is an assumption (A6) and an open risk.
- **Result:** the producers went from 1 to 5 pods and sent 566 → 2,644 messages per second (x4.7); MongoDB stored 551 → 1,796 per second (x3.3); 71,024 messages were waiting in Kafka at the end of the window and were gone 54 s after the load stopped; the processors went from 1 to 3 pods and back to 1; the REST API answered 39 of 39 calls; the processors were at 99% of their CPU limit. All 7 goals were met, and none was changed after the run.
- **Risk that remains:** replicas are a means, not a result: x4.7 producers gave x3.3 stored, the processors at 99% of their limit suggest that they were the limit, but one run does not prove a cause. The load generator and the pipeline share three small machines that were at 74% to 80% CPU, so the machines may be a limit too. The 'before' window was not a steady state (the processors were still scaling). It is one run, on copies of 5,000 rows. The lag-based autoscaler was not built.
- **Verified by Amshu:** I ran `make scale-test` on my cluster (the run started at 2026-11-06 21:16 UTC), saved its output, and took the dashboard picture during that run.

The lines that prove it:

<!-- from: evidence/step6-scale-results.md -->
```text
Run started 2026-11-06 21:16 (UTC). The load is the Deployment `producer-load`: producer pods that replay the dataset in a loop.
...
| Measurement | Before: 1 producer pod, the autoscaler held at 1 | After: 5 producer pods, autoscaler free | Change |
...
| Producer pods ready | 1 | 5 | x5.0 |
...
| Producers send, messages per second (all pods together, reported by the pods) | 566 | 2,644 | x4.7 |
...
| MongoDB stores, messages per second (growth of the record count) | 551 | 1,796 | x3.3 |
| Backlog in Kafka at the end of the window, messages | 1,432 | 71,024 |  |
...
| One tagged event: stored in Kafka until the REST API returns it, seconds | 0.1 | 16.0 | |
...
| Processor pods ready | 2 | 3 | x1.5 |
| REST API: calls answered with HTTP 200, percent (one call every 5 s from your computer) | 100.0 | 100.0 | |
| REST API: 95 percent of the answers came within (milliseconds, including the trip over the internet) | 201 | 205 |  |
...
| CPU of a processor pod, % of its CPU limit | 79 | 99 |  |
```

📄 [View the full text file: step6-scale-results.md](evidence/step6-scale-results.md)

`kubectl get hpa` at the top of the load, and again when everything had shrunk back:

<!-- from: evidence/step6-scale-hpa-over-time.txt -->
```text
=== 21:18:30 UTC   +145 s   phase: after   (producer pods ready 5, processor pods ready 3) ===
NAME            REFERENCE                  TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
processor       Deployment/processor       cpu: 493%/70%   1         3         3          3h49m
producer-load   Deployment/producer-load   cpu: 489%/50%   1         5         5          3h49m
...
=== 21:21:47 UTC   +342 s   phase: cooldown   (producer pods ready 1, processor pods ready 2) ===
NAME            REFERENCE                  TARGETS       MINPODS   MAXPODS   REPLICAS   AGE
processor       Deployment/processor       cpu: 6%/70%   1         3         3          3h53m
producer-load   Deployment/producer-load   cpu: 1%/50%   1         5         1          3h53m
```

📄 [View the full text file: step6-scale-hpa-over-time.txt](evidence/step6-scale-hpa-over-time.txt)

The goals, with what this run measured:

<!-- from: evidence/step6-scale-results.md -->
```text
| Goal | Asked | This run | |
|---|---|---|---|
| The REST API answers (HTTP 200) when it is asked, also while the pipeline is under full load | at least 99 percent of the calls | 100.0 percent (39 of 39 calls) | **met** |
| The REST API answers quickly, measured from your computer over the internet | at most 1,000 milliseconds, for 95 percent of the answers | 205 ms (slowest 210 ms) | **met** |
| A new event can be read from the REST API soon after Kafka has stored it, with one producer and with the autoscaler at its maximum | at most 60 seconds | one producer: 0.1 s, autoscaler at its maximum: 16.0 s | **met** |
| With the load on, the producers' autoscaler has all its pods ready | at most 180 seconds after the autoscaler was freed | all producer pods were ready after 41 s | **met** |
| After the load stops, the producers are back to the minimum of 1 pod | at most 180 seconds after the load was switched off | the producers were back to the minimum after 36 s | **met** |
| After the load stops, the processors catch up: the backlog in Kafka is below 1,000 messages | at most 300 seconds after the load was switched off | the backlog was gone after 54 s | **met** |
| After the load stops, the processors are back to the minimum of 1 pod | at most 600 seconds after the load was switched off | the processors were back to the minimum after 175 s (2.9 min) | **met** |
```

📄 [View the full text file: step6-scale-results.md](evidence/step6-scale-results.md)

### C5. Only the intended connections work between pods, and the NetworkPolicies are the reason

- **Assumptions:** A3 (k3s enforces NetworkPolicies; a policy that is not enforced only looks like security).
- **Evidence:** [step7-netpol-check.txt](evidence/step7-netpol-check.txt) (13 probe pods, 169 connections) and [step7-netpol-control.txt](evidence/step7-netpol-control.txt) (the control experiment); the same tests when the stack was smaller: [step3-netpol-check.txt](evidence/step3-netpol-check.txt) and [step3-netpol-control.txt](evidence/step3-netpol-control.txt) (the first version, twelve NetworkPolicy objects), [step4-netpol-check.txt](evidence/step4-netpol-check.txt) (after the monitoring was added); the first try of the 169-connection test, which failed (failure P): [step7-netpol-check-run1-failed.txt](evidence/step7-netpol-check-run1-failed.txt) and [step7-alloy-events.txt](evidence/step7-alloy-events.txt); the objects: [policies/networkpolicies.yaml](policies/networkpolicies.yaml) and [policies/observability.yaml](policies/observability.yaml); the picture [images/kubectl-get-networkpolicy.png](images/kubectl-get-networkpolicy.png).
- **Validation:** `make netpol-check` and `make netpol-control`. Both start their probe pods, clean up after themselves, and print a table.
- **Result:** the namespace denies all traffic in both directions, then one rule allows each connection that has to work (the README, section *Network and isolation*, has the table of all 25). 13 probe pods, each wearing the labels of one kind of pod (and one in another namespace), tried 169 TCP connections and compared each answer with what the policies say: 34 allowed, 135 blocked, no difference. The control shows that the policies are what blocks: with them a stranger in the namespace reaches nothing but DNS; with all of them deleted every connection opened (after at most 1 s); with them back the original answers returned (after at most 2 s).
- **Risk that remains:** the test covers TCP connections only: not UDP, not what a pod can send once it is connected, and not traffic to the internet. The REST API port accepts calls from any pod, also one from another namespace, because node-port traffic arrives that way (the API asks for a login). The policies trust labels: whoever may create pods in the namespace can give a pod the label of a trusted one. The two exporters are exceptions to "only the producers and the processor talk to Kafka, only the processor and the REST API talk to MongoDB", and the table names them. Two policy files pin the machines' subnet and the address of the Kubernetes API Service, so they are not portable.
- **Verified by Amshu:** I ran `make netpol-check` and `make netpol-control` on my cluster. The first try of the 169-connection test failed, and I saved its output and the events that explain it (failure P) before the fixed version passed.

The lines that prove it (the first table of the test, then the control experiment):

<!-- from: evidence/step7-netpol-check.txt -->
```text
Trying 169 connections (a blocked one can take up to 3 seconds to give up)...
...
The pipeline (78 connections):
  from              kafka:9092  mongo:27017  processor:8000  restapi:8443  DNS:53      k8s-api:443
  stranger          blocked     blocked      blocked         blocked       open        blocked
  intruder          blocked     blocked      blocked         open          open        open
  producer          open        blocked      blocked         blocked       open        blocked
  processor         open        open         blocked         blocked       open        blocked
  restapi           blocked     open         blocked         blocked       open        blocked
  observer          blocked     blocked      open            open          open        blocked
  bootstrap         blocked     blocked      blocked         blocked       open        open
  prometheus        blocked     blocked      open            open          open        blocked
  grafana           blocked     blocked      blocked         blocked       open        blocked
  loki              blocked     blocked      blocked         blocked       open        blocked
  alloy             blocked     blocked      blocked         blocked       open        open
  kafka-exporter    open        blocked      blocked         blocked       open        blocked
  mongodb-exporter  blocked     open         blocked         blocked       open        blocked
...
RESULT: PASS - all 169 connections behave as the policies say (34 allowed, 135 blocked)
```

📄 [View the full text file: step7-netpol-check.txt](evidence/step7-netpol-check.txt)

<!-- from: evidence/step7-netpol-control.txt -->
```text
Phase 1: the 25 policies are in place
...
  stranger  blocked     blocked      blocked         blocked       blocked          blocked       blocked     blocked              blocked
  intruder  blocked     blocked      blocked         open          blocked          blocked       blocked     blocked              blocked
...
Phase 2: no policies
...
  stranger  open        open         open            open          open             open          open        open                 open
  intruder  open        open         open            open          open             open          open        open                 open
...
  every connection was accepted after at most 1 s
...
Phase 3: the policies are back
...
  stranger  blocked     blocked      blocked         blocked       blocked          blocked       blocked     blocked              blocked
  intruder  blocked     blocked      blocked         open          blocked          blocked       blocked     blocked              blocked
...
  the original answers were back after at most 2 s
...
RESULT: PASS - without the policies every connection worked, with them only the allowed ones do: the policies are what blocks
```

📄 [View the full text file: step7-netpol-control.txt](evidence/step7-netpol-control.txt)

### C6. The passwords and certificates are not in Git, reach the programs as read-only files, are stored encrypted, and the access rules give the least rights

- **Assumptions:** A3 (k3s encrypts Secrets and ships the other parts), A12 (`.secrets/` stays on my computer).
- **Evidence:** [step10-security-facts.txt](evidence/step10-security-facts.txt) (names, modes and counts of the running cluster; the first run, which stopped at 5 of 6 because my check was too strict, is kept as [step10-security-facts-first-run.txt](evidence/step10-security-facts-first-run.txt), failure T), [step10-secrets-at-rest.txt](evidence/step10-secrets-at-rest.txt) (the cluster database read directly), [step9-secrets-encryption.txt](evidence/step9-secrets-encryption.txt) (k3s's own report), [step3-rbac.txt](evidence/step3-rbac.txt) with the picture [images/rbac-check.png](images/rbac-check.png), [step3-rebuild-status.txt](evidence/step3-rebuild-status.txt) and the picture [images/kubectl-get-secrets.png](images/kubectl-get-secrets.png) (the Secrets by name, never their content); the code that makes the Secrets: [processor/bootstrap.py](processor/bootstrap.py) and [bootstrap/job.yaml](bootstrap/job.yaml).
- **Validation:** `make rbac-check`; the security facts, the database look and the TLS checks (C7) were run from commands that Claude wrote and I pasted, and have no `make` command (README, *Known limitations*). `make verify` searches every file that Git would publish for a value from my `.secrets/` folder and fails if it finds one.
- **Result:** 13 Secrets are made inside the cluster by the Job `bootstrap`, which may only create Secrets, so no password or key was ever on a computer or in Git. The processor sees 6 Secret files, all `-r--r-----`, its environment holds only the path of the password file, and it has no Kubernetes API token. The namespace enforces Pod Security `restricted`: a privileged pod is refused by the API server. 13 of 13 Secrets are stored encrypted (AES-CBC) in the cluster database, with no readable word in them, and the control (the 9 ConfigMaps) is readable, so the method does show plain text when there is some. The nine access answers are: the viewer can list pods and not delete them; the operator can scale producers and not read Secrets; the processor cannot read Secrets; the setup Job can create Secrets and cannot read or change them, and cannot delete pods.
- **Risk that remains:** the encryption key is on the same machine as the database, so this protects a copy of the database without the key, not someone who can read the whole server. One Secret value (the login of the MongoDB metrics reader) is in an environment variable. The root file system of the containers is writable. There is no rotation: a certificate or a password cannot be replaced while the data stays. Whoever holds my kubeconfig is a cluster administrator and can read everything.
- **Verified by Amshu:** I ran the checks above on my cluster and saved their output. The kubeconfig and the SSH key stay in `.secrets/` on my computer, which Git ignores.

The lines that prove it:

<!-- from: evidence/step10-security-facts.txt -->
```text
/etc/pki/ca:
total 4
-r--r----- 1 0 1000 1172 Nov  8 15:01 ca.crt

/etc/pki/kafka-client:
total 12
-r--r----- 1 0 1000 1172 Nov  8 15:01 ca.crt
-r--r----- 1 0 1000 1229 Nov  8 15:01 tls.crt
-r--r----- 1 0 1000 1704 Nov  8 15:01 tls.key

/etc/secrets/mongo:
total 8
-r--r----- 1 0 1000 32 Nov  8 15:01 password
-r--r----- 1 0 1000  9 Nov  8 15:01 username
...
ls: cannot access '/var/run/secrets/kubernetes.io/serviceaccount': No such file or directory
...
OK: 13 Secrets exist
OK: the namespace ca3 enforces the Pod Security profile restricted
OK: a privileged pod is refused by the API server (dry run, nothing was created)
OK: the processor sees 6 Secret files, all read-only for owner and group (-r--r-----)
OK: the processor's environment holds the path of the password file (MONGO_PASSWORD_FILE), and no variable named like a password, key or token (GPG_KEY is set by the Python base image: it is the ID of a public key)
OK: the processor pod has no Kubernetes API token
...
RESULT: PASS - 6 of 6 checks passed
```

📄 [View the full text file: step10-security-facts.txt](evidence/step10-security-facts.txt)

<!-- from: evidence/step10-secrets-at-rest.txt -->
```text
SUMMARY secrets=13 encrypted=13 with_readable_words=0 configmaps=9 readable=9
...
OK: 13 of 13 Secrets are stored encrypted in the cluster database
OK: no stored Secret has a readable word in it (password, username, tls.key, ca.crt, BEGIN)
OK: the control: 9 of 9 ConfigMaps are stored readable, so this method does show plain text when there is some
...
RESULT: PASS - 13 Secrets are stored encrypted in the cluster database, and the control (ConfigMaps) is readable
```

📄 [View the full text file: step10-secrets-at-rest.txt](evidence/step10-secrets-at-rest.txt)

<!-- from: evidence/step3-rbac.txt -->
```text
pipeline-viewer   can list pods?       yes
pipeline-viewer   can delete pods?     no
pipeline-operator can scale producers? yes
pipeline-operator can read secrets?    no
processor pod     can read secrets?    no
bootstrap Job     can create secrets?  yes
bootstrap Job     can read secrets?    no
bootstrap Job     can change secrets?  no
bootstrap Job     can delete pods?     no
```

📄 [View the full text file: step3-rbac.txt](evidence/step3-rbac.txt)

### C7. Kafka demands TLS and a client certificate, MongoDB demands TLS and a login, and the opposite of each rule was tried

- **Assumptions:** A3.
- **Evidence:** [step10-tls-checks.txt](evidence/step10-tls-checks.txt) (six connections, run inside the processor pod with the processor's own settings); [kafka/kafka.yaml](kafka/kafka.yaml) and [db/mongo.yaml](db/mongo.yaml) (the settings); [step10-security-facts.txt](evidence/step10-security-facts.txt) (the Secrets that hold the certificates, by name).
- **Validation:** by hand, as in C6 (no `make` command).
- **Result:** a plain connection to Kafka got bytes back that are not a Kafka answer; Kafka over TLS without a client certificate was refused (`SSLV3_ALERT_BAD_CERTIFICATE`); Kafka over TLS with the processor's own certificate answered; a plain connection to MongoDB got no connection; MongoDB over TLS answered a ping without a login but refused to list the databases (`Unauthorized`, code 13). 6 of 6 behaved as required. The REST API's HTTPS is tested in C2.
- **Risk that remains:** there are no Kafka ACLs (every client with a certificate from the stack's authority can read and write every topic; the pipeline uses one topic). MongoDB clients use passwords, not certificates. With one broker there is no broker-to-broker traffic, so that part of the assignment's wording was not built or tested with two brokers. The metrics travel as plain HTTP inside the namespace. The certificates last 365 days and are not rotated.
- **Verified by Amshu:** I ran the six checks on my cluster and saved the output.

The lines that prove it:

<!-- from: evidence/step10-tls-checks.txt -->
```text
[1/6] Kafka, plain connection without TLS
...
      observed: bytes came back that are not a Kafka answer
...
[2/6] Kafka, TLS but no client certificate
...
      observed: TLS refused (SSLV3_ALERT_BAD_CERTIFICATE)
...
[3/6] Kafka, TLS with the processor's own certificate
...
      observed: the broker answered the Kafka question (error code 0)
...
[4/6] MongoDB, plain connection without TLS
...
      observed: no connection (ServerSelectionTimeoutError)
...
[5/6] MongoDB, TLS, ping without a login
...
      observed: MongoDB answered the ping
...
[6/6] MongoDB, TLS, list the databases without a login
...
      observed: MongoDB refused: Unauthorized (code 13)
...
RESULT: PASS - 6 of 6 connections behaved as required: nothing without TLS, no Kafka client without a certificate, no MongoDB data without a login; the right ones were answered
```

📄 [View the full text file: step10-tls-checks.txt](evidence/step10-tls-checks.txt)

### C8. Only three ports answer from outside, and only the REST API's port is meant for the application

- **Assumptions:** A11 (who can reach the ports).
- **Evidence:** [step3-exposure.txt](evidence/step3-exposure.txt) (every Service that is reachable from outside the cluster, then 11 ports tried on each of the 3 machines); [infra/terraform/security.tf](infra/terraform/security.tf) (the firewall rules); the Services in the manifests.
- **Validation:** `make exposure-check`.
- **Result:** on all three machines only 22 (SSH), 6443 (the Kubernetes API, on the server only) and 30080 (the REST API's node port) answer; the other 8 ports (etcd, the processor's metrics, the container port of the REST API, Kafka, the kubelet, MongoDB and two more node ports) are dropped. In the manifests exactly one Service is not a ClusterIP: `restapi`, node port 30080.
- **Risk that remains:** the check runs from my own computer, which is the one address the firewall lets in, so what another address sees is read from the rule, not tried (A11). The check ran on the first cluster (Step 3). The firewall file has had one commit, from Step 2, and `restapi` is still the only Service that is not a ClusterIP, but this check was not run again on the final stack. It is TCP only. Port 6443 is not judged on the two workers, because only the server runs the Kubernetes API.
- **Verified by Amshu:** I ran `make exposure-check` on my cluster and saved the output.

The lines that prove it:

<!-- from: evidence/step3-exposure.txt -->
```text
  port  what                                  server     agent-1    agent-2
  22    SSH                                   open       open       open
  2379  etcd                                  no answer  no answer  no answer
  6443  Kubernetes API                        open       no answer* no answer*
  8000  processor metrics                     no answer  no answer  no answer
  8443  REST API (container port)             no answer  no answer  no answer
  9092  Kafka                                 no answer  no answer  no answer
  10250 kubelet                               no answer  no answer  no answer
  27017 MongoDB                               no answer  no answer  no answer
  30080 REST API node port                    open       open       open
  30081 another node port                     no answer  no answer  no answer
  32767 last node port                        no answer  no answer  no answer

  * not judged: only the server runs the Kubernetes API, so the other machines have nothing to show on port 6443
...
RESULT: PASS - on all 3 nodes only 22 (SSH), 6443 (Kubernetes API, server only), 30080 (REST API node port) answer; the other 8 ports are dropped
```

📄 [View the full text file: step3-exposure.txt](evidence/step3-exposure.txt)

### C9. A pod deleted in each of five tiers is replaced, the stack is whole again within seconds, and no data is lost

- **Assumptions:** A7 (the goals are my choice), A8 (a deleted pod keeps its data; a lost machine does not).
- **Evidence:** the drill, in [step7-drill-run.txt](evidence/step7-drill-run.txt) (the console), [step7-drill-results.md](evidence/step7-drill-results.md) (a table per tier, the details, the five goals), [step7-drill-timeline.txt](evidence/step7-drill-timeline.txt) (the pods second by second), [step7-drill-events.txt](evidence/step7-drill-events.txt) (what Kubernetes said), [step7-drill-observability.txt](evidence/step7-drill-observability.txt) (what Loki and Prometheus still held), [step7-drill-rest-calls.csv](evidence/step7-drill-rest-calls.csv) (every call to the REST API during the drill), [step7-drill-summary.json](evidence/step7-drill-summary.json), [step7-drill-goals.json](evidence/step7-drill-goals.json) and [step7-drill-chart.svg](evidence/step7-drill-chart.svg); the pictures [images/step7-grafana-recovery.png](images/step7-grafana-recovery.png) and [images/step7-logs-errors.png](images/step7-logs-errors.png); the earlier self-healing test of the first version of the stack, [step3-heal.txt](evidence/step3-heal.txt); the video, <https://youtu.be/gmOuqr7P_TI>.
- **Validation:** `make drill` (about 6 minutes) and `make drill-report`, which draws the table, the goals and the chart again from the saved files.
- **Result:** one run (2026-11-07, 00:06 UTC) deleted one pod in each of the five tiers, and all five tiers `PASS`: the replacement was ready after 6 to 23 seconds, the whole stack after 8 to 25 seconds, and a tagged event sent right after the delete was readable from the REST API after 12 to 29 seconds. No record, no message and no disk was lost. Four tiers show no failed REST call at all; when `mongo-0` was deleted, 3 of 6 `/stats` calls failed over a stretch of about 8 seconds, which is what one database pod costs. All 5 drill goals were met.
- **Risk that remains:** the three time goals were first 150, 300 and 300 seconds and were tightened to 60, 90 and 90 after I saw the first results (the measurements did not change, only the thresholds), so read "met" as "within the numbers I chose". It is one run. Every drill deleted a pod, not a machine, and the disks of Kafka and MongoDB are local, so a lost machine would lose their data. Four of the five tiers were drilled on a quiet pipeline. A second Kafka broker or a MongoDB replica set was not built.
- **Verified by Amshu:** I ran `make drill` on my cluster, saved its output, took the two pictures, and recorded the video (Claude has not watched it).

The lines that prove it:

<!-- from: evidence/step7-drill-results.md -->
```text
Run at 2026-11-07 00:06 UTC, 5 of 5 tiers drilled. Every number is in seconds after the pod was deleted (`kubectl delete pod`).
...
| Tier | Pod deleted | New pod ready | Whole stack | Event readable | REST `/stats` calls failed | Data | Result |
|---|---|---|---|---|---|---|---|
| Kafka | `kafka-0` | 23 s | 25 s | 29 s | 0 of 29 | kept | **PASS** |
| Processor | `processor-6d4869f56b-8qsrh` | 14 s | 14 s | 14 s | 0 of 14 | kept | **PASS** |
| MongoDB | `mongo-0` | 13 s | 14 s | 13 s | 3 of 6 (8 s) | kept | **PASS** |
| REST API | `restapi-5f54fc9f7b-5gz55` | 7 s | 8 s | 13 s | 0 of 13 | kept | **PASS** |
| Producer | `producer-load-f877bb9f4-w2rlt` | 6 s | 16 s | 12 s | 0 of 16 | kept | **PASS** |
...
| Goal | Asked | This run | |
|---|---|---|---|
| A pod that is deleted is replaced by a new pod that is ready, in every tier | at most 60 seconds after the delete, for the slowest of the five tiers | kafka 23 s, processor 14 s, mongo 13 s, restapi 7 s, producer 6 s | **met** |
| After the delete, every part of the stack has its pods ready again (not only the replaced pod) | at most 90 seconds after the delete, for the slowest of the five tiers | kafka 25 s, processor 14 s, mongo 14 s, restapi 8 s, producer 16 s | **met** |
| An event that is sent while a pod is being replaced is not lost: it can be read from the REST API afterwards | at most 90 seconds after the delete, for the slowest of the five tiers | kafka 29 s, processor 14 s, mongo 13 s, restapi 13 s, producer 12 s | **met** |
| Deleting a pod deletes no data: every tier still has all the records and messages it had | at least 100 percent of the tiers | 100 percent (5 of 5 tiers kept all their data) | **met** |
| While one of the two REST API pods is replaced, the REST API keeps answering the calls for /stats | at most 5 seconds in a row without a good answer | longest stretch of failed /stats calls: 0.0 s (0 of 13 calls failed) | **met** |
```

📄 [View the full text file: step7-drill-results.md](evidence/step7-drill-results.md)

<!-- from: evidence/step7-drill-run.txt -->
```text
Goals in slo.json: 5 met, 0 NOT met, 0 not measured (5 goals).
...
RESULT: PASS - a pod was deleted in 5 of 5 tiers; each one was replaced, the stack was whole again and no data was lost
```

📄 [View the full text file: step7-drill-run.txt](evidence/step7-drill-run.txt)

### C10. The runbook's manual steps work on the running stack, and the file says what was not done

- **Assumptions:** A5 (3 partitions cap the processors).
- **Evidence:** [step7-operator-steps.txt](evidence/step7-operator-steps.txt) (2026-11-07, 02:20 UTC) and [runbook.md](runbook.md) (the person's page: look first, check the logs, scale replicas, certificates and passwords, tier by tier, prove that it is healthy again, what was not tested).
- **Validation:** `make operator-steps` does the steps of the runbook once on the running stack and changes nothing permanent; `make status` and `make diagnose` are the first two steps of the runbook.
- **Result:** the last lines of the log of each of the five programs could be read; an error search in Loki answered; the REST API (the only Deployment without an autoscaler) was scaled by hand from 2 to 3 pods and back, was ready after 8 seconds, and answered `/stats` with 3 pods; the end dates of seven certificates were read (364 days left on the day of the check). Every check says `ok`. Rotating a certificate was **not** done, on purpose, and the file gives three reasons.
- **Risk that remains:** the steps were run by me on a stack I knew, not by a person in an incident they did not cause. The processors and the producers cannot be scaled by hand, because their autoscalers undo it within about 15 seconds. Nothing rotates a certificate or a password. There is no Alertmanager, so a person has to notice that something is wrong.
- **Verified by Amshu:** I ran `make operator-steps` on my cluster and saved the output.

The lines that prove it:

<!-- from: evidence/step7-operator-steps.txt -->
```text
   ok:       Kafka: the log of kafka-0 can be read
...
   ok:       Processor: the log of processor-6d4869f56b-rjxqg can be read
...
   ok:       MongoDB: the log of mongo-0 can be read
...
   ok:       REST API: the log of restapi-5f54fc9f7b-87bbr can be read
...
   ok:       Producer (load generator): the log of producer-load-66dcbb6f55-xrpg5 can be read
...
   ok:       Loki answers the error search
...
   ok:       the REST API starts with all its pods ready (2 of 2)
...
   ok:       kubectl scale to 3 was accepted
       after 8 s: 3 wanted, 3 ready (3 pods)
   ok:       the extra pod became ready within 240 s (3 of 3 ready)
...
   ok:       the REST API answers /stats with 3 pods
...
       after 4 s: 2 wanted, 2 ready (2 pods listed)
   ok:       the REST API is back to 2 pods, all ready
...
   ok:       pki-ca: more than 30 days left
...
   ok:       kafka-tls: more than 30 days left
...
   ok:       mongo-tls: more than 30 days left
...
   ok:       restapi-tls: more than 30 days left
...
   ok:       kafka-client-producer: more than 30 days left
...
   ok:       kafka-client-processor: more than 30 days left
...
   ok:       kafka-client-exporter: more than 30 days left
...
4. Rotate a certificate: NOT DONE here, on purpose
...
This step was not run, so nothing here claims that it works.
```

📄 [View the full text file: step7-operator-steps.txt](evidence/step7-operator-steps.txt)

### C11. The cleanup scan sees the cluster while it exists, and finds nothing after the teardown

- **Assumptions:** A1 (AWS rights), A10 (Terraform's state file can be lost, so the scan asks AWS).
- **Evidence:** [step10-scan-before.txt](evidence/step10-scan-before.txt) (the scan on the running cluster), [step10-teardown.txt](evidence/step10-teardown.txt) (the teardown and the scan after it) and [step10-teardown-again.txt](evidence/step10-teardown-again.txt) (a second teardown with no cluster); the code is [scripts/cleanup-scan.py](scripts/cleanup-scan.py). The earlier versions of the scan, and the way it first believed stale tag lists (failure B), have no saved output.
- **Validation:** `make scan` (it only reads: exit 0 means nothing is left, 1 that something is left, 2 that it could not scan) and `make teardown` (deletes the stack, runs `terraform destroy`, scans again, and fails if anything is left).
- **Result:** with the cluster running, the scan found the machines, disks and network and ended `LEFTOVERS FOUND`, which shows that it can see. `make teardown` deleted the stack, Terraform destroyed 19 resources, and the scan ended `RESULT: CLEAN`. A second `make teardown` found no cluster, destroyed nothing, and the scan ended `CLEAN` again.
- **Risk that remains:** the scan looked at the region `us-east-2` only (the option for all regions exists and was not run). Its check of billable things knows five kinds. AWS's tag lists still named items that were already deleted; the scan asks AWS about each of them directly and says so. The saved scan files show the last four digits of my AWS account number. A cluster that costs money is only proved gone at the time of the scan.
- **Verified by Amshu:** I ran `make scan` with the cluster up, then `make teardown` twice, and saved all three outputs.

The lines that prove it:

<!-- from: evidence/step10-scan-before.txt -->
```text
CA3 cleanup scan
...
  region  : us-east-2
  tag     : Project=CS5287-CA3
...
  [FOUND] EC2 instances (3)
...
  [FOUND] EBS volumes (3)
...
  [FOUND] VPCs (1)
...
  [FOUND] Subnets (1)
...
  [FOUND] Security groups (2)
...
  [FOUND] Internet gateways (1)
...
  [FOUND] Route tables (1)
...
  [FOUND] Key pairs (1)
...
  [FOUND] Tagged resources (21)
...
  [FOUND] Tagged EC2 resources (21)
...
  [FOUND] EC2 instances (3)
...
  [FOUND] EBS volumes (3)
...
  [none ] Elastic IP addresses
  [none ] NAT gateways
  [none ] Load balancers
...
RESULT: LEFTOVERS FOUND - the resources listed above are still in AWS.
```

📄 [View the full text file: step10-scan-before.txt](evidence/step10-scan-before.txt)

<!-- from: evidence/step10-teardown.txt -->
```text
namespace "ca3" deleted
...
Plan: 0 to add, 0 to change, 19 to destroy.
...
Destroy complete! Resources: 19 destroyed.
...
  [none ] EC2 instances
  [none ] EBS volumes
  [none ] VPCs
  [none ] Subnets
  [none ] Security groups
  [none ] Internet gateways
  [none ] Route tables
  [none ] Key pairs
...
  [none ] Tagged resources
...
  [none ] Tagged EC2 resources
...
  [none ] EC2 instances
  [none ] EBS volumes
  [none ] Elastic IP addresses
  [none ] NAT gateways
  [none ] Load balancers
...
RESULT: CLEAN - nothing tagged Project=CS5287-CA3 is left, and no billable resources are running in us-east-2.
```

📄 [View the full text file: step10-teardown.txt](evidence/step10-teardown.txt)

<!-- from: evidence/step10-teardown-again.txt -->
```text
The cluster does not answer, so there is no stack to delete: going on to destroy the machines.
...
No changes. No objects need to be destroyed.
...
Destroy complete! Resources: 0 destroyed.
...
RESULT: CLEAN - nothing tagged Project=CS5287-CA3 is left, and no billable resources are running in us-east-2.
        AWS's tag lists still name 12 deleted items; each was asked for directly and does not exist, so none of them is a leftover.
```

📄 [View the full text file: step10-teardown-again.txt](evidence/step10-teardown-again.txt)

### C12. Building a second time changes nothing, and everything can be built again from the files

- **Assumptions:** A1, A4 (the images are public), A10, A13 (the reviewer's tools).
- **Evidence:** the first builds, [step3-cluster-up.txt](evidence/step3-cluster-up.txt) and [step3-up.txt](evidence/step3-up.txt); the second runs, [step9-cluster-up-run2.txt](evidence/step9-cluster-up-run2.txt) and [step9-up-run2.txt](evidence/step9-up-run2.txt); the dry runs, [step9-dry-run-server.txt](evidence/step9-dry-run-server.txt) and [step9-dry-run-client.txt](evidence/step9-dry-run-client.txt); the schema check, [step9-validate.txt](evidence/step9-validate.txt); the cluster destroyed and built again from the same files, [step3-rebuild-cluster-up.txt](evidence/step3-rebuild-cluster-up.txt), [step3-rebuild-nodes.txt](evidence/step3-rebuild-nodes.txt), [step3-rebuild-up.txt](evidence/step3-rebuild-up.txt), [step3-rebuild-status.txt](evidence/step3-rebuild-status.txt) and [step3-rebuild-rest-check.txt](evidence/step3-rebuild-rest-check.txt); the final cluster, [step9-get-all.txt](evidence/step9-get-all.txt) with the pictures [images/kubectl-get-all.png](images/kubectl-get-all.png) and [images/kubectl-get-nodes.png](images/kubectl-get-nodes.png); the tool versions, [step9-tool-versions.txt](evidence/step9-tool-versions.txt).
- **Validation:** `make cluster-up` twice, `make up` twice, `make dry-run` and `make validate`; for the rebuild, destroy the cluster and run `make cluster-up` and `make up` again.
- **Result:** the second `make cluster-up` said `No changes` and Ansible `changed=0` on all three machines (27 s). The second `make up` said `unchanged` for all 85 objects, with none created or configured (9 s). Both dry runs said `unchanged` for all of them. `make validate` found all 85 objects valid. The cluster was destroyed and built again from the same files: 19 resources added, the 45 objects of the first version of the stack created, and `make rest-check` passed on 3 of 3 nodes.
- **Risk that remains:** the first run of the final stack of 85 objects from nothing (done on Friday and again on Sunday) was not saved; what is saved is the second run and the state of the Sunday cluster. The rebuild that is saved is the earlier, smaller version of the stack. No saved run shows the setup Job running again against Secrets that already exist (the program is written to handle it, but no run shows it). The repository has not been deployed again since Sunday morning; since then only the saved evidence, a `.gitignore`, these documents, the help texts of the `Makefile` and the comments of the scripts changed. Everything was done on macOS.
- **Verified by Amshu:** I ran the second runs and the dry runs on my cluster and saved the output, and I destroyed and rebuilt the cluster myself.

The lines that prove it:

<!-- from: evidence/step9-cluster-up-run2.txt -->
```text
No changes. Your infrastructure matches the configuration.
...
Apply complete! Resources: 0 added, 0 changed, 0 destroyed.
...
PLAY RECAP *********************************************************************
ca3-agent-1                : ok=8    changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
ca3-agent-2                : ok=7    changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
ca3-server                 : ok=13   changed=0    unreachable=0    failed=0    skipped=1    rescued=0    ignored=0
...
make cluster-up took 0 min 27 s
```

📄 [View the full text file: step9-cluster-up-run2.txt](evidence/step9-cluster-up-run2.txt)

<!-- from: evidence/step9-up-run2.txt -->
```text
namespace/ca3 unchanged
serviceaccount/alloy unchanged
serviceaccount/bootstrap unchanged
...
Stack is up. Call the REST API from this computer with:  make rest-check   and check the monitoring with:  make metrics-check
make up took 0 min 9 s
```

📄 [View the full text file: step9-up-run2.txt](evidence/step9-up-run2.txt)

<!-- from: evidence/step9-validate.txt -->
```text
Summary: 85 resources found parsing stdin - Valid: 85, Invalid: 0, Errors: 0, Skipped: 0
...
make validate took 0 min 2 s
```

📄 [View the full text file: step9-validate.txt](evidence/step9-validate.txt)

The cluster built again from the same files:

<!-- from: evidence/step3-rebuild-cluster-up.txt -->
```text
Apply complete! Resources: 19 added, 0 changed, 0 destroyed.
...
ca3-agent-1                : ok=10   changed=5    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
ca3-agent-2                : ok=9    changed=5    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
ca3-server                 : ok=15   changed=7    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
```

📄 [View the full text file: step3-rebuild-cluster-up.txt](evidence/step3-rebuild-cluster-up.txt)

<!-- from: evidence/step3-rebuild-rest-check.txt -->
```text
RESULT: PASS - 3 of 3 nodes answered correctly (HTTPS only, login required) and MongoDB holds 5000 flows
```

📄 [View the full text file: step3-rebuild-rest-check.txt](evidence/step3-rebuild-rest-check.txt)

### C13. The documentation agrees with the repository, and nothing private was published

- **Assumptions:** A12 (`.secrets/` stays on my computer), A13 (the reviewer's tools).
- **Evidence:** [scripts/verify_manifests.py](scripts/verify_manifests.py) (its section 9 checks the documents), [scripts/scrub-evidence.py](scripts/scrub-evidence.py) (replaces public addresses, host names and home folders in the evidence), [step9-tool-versions.txt](evidence/step9-tool-versions.txt) (the versions behind the README's *Prerequisites* table). `make verify` has no saved output file.
- **What `make verify` checks about the documents:** that the three documents exist and the screenshot is a PNG; that no placeholder is left; that the required sections exist; that every link exists and stays inside `CA3/`; that every file name and every `make` command in the documents is real; that every quoted line is a line of the file named above it; that the counts of objects, NetworkPolicies, AI-log recommendations and mistakes in the documents are right; that every file in `evidence/` is listed in this packet; that no evidence file or document holds a public address, a host name with an address, an account number, a home folder or a key; and that no value from `.secrets/` is in a file that Git would publish.
- **Validation:** `make verify` (needs `kubectl` and Python 3 with PyYAML; no cluster and no AWS). The commit that adds these three documents is made by a block that runs `make verify` first and stops if it fails.
- **Result:** Claude ran `make verify` on a copy of the repository with these documents in place, and it ended with `0 failed, 0 warnings, 0 still to do`. The commit block runs it again on my computer.
- **Risk that remains:** `make verify` shows that the files agree with each other. It cannot tell whether a sentence is well written or whether I understood it, and its secrets check only knows the secrets that exist on the computer that runs it. One early commit (`b5ae817`) held unscrubbed output (my home address and folder names); `7727eae` replaced it, but the old commit stays in the history of the repository. The saved scan files show the last four digits of my AWS account number.
- **Verified by Amshu:** the block that commits these documents runs `make verify` on my computer and stops if it fails; the public repository is mine.

## Assumptions

Each assumption is written so that it can be tested. The last column says what happened.

| # | Assumption | Why it matters | How it was tested | What happened |
|---|---|---|---|---|
| A1 | I work as a dedicated IAM user (not root) who may create and delete EC2 and VPC resources in `us-east-2` and list resources by tag. | Wider or narrower rights would change what the build and the scan can do. | `make cluster-up` created 19 resources and `make teardown` destroyed them; the scan's tag-index check ran (`Tagged resources (21)` while the cluster existed). | Held. In CA2 the tag permission was missing and cost a point; in CA3 it is there, and the scan has a second check that needs no extra permission. |
| A2 | My public internet address can change while the cluster exists. | The firewall lets SSH, the Kubernetes API and the REST API in from one address; an address typed into a file goes stale. | Terraform looks the address up on every run (`admin_cidr: auto`); the scripts' hint for a failed connection names `make cluster-up` (failure D), and the README's *Troubleshooting* says the same. | Not shown in the saved files of CA3. CA2 saw a real change (October 14) and CA3 uses the same rule, so this rests on that. |
| A3 | k3s provides what the stack needs: DNS, metrics-server, local-path storage, encrypted Secrets and a NetworkPolicy controller that enforces the policies. | A policy that is not enforced only looks like security, and an autoscaler without metrics does nothing. | 169 connections and a control experiment (C5); both autoscalers read metrics-server (C4); the Secrets are encrypted (C6); the disks are `local-path` volumes (C9). | Held. |
| A4 | The three application images are public on `ghcr.io` and can be pulled without a login. | A private image needs a pull secret, which is one more secret in the cluster. | Every pod started on a rebuilt cluster with no pull secret (C12). | Held. |
| A5 | The topic has 3 partitions, so more than 3 processors would not help. | It sets the maximum of the processors' autoscaler. | The processors went to 3 and stopped there. | Held by design. Four processors were not tried. |
| A6 | CPU is a good enough signal for both autoscalers. In class either CPU or a custom metric such as the consumer lag was accepted, and a custom metric was preferred. | For the processors the lag says directly that events are waiting; CPU says only that the processor is busy. | The scaling test checks the goals that matter (the backlog is gone, a new event is readable soon); the autoscalers do not look at them. A lag-based autoscaler was not built. | Held for the goals I set (all 7 met). Open as a design choice (C4). |
| A7 | The numbers in `slo.json` are my choice, not a standard. | A goal that was moved to fit the result proves nothing. | Each goal has a sentence saying why. The scaling goals were not changed after the run. The three time goals of the drill were tightened from 150, 300 and 300 seconds to 60, 90 and 90 after I saw the first results. | Open. Read "met" as "within the numbers I chose" (C9). |
| A8 | A deleted pod keeps its data (the new pod uses the same disk on the same machine); a lost machine does not. | It decides what "self-healing" can honestly mean here. | The drill and `make heal-check` compare the disks and the data before and after a delete. | Held for pods. Losing a machine was not tested (C9). |
| A9 | The 5,000-row sample, replayed in a loop, is enough to show that the pipeline is correct and what shape scaling has, not how it behaves on real traffic. | The full dataset has about 191,000 rows. | Not tested at full size. The tests produced hundreds of thousands of records, but they are copies of the same 5,000 rows, so a record count is not a count of different flows. | Open (README, *Known limitations*). |
| A10 | Terraform's local state file is the only memory of what it created, and it can be lost or belong to another folder. | Without it `terraform destroy` cannot find the resources. | `make scan` asks AWS directly. It was tried in both directions: it finds the cluster while it exists and nothing after the teardown (C11). | Held. |
| A11 | Only my address needs to reach SSH, the Kubernetes API and the REST API's node port. | A published port is a way in. | The ports were probed from my computer (C8). The rule that limits them to my address (`rest_api_cidr: admin`) was read in `infra/terraform/security.tf`, not tried from a second address. | Held for my address. What other addresses see is not measured. |
| A12 | `.secrets/` (the kubeconfig and the SSH key) stays on my computer and out of Git, and no password exists on any computer, because the Job makes them inside the cluster. | It is the base of the secrets claim (C6). | `.gitignore` lists `.secrets/` (`make verify` checks the line), and `make verify` searches every file that Git would publish for values from `.secrets/`. | Held. |
| A13 | A reviewer has `make`, `kubectl` and Python 3 with PyYAML (and, to rebuild everything, Terraform, Ansible and AWS credentials; `make validate` also needs `kubeconform`). | `make verify` needs no cluster, but it needs these. | If PyYAML is missing, `make verify` stops and prints the command that installs it. The tool versions are in [step9-tool-versions.txt](evidence/step9-tool-versions.txt). | Held on macOS only. Linux and Windows were not tried. |

## Failures found and fixed

A packet that shows only success is not evidence. These are the failures that mattered most, in the order they happened. The AI log ([ai-guidance-log.md](ai-guidance-log.md), second table) holds 24 mistakes, with the way each was found. Each row follows the same path: what I expected (the baseline), what the test showed (the failure), the correction, the evidence, and what is left.

| Mistake | What I expected, and what the test showed | The correction | Evidence, and what is left |
|---|---|---|---|
| A | MongoDB with TLS switched on would start. The first real `make up` timed out after 7 min 15 s: `mongo-0` was in `CrashLoopBackOff`, and its log said that TLS without a named chain of trust is no longer supported (error 72). Everything that only reads files (`make verify`, the schema check, a pretend cluster) had passed. | The certificate authority file is named in `db/mongo.yaml`, and `make verify` now fails when MongoDB has TLS and names no such file. | [step3-up-run1-failed.txt](evidence/step3-up-run1-failed.txt) and [step3-diagnose-run1-failed.txt](evidence/step3-diagnose-run1-failed.txt), then [step3-up.txt](evidence/step3-up.txt) (45 objects, 1 min 15 s). Left: a check that reads files cannot show that a program starts. |
| B | The cleanup scan would say `CLEAN` when AWS is clean. On real AWS it reported leftovers that no longer existed, because AWS's tag lists keep naming deleted items for minutes (more than eight minutes in the first test) while the checks that ask EC2 directly already said none. | Every item that the tag lists name is asked for by its id, and only AWS's own answer "not found" counts as gone; such items are shown, not hidden. | [step10-teardown.txt](evidence/step10-teardown.txt): "AWS's tag lists still name 12 deleted items; each was asked for directly and does not exist". Left: the outputs of the early versions were not saved. |
| E | Evidence would be scrubbed before it was published. Raw output, with my home address and folder names, was committed and pushed first (`b5ae817`). Found by looking at the pushed files. | `7727eae` replaced the files; `scripts/scrub-evidence.py` now runs before every commit, and section 9 of `make verify` looks for addresses, host names, account numbers and home folders in the evidence and the documents. | The scrub is part of the commit blocks. Left: the old commit stays in the history of the repository. |
| L | A block that must not be run yet would be skipped. It sat in a runnable box; I pasted it after the teardown and the cluster was built again by accident (19 resources). | A block that must not be run now never appears as a runnable block. A block for a problem checks first and refuses with a `STOP:` message, and sits under a table row that says when to use it. | No saved output. Left: the rule is a way of writing the instructions; no test enforces it. |
| N | The dashboard shows what the assignment asks for. Claude described two graphs (REST requests per second, log lines per second) without having seen a picture of them. In my first pictures they were stacked, so idle health checks looked like a solid band and the 401 and 404 lines were invisible. | Stacking switched off in the dashboard file, applied by an exact-match patch. `make verify` cannot see this kind of fault. | [step4-grafana-before-1.pdf](evidence/step4-grafana-before-1.pdf) and [step4-grafana-before-2.pdf](evidence/step4-grafana-before-2.pdf) against [step4-grafana-after.png](evidence/step4-grafana-after.png). Left: only looking at the real picture finds this. |
| O | A search for the word `error` in Loki would come back empty on a healthy stack. It never does: MongoDB writes about five lines a second that say the metrics reader's request was refused ("Checking authorization failed"), and the field name in those lines is `error`. Found by reading my real Grafana picture. | The search leaves those lines out, and the README and the operator steps say why. Widening the metrics reader's rights, to stop the refusals, was rejected. | [images/step7-logs-errors.png](images/step7-logs-errors.png) and [step7-operator-steps.txt](evidence/step7-operator-steps.txt). Left: the refusals are real, and a search for `error` still finds them. |
| P | The 169-connection test's helper pod for Alloy would run like the other 12. The first real run failed twice ("the probe pods did not start within 180 s"; `probe-alloy` was gone). The events show why: the Alloy DaemonSet adopted the helper pod, which wore Alloy's label and had no owner, and deleted it as one pod too many. A pretend cluster has no controllers, so no earlier test could see it. | The helper pod is created with an owner reference to its own namespace, so no controller adopts it. The next run passed: 169 connections, as the policies say. | [step7-netpol-check-run1-failed.txt](evidence/step7-netpol-check-run1-failed.txt) and [step7-alloy-events.txt](evidence/step7-alloy-events.txt), then [step7-netpol-check.txt](evidence/step7-netpol-check.txt). Left: a test pod that wears a real pod's label must be safe against every controller that selects it. |
| Q | The drill's producer row says 16 s until the whole stack is ready, while the new pod was ready after 6 s. Claude explained it wrongly twice: first as the processors' autoscaler starting a pod (the events show that pod came after the 16 s), then as Prometheus's reading interval (the drill reads the new producer's own `rate` log line, which the producer writes every 5 s). | The explanation was replaced by the one that the code and the data show: the drill counts the producer as whole when its first new rate line has been seen, 15.3 s after the delete. | [step7-drill-events.txt](evidence/step7-drill-events.txt), [step7-drill-summary.json](evidence/step7-drill-summary.json) and [scripts/drill.py](scripts/drill.py). Left: the 16 s is a property of how the drill measures, not of Kubernetes. |
| T | The security-facts check would pass on a hardened cluster. It stopped at 5 of 6: it took `GPG_KEY` in the processor's environment for a password-like name. The Python base image sets that variable; it holds the id of a public key. | The check names the variable and says why it is not a secret. The pods were not changed. | [step10-security-facts-first-run.txt](evidence/step10-security-facts-first-run.txt) (`5 of 6`) against [step10-security-facts.txt](evidence/step10-security-facts.txt) (`6 of 6`). Left: my check was too strict, not the cluster wrong, and it still looks at names only. |
| X | The saved scan files are scrubbed like the other evidence. The scan prints the last four digits of the AWS account number, and the scrub script does not remove them. Found while preparing the quotes for these documents: the program that copies them out refuses to quote such a line. | The README's *Known limitations* say so, and the copying program never quotes the line. | [step10-scan-before.txt](evidence/step10-scan-before.txt). Left: the four digits stay in the saved scan files. |

## Escalation path

This system does not repair itself in every case, and I do not want it to look as if it did. These are the places where a person stays in the loop on purpose, instead of letting code guess or retry:

- **A `make cluster-up` that stops half-way** is not rolled back or retried. Terraform's state shows what was created, and running `make cluster-up` again continues from there (C12). `make scan` shows what AWS holds in the meantime.
- **A pod that does not become ready** stops `make up` with an error after its wait. Kubernetes restarts a crashed container by itself, but nothing decides to try something else. The first real `make up` ended this way (failure A): after 7 min 15 s it printed "Read the first error above, then run: make diagnose". `make diagnose` only reads: it prints the node states, the pods that are not ready, their events and logs, and no Secret. A person decides what to do next.
- **The scaling test stops itself when the cluster is in danger:** after 1,000,000 messages, after 30 minutes, when `kafka-0` or `mongo-0` restarts, and on Ctrl-C. `make scale-up` has none of these protections, so a person has to run `make scale-down`; producers at their CPU limit fill the disks.
- **The drill** stops and cleans up on Ctrl-C. It never deletes the monitoring pods, because they are what watches the drill.
- **The autoscalers stop at their maximum** (5 producers, 3 processors). Nothing raises an alarm when the backlog in Kafka keeps growing (71,024 messages at the end of the scaling window). There is no Alertmanager, so a person has to watch the dashboard and decide whether the topic needs more partitions.
- **A lost machine** is not handled. The disks of Kafka and MongoDB are on the machine, so they go with it. A person decides whether to rebuild (`make cluster-up`, then `make up`), and the sample data are loaded again.
- **Certificates and passwords** are not renewed or rotated by anything. `make operator-steps` reads the end dates (364 days left on the day it ran). Before they pass, a person must rebuild the stack (`make down`, then `make up`), which also deletes the data (C10).
- **The monitoring lives in the cluster it watches.** When the cluster is gone, so is the monitoring. The one check from outside is `make rest-check`, run by a person.
- **`make scan` has three outcomes:** exit 0 (nothing left), 1 (something is left) and 2 (it could not scan). It never says `CLEAN` when a required check did not run. A leftover is for a person to read and remove; the scan deletes nothing. `make teardown` fails, with a non-zero exit, if anything is left.
- **Who may create pods in the namespace** needs human review: the NetworkPolicies trust pod labels, and closing that gap needs an admission policy.
- **Nothing here decides "my AWS bill is too high" or "I am done working".** The machines cost about $0.15 an hour together, and `make teardown` is a separate, explicit command that never runs on a timer.

## Risks that remain

The README lists every known limitation one by one. These are the ones I would fix first:

- **One Kafka broker and one MongoDB, with their disks on one machine.** Losing that machine loses the data, and while a pod is away that part is away: the drill measured 23 seconds for Kafka and 13 for MongoDB (C9). A real system would replicate and use storage that is not tied to a machine. The assignment's "broker-to-broker" TLS was not built or tested, because there is only one broker.
- **The autoscalers use CPU, not the consumer lag.** In class either was accepted and a custom metric was preferred. The processors would be better served by the lag; I did not build it, and I cannot say how it would behave (C4, A6).
- **The measurements describe a small cluster.** The load generator and the pipeline share three small machines (74% to 80% CPU during the test), the data are copies of 5,000 rows, each test was run once, the 'before' window of the scaling test was not a steady state, and the cause of x4.7 producers giving x3.3 stored records is not proved.
- **The goals are my choice.** The scaling goals were fixed before the run and not changed. The three time goals of the drill were tightened after I saw the first results, so they are weaker evidence (C9, A7).
- **No rotation, no Kafka ACLs, MongoDB clients with passwords.** A certificate or a password cannot be replaced while the data stays; every client with a certificate can read and write every topic; the encryption key of the Secrets sits on the same machine as the database; one Secret value is in an environment variable; the root file system of the containers is writable (C6, C7).
- **NetworkPolicies trust labels, and the test covers TCP only** (C5). Two policy files pin the machines' subnet and the address of the Kubernetes API, so they are not portable to another cluster.
- **The monitoring is in the same cluster, keeps 24 hours on `emptyDir`, and has no Alertmanager** (C3).
- **Three of the security checks have no `make` command.** The TLS checks, the security facts and the look into the cluster database were run from commands that Claude wrote and I pasted; the files show what each asked, but the commands are not in the repository (C6, C7).
- **Some history is not clean.** One early commit holds unscrubbed output and stays in the history; the saved scan files show the last four digits of my AWS account number (C11, C13).
- **The first run of the final stack from nothing was not saved,** only the second run and the state of the cluster. The saved rebuild is the earlier, smaller version of the stack (C12).
- **Everything was tested on k3s from a Mac.** It was not tried on another Kubernetes distribution or another operating system.
- **A running cluster costs money.** `make teardown` stops it and the scan proves it (C11); it is the last thing I did.

## AI-assisted work

**Disclosure.** I used Claude (Anthropic) throughout CA3. Claude designed and wrote the first version of nearly everything in this folder: the manifests, the three programs, the Terraform and Ansible changes, the scripts and the Makefile targets, the checks in `make verify`, and, from the saved files in `evidence/`, these three documents (a script that Claude wrote copies the quoted lines out of the evidence files; the script is not in the repository). Claude had no access to my cluster or my AWS account and ran nothing on them. I ran every command on my own computer and sent the terminal output back before the next step. Claude tested its own work in copies of the repository, with a Kubernetes API server that has no machines, with pretend clusters and with stand-in programs, and the AI log says for each row what was tested where.

**The AI log** ([ai-guidance-log.md](ai-guidance-log.md)) holds 44 consequential recommendations, each with the official page to read and what was tested and what was not. 41 of those rows carry a *Verified by Amshu* line, which means that I ran the check on my own cluster or AWS account and saved its output. For the other rows the log says that Claude tested the thing in a copy of the repository, and what no one tested.

**Accepted, changed, rejected.**

- *Accepted:* all 44 recommendations are in the repository.
- *Changed after a test or a reading showed a problem:* 24 pieces of Claude's output (the second table of the AI log), for example the missing certificate authority file of MongoDB, the cleanup scan that believed stale tag lists, the stacked graphs, the helper pod that a controller deleted, and a security check that was too strict.
- *Rejected or left unbuilt:* I did not reject a recommendation as a whole. Some things were deliberately not built, and the README names each as a limitation: certificate rotation, Kafka ACLs, replication of Kafka and MongoDB, a read-only root file system, an admission policy, an Alertmanager, and a lag-based autoscaler (Claude recommended against building it in the time left, and I did not ask for it).
- *Where the AI was weakest:* claims about the real cluster written before the cluster existed or before the evidence was in hand (A, N, O, P), explanations of measurements that were guesses (Q), checks that were too strict (T), time estimates (V, W) and statements about what a document says (H, X). I treat a sentence from the AI about the real world as unproven until it appears in a file in `evidence/`.
- *How the mistakes were found:* the second table of the AI log says. Most were first seen in my real runs, my pictures or my terminal output. One (F) was found by Claude's review of a script that I already had, and a few (G, I, J, K, R, S, U) were caught by Claude before they reached me. All of them are counted.

**Blocks pasted into the terminal.** In three blocks of one step, the text in my terminal was not Claude's text letter for letter (a comment missing, shorter messages, re-wrapped lines). The results were identical, and the cause is not known. So what the AI log says Claude tested covers Claude's text. The patch files are protected by a sha256 check and by a comparison of the files they touch, so a difference in a comment cannot change the repository.

**What I should not do with the AI.**

- Treat a sentence of the AI about the real world as true before a file in `evidence/` shows it. The mistakes above are what happens otherwise.
- Let it touch the cluster or the AWS account. Every command was run by me, and the output was read before the next step.
- Trust a check that only reads files to show that a program starts (failure A), or a pretend cluster to show what real controllers do (failure P).
- Give it a password or a key. None was needed: the Job makes them inside the cluster.

**Prompts and critiques.** I did not keep the prompts as separate files. The AI log holds, for each recommendation, the critique (what was tested and what was not) and the outcome; the git history shows the order of the work; and the failed attempts are kept in `evidence/` instead of being deleted.

## Ownership

I own this outcome. I ran every command whose output is in [`evidence/`](evidence/) on my own computer and my own AWS account, I saved that output, and I am responsible for what this repository says. If any part of it is wrong, that is on me, not on the tools I used to build it.

## Evidence index

Every file in [`evidence/`](evidence/) is listed here with what it holds (73 files). They are the saved terminal output of real runs unless the description says otherwise: the `.png` and `.pdf` files are pictures I took, and the `.md`, `.svg`, `.json` and `.csv` files of the scaling test and the drill are written by `make scale-test`, `make drill` and their report commands from the measurements. The pictures that are not in `evidence/` are in `images/`, and the README shows them.

| File | What it holds |
|---|---|
| [step10-scan-before.txt](evidence/step10-scan-before.txt) | The cleanup scan on the running cluster: it finds the machines, disks and network (`LEFTOVERS FOUND`, as it must while the cluster exists). |
| [step10-secrets-at-rest.txt](evidence/step10-secrets-at-rest.txt) | The k3s database read directly: all 13 Secrets are stored encrypted and the ConfigMaps (the control) are readable. |
| [step10-security-facts-first-run.txt](evidence/step10-security-facts-first-run.txt) | The first run of the security facts check: 5 of 6, because the check was too strict about an image setting (mistake T). |
| [step10-security-facts.txt](evidence/step10-security-facts.txt) | The security facts of the running cluster, names and counts only: Secrets, mounts, Pod Security, accounts; 6 of 6. |
| [step10-teardown-again.txt](evidence/step10-teardown-again.txt) | The second `make teardown`: no cluster, nothing to destroy, the scan still `CLEAN`. |
| [step10-teardown.txt](evidence/step10-teardown.txt) | The first `make teardown`: the stack deleted, 19 resources destroyed, the scan `CLEAN`. |
| [step10-tls-checks.txt](evidence/step10-tls-checks.txt) | Six TLS and login checks that try the opposite of each rule, from inside the processor pod; 6 of 6. |
| [step3-cluster-up.txt](evidence/step3-cluster-up.txt) | The first `make cluster-up`: Terraform adds 19 resources, Ansible installs k3s on three machines. 1 min 48 s. |
| [step3-diagnose-run1-failed.txt](evidence/step3-diagnose-run1-failed.txt) | `make diagnose` after that failure: `mongo-0` in `CrashLoopBackOff`, and the MongoDB log line that says why (TLS was switched on without naming a certificate authority file). |
| [step3-exposure.txt](evidence/step3-exposure.txt) | `make exposure-check`: from outside, on all three nodes only 22, 6443 (server only) and 30080 answer; the other 8 ports are dropped. |
| [step3-heal.txt](evidence/step3-heal.txt) | `make heal-check` on the first version of the stack: four deliberate failures (a REST pod, the processor, MongoDB, Kafka) and four recoveries, 1 min 47 s. |
| [step3-netpol-check.txt](evidence/step3-netpol-check.txt) | The first network test on the first version of the stack: 12 NetworkPolicy objects, 7 probe pods, 42 connections, all as the policies say. |
| [step3-netpol-control.txt](evidence/step3-netpol-control.txt) | The first control experiment: with the 12 NetworkPolicy objects every blocked connection is blocked, without them every connection works, with them back it is blocked again. |
| [step3-nodes.txt](evidence/step3-nodes.txt) | `kubectl get nodes` and the system pods of the new cluster: three nodes `Ready`, k3s v1.37.1+k3s1. |
| [step3-rbac.txt](evidence/step3-rbac.txt) | `make rbac-check`: nine answers about what the viewer, the operator, the processor and the setup Job may do. |
| [step3-rebuild-cluster-up.txt](evidence/step3-rebuild-cluster-up.txt) | The cluster destroyed and built again from the same files: 19 resources added. 2 min 28 s. |
| [step3-rebuild-nodes.txt](evidence/step3-rebuild-nodes.txt) | The nodes and system pods of the rebuilt cluster. |
| [step3-rebuild-rest-check.txt](evidence/step3-rebuild-rest-check.txt) | `make rest-check` on the rebuilt cluster: `PASS`, 3 of 3 nodes. |
| [step3-rebuild-status.txt](evidence/step3-rebuild-status.txt) | `make status` of the rebuilt cluster, with the autoscalers, the policies and the Secrets made by the Job. |
| [step3-rebuild-up.txt](evidence/step3-rebuild-up.txt) | `make up` on the rebuilt cluster: the 45 objects of the first version of the stack created. 1 min 0 s. |
| [step3-rest-check.txt](evidence/step3-rest-check.txt) | `make rest-check` on the first cluster: all three nodes answer over HTTPS only and ask for a login; `PASS`, 5000 flows. |
| [step3-smoke-response.json](evidence/step3-smoke-response.json) | The REST API's answer for that tagged event. |
| [step3-smoke.txt](evidence/step3-smoke.txt) | The first `make smoke`: one tagged event sent through producer, Kafka, processor, MongoDB and REST API, in six steps. |
| [step3-status.txt](evidence/step3-status.txt) | `make status` of the first cluster: pods, volumes, ConfigMaps, the 13 Secrets (names only), policies, service accounts and roles. |
| [step3-up-run1-failed.txt](evidence/step3-up-run1-failed.txt) | The first `make up` on the real cluster. It timed out after 7 min 15 s because MongoDB did not start (mistake A in the AI log). |
| [step3-up.txt](evidence/step3-up.txt) | `make up` after the fix: 45 objects created, everything ready. 1 min 15 s. |
| [step4-grafana-after.png](evidence/step4-grafana-after.png) | The dashboard `CA3 pipeline` after the fix of the stacked graphs. |
| [step4-grafana-before-1.pdf](evidence/step4-grafana-before-1.pdf) | Full-page capture of the first version of the dashboard, page 1 (the stacked graphs; mistake N). |
| [step4-grafana-before-2.pdf](evidence/step4-grafana-before-2.pdf) | Full-page capture of the first version of the dashboard, page 2. |
| [step4-images.txt](evidence/step4-images.txt) | The six monitoring images and versions, checked to exist in their registries before the first deploy. |
| [step4-metrics-check.txt](evidence/step4-metrics-check.txt) | `make metrics-check`: every target is up, the seven numbers of the assignment exist, logs of 12 programs are in Loki, Grafana has the dashboard; 4 of 4. |
| [step4-netpol-check.txt](evidence/step4-netpol-check.txt) | The short network test (7 probes, 42 connections) with the 25 policies of the monitored stack. |
| [step4-rest-check.txt](evidence/step4-rest-check.txt) | `make rest-check` with the monitoring running: `PASS`, 3 of 3 nodes. |
| [step4-smoke-response.json](evidence/step4-smoke-response.json) | The REST API's answer for that tagged event. |
| [step4-smoke.txt](evidence/step4-smoke.txt) | `make smoke` with the monitoring running. |
| [step4-status.txt](evidence/step4-status.txt) | `make status` after the monitoring was added. |
| [step4-up.txt](evidence/step4-up.txt) | `make up` that added the monitoring to the running stack: 40 objects created, 45 left unchanged. 42 s. |
| [step5-log-sample.txt](evidence/step5-log-sample.txt) | The newest log line of each program as Loki keeps it, with its labels. |
| [step5-smoke-response.json](evidence/step5-smoke-response.json) | The REST API's answer for that event. |
| [step5-smoke.txt](evidence/step5-smoke.txt) | The smoke test whose id was searched in the logs (`smoke-1106-190230-548c`). |
| [step5-trace.txt](evidence/step5-trace.txt) | `make logs-trace`: the three log lines with that id, from the producer, the processor and the REST API. |
| [step6-scale-chart.svg](evidence/step6-scale-chart.svg) | The chart of the scaling test: producers, processors, backlog, REST answers and CPU of the nodes. |
| [step6-scale-goals.json](evidence/step6-scale-goals.json) | The seven scaling goals with this run's measurement and verdict. |
| [step6-scale-hpa-over-time.txt](evidence/step6-scale-hpa-over-time.txt) | `kubectl get hpa` every 15 seconds during the test (134 lines): the autoscalers at work. |
| [step6-scale-hpa.txt](evidence/step6-scale-hpa.txt) | `kubectl describe hpa` after the test: the settings of both autoscalers (limits, targets, scale-up and scale-down behavior), their last readings and their conditions. |
| [step6-scale-producer-log.txt](evidence/step6-scale-producer-log.txt) | The log lines of the load-generating producers during the test. |
| [step6-scale-results.md](evidence/step6-scale-results.md) | The results table of the scaling test: before and after, how the autoscaler added pods, how everything shrank back, the goals. |
| [step6-scale-run.txt](evidence/step6-scale-run.txt) | The whole console output of `make scale-test`. |
| [step6-scale-samples.csv](evidence/step6-scale-samples.csv) | The samples taken every 5 seconds during the test: pods, rates, backlog, CPU. |
| [step6-scale-summary.json](evidence/step6-scale-summary.json) | The numbers of the scaling test in one file; `make scale-report` draws the table and the chart from it. |
| [step7-alloy-events.txt](evidence/step7-alloy-events.txt) | The Kubernetes events that show why: the log collector's DaemonSet deleted a helper pod that wore its name. |
| [step7-drill-chart.svg](evidence/step7-drill-chart.svg) | The chart of the drill: time to recover per tier. |
| [step7-drill-events.txt](evidence/step7-drill-events.txt) | What Kubernetes said about each deleted pod (events). |
| [step7-drill-goals.json](evidence/step7-drill-goals.json) | The five drill goals with this run's measurement and verdict. |
| [step7-drill-observability.txt](evidence/step7-drill-observability.txt) | What Loki and Prometheus still held after each delete: the last words of the deleted pod, the search for errors, a few numbers. |
| [step7-drill-rest-calls.csv](evidence/step7-drill-rest-calls.csv) | Every call the drill made to the REST API from outside, once a second: time, answer, duration. |
| [step7-drill-results.md](evidence/step7-drill-results.md) | The results of the drill: a table per tier, the details of each, and the five goals with their verdicts. |
| [step7-drill-run.txt](evidence/step7-drill-run.txt) | The whole console output of `make drill`: one pod deleted in each of five tiers. |
| [step7-drill-summary.json](evidence/step7-drill-summary.json) | The numbers of the drill in one file; `make drill-report` draws the table, the goals and the chart from it. |
| [step7-drill-timeline.txt](evidence/step7-drill-timeline.txt) | The pods of the stack second by second after each delete (`kubectl get pods` as the drill saw it). |
| [step7-netpol-check-run1-failed.txt](evidence/step7-netpol-check-run1-failed.txt) | The first network test with 25 policies: the probe pods did not start in time (mistake P). |
| [step7-netpol-check.txt](evidence/step7-netpol-check.txt) | `make netpol-check`: 13 probe pods try 169 connections; 34 allowed, 135 blocked, every answer as the policies say. |
| [step7-netpol-control.txt](evidence/step7-netpol-control.txt) | `make netpol-control`: with the 25 policies, without them (everything open), with them back. |
| [step7-operator-steps.txt](evidence/step7-operator-steps.txt) | `make operator-steps`: the manual steps of the runbook done once on the running stack (look, logs, scale by hand, certificate end dates), with what is not done. |
| [step7-rest-evidence.txt](evidence/step7-rest-evidence.txt) | `make rest-evidence`: the request and the response of every REST endpoint (also the refused ones), the REST API's log lines for them, what Prometheus counted, and what the drill saw of failure and recovery (taken from the drill's files). |
| [step9-cluster-up-run2.txt](evidence/step9-cluster-up-run2.txt) | The second `make cluster-up`: Terraform says `No changes`, Ansible `changed=0` on all three machines. 27 s. |
| [step9-dry-run-client.txt](evidence/step9-dry-run-client.txt) | The same as a client dry run, the form the assignment's tip names. |
| [step9-dry-run-server.txt](evidence/step9-dry-run-server.txt) | `make dry-run`: the cluster is asked what `kubectl apply -k .` would change (server dry run): nothing. |
| [step9-get-all.txt](evidence/step9-get-all.txt) | `kubectl get all -A` of the Sunday cluster, as text (the picture `images/kubectl-get-all.png` shows the same command on the same cluster). |
| [step9-secrets-encryption.txt](evidence/step9-secrets-encryption.txt) | k3s's own report: Secret encryption enabled, key type AES-CBC, all hashes match. |
| [step9-tool-versions.txt](evidence/step9-tool-versions.txt) | The versions of the tools on the operator's computer (macOS, Terraform, Ansible, kubectl, kubeconform, aws-cli, Python, OpenSSL, git). |
| [step9-up-run2.txt](evidence/step9-up-run2.txt) | The second `make up`: all 85 objects `unchanged`. 9 s. |
| [step9-validate.txt](evidence/step9-validate.txt) | `make validate`: all 85 objects, and the overlays and the smoke Job, valid against the Kubernetes schemas. |

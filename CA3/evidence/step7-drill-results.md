# Resilience drill: one pod deleted in every tier

Run at 2026-11-07 00:06 UTC, 5 of 5 tiers drilled. Every number is in seconds after the pod was deleted (`kubectl delete pod`).

## What happened, tier by tier

| Tier | Pod deleted | New pod ready | Whole stack | Event readable | REST `/stats` calls failed | Data | Result |
|---|---|---|---|---|---|---|---|
| Kafka | `kafka-0` | 23 s | 25 s | 29 s | 0 of 29 | kept | **PASS** |
| Processor | `processor-6d4869f56b-8qsrh` | 14 s | 14 s | 14 s | 0 of 14 | kept | **PASS** |
| MongoDB | `mongo-0` | 13 s | 14 s | 13 s | 3 of 6 (8 s) | kept | **PASS** |
| REST API | `restapi-5f54fc9f7b-5gz55` | 7 s | 8 s | 13 s | 0 of 13 | kept | **PASS** |
| Producer | `producer-load-f877bb9f4-w2rlt` | 6 s | 16 s | 12 s | 0 of 16 | kept | **PASS** |

*New pod ready*: the replacement pod reports Ready. *Whole stack*: every part (including Prometheus, Grafana, Loki and the exporters) has its pods ready again. *Event readable*: a tagged event sent right after the delete can be read from the REST API. *REST `/stats` calls failed*: the drill asked the REST API for `/stats` about once a second from the computer that ran it; in brackets the longest stretch of failures.

### Kafka

- Pod deleted: `kafka-0` (StatefulSet) at 00:06:13 UTC (18:06:13 on the computer that ran it)
- Replacement: `kafka-0`; the old pod was gone after 2 s
- New pod ready after 23 s, whole stack after 25 s
- Test event `smoke-1107-000613-884f`: Kafka stored it 29 s after the delete; readable from the REST API after 29 s; stored 1 time
- OK: records in MongoDB: 330,353 -> 330,354
- OK: the test event is stored in MongoDB: sent -> 1 time
- OK: messages in Kafka: 330,353 -> 330,354
- OK: the disk of kafka-0: pvc-a183437c-3daa-486b-99b8-ba0951139f8f -> pvc-a183437c-3daa-486b-99b8-ba0951139f8f
- REST API, asked from outside every second: `/health` failed 0 of 29 times (longest stretch 0.0 s), `/stats` failed 0 of 29 times (longest stretch 0.0 s)

### Processor

- Pod deleted: `processor-6d4869f56b-8qsrh` (Deployment) at 00:07:37 UTC (18:07:37 on the computer that ran it)
- Replacement: `processor-6d4869f56b-rjxqg`; the old pod was gone after 3 s
- New pod ready after 14 s, whole stack after 14 s
- Test event `smoke-1107-000737-fa07`: Kafka stored it 14 s after the delete; readable from the REST API after 14 s; stored 1 time
- OK: records in MongoDB: 330,354 -> 330,355
- OK: the test event is stored in MongoDB: sent -> 1 time
- OK: messages waiting in Kafka for the processor: - -> 0
- REST API, asked from outside every second: `/health` failed 0 of 14 times (longest stretch 0.0 s), `/stats` failed 0 of 14 times (longest stretch 0.0 s)

### MongoDB

- Pod deleted: `mongo-0` (StatefulSet) at 00:08:45 UTC (18:08:45 on the computer that ran it)
- Replacement: `mongo-0`; the old pod was gone after 3 s
- New pod ready after 13 s, whole stack after 14 s
- Test event `smoke-1107-000845-ed8e`: Kafka stored it 10 s after the delete; readable from the REST API after 13 s; stored 1 time
- OK: records in MongoDB: 330,355 -> 330,356
- OK: the test event is stored in MongoDB: sent -> 1 time
- OK: the disk of mongo-0: pvc-70d04a35-7345-44a5-922f-a8160f9d1e97 -> pvc-70d04a35-7345-44a5-922f-a8160f9d1e97
- REST API, asked from outside every second: `/health` failed 0 of 6 times (longest stretch 0.0 s), `/stats` failed 3 of 6 times (longest stretch 7.5 s)

### REST API

- Pod deleted: `restapi-5f54fc9f7b-5gz55` (Deployment) at 00:09:52 UTC (18:09:52 on the computer that ran it)
- Replacement: `restapi-5f54fc9f7b-gc7qb`; the old pod was gone after 3 s
- New pod ready after 7 s, whole stack after 8 s
- Test event `smoke-1107-000952-4acc`: Kafka stored it 12 s after the delete; readable from the REST API after 13 s; stored 1 time
- OK: records in MongoDB: 330,356 -> 330,357
- OK: the test event is stored in MongoDB: sent -> 1 time
- REST API, asked from outside every second: `/health` failed 0 of 13 times (longest stretch 0.0 s), `/stats` failed 0 of 13 times (longest stretch 0.0 s)

### Producer

- Pod deleted: `producer-load-f877bb9f4-w2rlt` (Deployment) at 00:11:35 UTC (18:11:35 on the computer that ran it)
- Replacement: `producer-load-f877bb9f4-vscbn`; the old pod was gone after 3 s
- New pod ready after 6 s, whole stack after 16 s; the producer was sending again after 15 s
- Test event `smoke-1107-001135-69fd`: Kafka stored it 12 s after the delete; readable from the REST API after 12 s; stored 1 time
- OK: records in MongoDB: 349,801 -> 357,007 (the load adds records all the time, so only 'not fewer than before' is checked)
- OK: the test event is stored in MongoDB: sent -> 1 time
- REST API, asked from outside every second: `/health` failed 0 of 16 times (longest stretch 0.0 s), `/stats` failed 0 of 16 times (longest stretch 0.0 s)

## The goals (service level objectives)

The goals are the numbers in `slo.json` whose field `test` says `drill`. Each line says what was asked, what this run measured, and whether the goal was met. Change a goal with `make goals` and run `make drill-report`: the verdicts change, the measurements do not.

| Goal | Asked | This run | |
|---|---|---|---|
| A pod that is deleted is replaced by a new pod that is ready, in every tier | at most 60 seconds after the delete, for the slowest of the five tiers | kafka 23 s, processor 14 s, mongo 13 s, restapi 7 s, producer 6 s | **met** |
| After the delete, every part of the stack has its pods ready again (not only the replaced pod) | at most 90 seconds after the delete, for the slowest of the five tiers | kafka 25 s, processor 14 s, mongo 14 s, restapi 8 s, producer 16 s | **met** |
| An event that is sent while a pod is being replaced is not lost: it can be read from the REST API afterwards | at most 90 seconds after the delete, for the slowest of the five tiers | kafka 29 s, processor 14 s, mongo 13 s, restapi 13 s, producer 12 s | **met** |
| Deleting a pod deletes no data: every tier still has all the records and messages it had | at least 100 percent of the tiers | 100 percent (5 of 5 tiers kept all their data) | **met** |
| While one of the two REST API pods is replaced, the REST API keeps answering the calls for /stats | at most 5 seconds in a row without a good answer | longest stretch of failed /stats calls: 0.0 s (0 of 13 calls failed) | **met** |

## Where to look in Grafana and Loki

Open the dashboard CA3 pipeline and set the time range to the minutes below (the clock of the computer that ran the drill):

- Kafka: from 18:03 to 18:09
- Processor: from 18:04 to 18:10
- MongoDB: from 18:05 to 18:11
- REST API: from 18:06 to 18:12
- Producer: from 18:08 to 18:14

# Integrity Packet — CA0

*Last updated: September 6, 2026*

**Owner: Amshu Wagle. I own this outcome.**

## Outcome
I built a real-time network intrusion detection pipeline spanning four separate AWS EC2 VMs: a producer that replays labeled CICIDS2017 network flow data, a Kafka pub/sub layer, a processor that writes results into MongoDB, and a REST API that surfaces flagged attack traffic. The goal was to prove genuine connectivity and data flow across separate machines — not a local simulation — matching what CA0 actually asks for.

## Assumptions
- The dataset's existing labels (`BENIGN` / `Bot`) are our result. The processor relays this label rather than computing its own detection.
- A curated 5,000-row subset of the original 191,033-row Friday file was used for the demo
- Security scope: SSH key-only, four minimal firewall rules, non-root containers, services that start on boot


## Evidence
- Screenshots: EC2 console showing all four running instances; security group inbound rules; subnet detail page showing the real CIDR (`172.31.16.0/20`).
  
- Terminal output: `sudo sshd -T | grep passwordauthentication` → `passwordauthentication no`, run directly on a live VM.
  
- Terminal output: `sudo docker inspect <container> --format '{{.HostConfig.RestartPolicy.Name}}'` → `unless-stopped`, confirmed independently on the Kafka, MongoDB, and processor containers.
  
- A full stop/start cycle on all four EC2 instances (new public IPs assigned by AWS each time), followed by successfully bringing every service back up and re-running the full pipeline — proving the deployment isn't a one-time fluke tied to the original boot.
  
- Processor logs showing sustained `[ALERT] Bot flow inserted` lines with a climbing processed count, and a successful `curl .../alerts` response containing real JSON documents labeled `"Bot"`.
  

## Validation
- Diagnosed a MongoDB startup failure by reading the crash log, which pointed to a specific, checkable MongoDB Jira issue (`SERVER-121912`) — confirmed against MongoDB's own release notes before applying a fix.
- Verified cross-VM network reachability directly at the TCP level (`/dev/tcp/<ip>/<port>` probes) before assuming any failure was network-related.
- Ran the SSH and restart-policy checks.
- Diagnosed a Kafka topic that appeared to "regenerate" after deletion as a still-running consumer auto-recreating it (Kafka's `auto.create.topics.enable` default), fixed by stopping the consumer before deleting the topic.

## Ownership
I own this outcome. If any part of it turns out to be wrong, that's on me, not on the tools I used to help build it.

## Risk
- **MongoDB 7.0 instead of 8.0**: an older major version means missing whatever security patches and features 8.0+ has shipped since. Accepted because 8.0+ cannot run at all on this VM's kernel; no functional trade-off in my own code, since I only use basic, long-stable MongoDB operations (`insert_one`, `find`).
- **KRaft instead of ZooKeeper**: KRaft is Kafka's newer, less battle-tested-at-scale metadata mode. For a single-broker setup like this one the risk is low, but less community troubleshooting material exists if something unusual comes up.
- **Demo-sized dataset**: A 5,000-row curated file doesn't demonstrate the pipeline holds up at the full dataset's ~191k-row scale.
- **No REST authentication**: anyone who discovers the endpoint's port could query flagged attack data. Acceptable for a graded class demo; would need addressing before any real use.
- **No replication configured**: single Kafka broker, single Mongo instance — if either VM goes down, that piece of the pipeline is unavailable until the VM itself comes back. This is separate from the "starts on boot" requirement, which *is* addressed: Kafka, MongoDB, and the processor all run with `restart: unless-stopped`, confirmed via `docker inspect`, so each one restarts automatically whenever its VM comes back up — I don't need to be the one bringing them back manually.

## AI-Assisted Work
I used Claude for this project, mainly for the code for the producer and the processor, rather than for the pipeline's actual design decisions, which I made myself.

- **Verified and used as-is**: Kafka KRaft configuration syntax, producer code, processor code.
- **Verified independently before trusting**: when a MongoDB downgrade was suggested to fix a startup crash, the crash log itself pointed to a specific, checkable bug report, which was confirmed before proceeding.
- **Decisions I made, not the AI**: the project topic (intrusion detection on CICIDS2017), the choice to relay the dataset's existing label rather than build independent detection logic, and the choice to build a denser demo dataset from the original file.
- **Debugging approach**: nearly all the debugging — reading error messages, checking container status, testing network reachability was done by running commands myself and checking things directly; I cross-validated with Claude when I got confused while reading the logs. For example,  I used Claude when the Kafka config failed, and the MongoDB config failed by showing the logs, and then we fixed it by not using the Bitnami example in the CS 5287 doc and downgrading the MongoDB

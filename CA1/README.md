# CA1 — Infrastructure as Code: Real-Time Network Intrusion Detection Pipeline

*Last updated: October 8, 2026*

## Overview

This project rebuilds the CA0 Producer → Kafka → Processor → MongoDB → REST API pipeline using Terraform for AWS infrastructure and Ansible for software configuration. Terraform now owns the disposable network as well as the four EC2 instances: a dedicated VPC, public subnet, internet gateway, route table and association, security group, SSH key pair, and generated Ansible inventory. Ansible installs Docker and deploys Kafka, MongoDB, the processor, producer, and REST API.

The post-grading work focused on the exact problems discussed in the CA1 debrief: make the network disposable, centralize shared settings, make configuration idempotent, trace one unique event through every hop, and prove that destroy really leaves nothing behind. The final fresh rebuild proved that the system can be recreated from nothing, configured twice with `changed=0` on the second run, validated end to end, destroyed, and scanned clean.

Once the one-time setup is complete, the lifecycle is:

| Stage | Command | Proof |
|---|---|---|
| Provision | `terraform apply` | Fresh rebuild created 14 resources |
| Configure | `ansible-playbook site.yml` | First run `failed=0`; second run `changed=0` on every host |
| Validate | `python3 scripts/smoke_test.py` | One unique event crosses Producer → Kafka → Processor → MongoDB → REST |
| Destroy | `terraform destroy` | `Destroy complete! Resources: 14 destroyed.` |
| Verify cleanup | `python3 scripts/aws_scan.py` | `RESULT: CLEAN` |

> **Important:** the operator public IP is still a manual Terraform input. Before a deploy, make sure `terraform/terraform.tfvars` contains your **current** public IPv4 address with `/32`. During the final rebuild, a stale value caused all four SSH connections to time out. Updating only `my_ip_cidr` and re-running `terraform apply` fixed the security group in place (`0 added, 1 changed, 0 destroyed`).

## Demo Video

**[VIDEO LINK — https://youtu.be/_km0kpBqTcI]**

The video is from the original CA1 submission. It shows a live Terraform/Ansible deployment, the producer replaying 5,000 rows, processor activity, and the REST endpoints. The post-grading improvements described below were completed after that recording, so their proof comes from the later terminal runs documented in this README and the Integrity Packet.

## What Changed After Grading

The class debrief ended with a practical checklist. This is where the project stands now:

| Debrief item | Current status | What changed |
|---|---|---|
| Make the network disposable | **Done** | Terraform creates and destroys a dedicated VPC, subnet, internet gateway, route table, association, security group, and compute resources. |
| Centralize parameters | **Done** | Topic, ports, database/collection, dataset, AWS region, project tag, plus the Kafka and MongoDB image tags are in one `config.yml` shared across Terraform, Ansible, and the apps. Old CA0 fallback IPs were removed. |
| Automate service installation | **Done** | Ansible roles install/configure the whole reference stack. |
| Use a vault / secret manager | **Done** | MongoDB credentials use Ansible Vault; app credentials are delivered through protected environment/config files rather than Docker command-line arguments. |
| Run the playbook twice | **Done** | A fresh rebuild was configured twice; the second complete run reported `changed=0` on all four hosts. |
| Trace one unique event end to end | **Done** | `scripts/smoke_test.py` creates one unique trace ID and matches the exact Kafka partition/offset through processor, MongoDB, and REST. |
| Verify destroy with a resource scan | **Done** | Every Terraform resource is tagged `Project=CA1`; `scripts/aws_scan.py` checks project resources and billable leftovers after destroy. |

A separate issue was also exposed by the final rebuild: `my_ip_cidr` can go stale when the operator changes networks. That behavior is documented under **Operator IP / SSH Troubleshooting** and **Known Limitations** rather than hidden.

## Architecture Diagram

```mermaid
flowchart TB
    User["Operator / Grader<br/>current public IPv4 /32 allow-listed"]

    subgraph AWS["AWS · us-east-2 · environment created and destroyed by Terraform"]
        direction TB

        subgraph VPC["CA1 VPC 10.0.0.0/16<br/>public subnet 10.0.1.0/24<br/>internet gateway + route table"]
            direction LR

            subgraph PVM["producer-vm"]
                PDock["Producer container<br/>python:3.11-slim<br/>run on demand"]
            end

            subgraph BVM["broker-vm"]
                KafkaNode["confluentinc/cp-kafka:7.7.1<br/>KRaft mode"]
            end

            subgraph ZVM["processor-vm"]
                ProcDock["Processor container<br/>restart: unless-stopped"]
            end

            subgraph DVM["database-vm"]
                MongoNode["mongo:7.0<br/>db=ca1 · collection=flows"]
                FlaskNode["Flask REST API · :8080<br/>/health · /alerts · /events/<trace_id>"]
                FlaskNode --> MongoNode
            end

            PDock -->|"topic network-flows · TCP 9092"| KafkaNode
            KafkaNode -->|"consume"| ProcDock
            ProcDock -->|"authenticated insert · TCP 27017"| MongoNode
        end
    end

    User -->|"SSH :22"| PVM
    User -->|"SSH :22"| BVM
    User -->|"SSH :22"| ZVM
    User -->|"SSH :22 + REST :8080"| DVM
```

## Automation Toolchain

```mermaid
flowchart LR
    Cfg["config.yml<br/>topic · ports · DB/collection · dataset<br/>AWS region · project tag · Kafka/Mongo images"]
    TF["Terraform"]
    AWSA["AWS<br/>VPC + subnet + routes + SG + key + 4 VMs"]
    Inv["ansible/inventory.ini<br/>generated by Terraform"]
    Ans["Ansible"]
    VMs["Docker · Kafka · MongoDB<br/>processor · producer · REST API"]
    Vault["Ansible Vault<br/>Mongo credentials"]
    Smoke["scripts/smoke_test.py"]
    Scan["scripts/aws_scan.py"]

    Cfg --> TF
    Cfg --> Ans
    TF --> AWSA
    TF --> Inv
    Inv --> Ans
    Vault --> Ans
    Ans --> VMs
    VMs -. validated by .-> Smoke
    AWSA -. cleanup checked by .-> Scan
```

Terraform writes the current VM public/private IPs and the absolute SSH-key path into `ansible/inventory.ini`, so Ansible does not depend on old machine addresses. Shared application settings come from `config.yml`. The one environment-specific value that still has to be kept current by the operator is `my_ip_cidr` in `terraform.tfvars`.

## Software Stack

| Component | Technology / version |
|---|---|
| Provisioning | Terraform 1.16.3 |
| Configuration | Ansible core 2.21.4 |
| Kafka | `confluentinc/cp-kafka:7.7.1` |
| MongoDB | `mongo:7.0` |
| Producer | Python + `kafka-python`, Dockerized, `python:3.11-slim` |
| Processor | Python + `kafka-python` + `pymongo`, Dockerized, `python:3.11-slim` |
| REST API | Flask + pymongo, systemd-managed |
| Container runtime | Docker Engine + Compose plugin |
| Cloud | AWS EC2, `us-east-2` |
| Secrets | Ansible Vault (AES256) |

The Kafka and MongoDB image tags are now centralized in `config.yml`. Their Ansible Compose templates read `kafka_image` and `mongo_image` from that shared file, so changing either service version no longer requires editing the deployment template itself. The producer and processor still use their existing pinned `python:3.11-slim` base image in their Dockerfiles; that was not part of this Kafka/Mongo image-tag fix.

## Configuration and Parameterization

`CA1/config.yml` is the shared non-secret configuration source for the settings that cross tool boundaries:

```yaml
kafka_topic: network-flows
kafka_port: 9092
mongo_port: 27017
mongo_db: ca1
mongo_collection: flows
rest_port: 8080
dataset_file: Friday-Morning-5000-mixed-bot.csv
aws_region: us-east-2
project_tag: CA1
kafka_image: confluentinc/cp-kafka:7.7.1
mongo_image: mongo:7.0
```

Terraform reads it for region, firewall ports, and project tagging. Ansible reads the same values and injects them into the applications through templates/environment files. The producer, processor, and REST API no longer contain old CA0 private-IP fallbacks; required settings must be present or the app exits with an error instead of silently talking to an old address.

Terraform-specific deployment inputs remain in `variables.tf` / `terraform.tfvars`, including:

- `instance_type` — defaults to `t3.medium`
- `vpc_cidr` — defaults to `10.0.0.0/16`
- `subnet_cidr` — defaults to `10.0.1.0/24`
- `my_ip_cidr` — the operator's **current** public IPv4 address with `/32`

## Network

Terraform now owns the disposable network rather than reusing the account's default VPC:

| Resource | Current design |
|---|---|
| VPC | `10.0.0.0/16` |
| Public subnet | `10.0.1.0/24` |
| Internet gateway | Created and attached by Terraform |
| Route table | Default route `0.0.0.0/0` to the internet gateway |
| Route association | Terraform-managed |
| Security group | One shared CA1 SG |
| Compute | Four EC2 VMs |

The security group exposes only what the pipeline needs:

| Port | Source | Purpose |
|---|---|---|
| 22/TCP | `my_ip_cidr` only | SSH |
| 8080/TCP | `my_ip_cidr` only | REST API |
| 9092/TCP | the CA1 security group itself | Kafka between VMs |
| 27017/TCP | the CA1 security group itself | MongoDB between VMs |

## Prerequisites

Everything below was built on macOS.

### Terraform

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform version
```

![Installing Terraform via Homebrew](images/download-terraform-with-homebrew.png)
*Original setup evidence: installing Terraform through HashiCorp's Homebrew tap.*

![Confirming the installed Terraform version](images/check-terraform-version.png)
*Original setup evidence: Terraform version check.*

### Ansible

```bash
brew install ansible
ansible --version
```

### AWS CLI

```bash
brew install awscli
aws --version
```

![Installing the AWS CLI via Homebrew](images/check-and-install-amazon-cli.png)
*Original setup evidence: AWS CLI installation/version check.*

Use a dedicated IAM user for this class project rather than root. Configure its access key with:

```bash
aws configure
aws sts get-caller-identity
```

![Configuring the static access key for the ca1-terraform IAM user](images/add-working-credentials.png)
*Original setup evidence: AWS credentials configured locally.*

![aws sts get-caller-identity confirming the scoped IAM user, not root](images/iam-user-confirmed.png)
*Original setup evidence: the scoped IAM identity.*

### Current operator IP

Before every deploy, especially after moving between Wi-Fi/campus/home networks, check your current public address:

```bash
curl -s https://checkip.amazonaws.com
```

Then make sure `CA1/terraform/terraform.tfvars` contains exactly that address with `/32`, for example:

```hcl
my_ip_cidr = "203.0.113.7/32"
```

`terraform.tfvars` is environment-specific and is not committed.

## Secret Management

MongoDB credentials use Ansible Vault. A fresh clone does **not** contain your real `vault.yml`; it contains only `vault.yml.example`.

```bash
cd CA1/ansible
cp group_vars/all/vault.yml.example group_vars/all/vault.yml
nano group_vars/all/vault.yml
echo "choose-a-vault-password" > .vault_pass
chmod 600 .vault_pass
ansible-vault encrypt group_vars/all/vault.yml
```

`vault.yml`, `.vault_pass`, Terraform state, `*.pem`, `*.tfvars`, and generated inventory are local/sensitive artifacts and should not be committed.

![Vault-encrypted MongoDB credentials, verified with cat](images/no-secrets-in-plain-text.png)
*Original evidence: the local vault file contains AES256 ciphertext rather than plaintext.*

The processor and REST API receive credentials through protected environment files rather than placing passwords directly on `docker run` or systemd command lines. The MongoDB Compose template still contains the credential after rendering on the VM, so its file permissions matter. See **Known Limitations** for the remaining `no_log`/state considerations.

## How to Deploy

### 1. Check the operator IP

```bash
cd CA1/terraform
curl -s https://checkip.amazonaws.com
cat terraform.tfvars
```

If the address is different, update only `my_ip_cidr` to the current address with `/32`.

### 2. Provision AWS

```bash
terraform init        # first time only
terraform validate
terraform apply
```

A fresh rebuild should create 14 managed resources. Terraform also writes the SSH key and generated Ansible inventory locally.

### 3. Configure the four VMs

```bash
cd ../ansible
ansible-playbook site.yml
```

On a fresh environment many tasks should report `changed`. On a healthy already-configured environment, running the same playbook again should converge to `changed=0` on every host.

### Original Build Screenshots

The following screenshots are from the original CA1 submission. Some infrastructure details shown in them (especially use of the default VPC) were replaced in the post-grading version described above, but the image paths are kept here as original build evidence.

![Terraform folder/version validation](images/make-terraform-folder-and-create-version-and-validate.png)
*Original build: Terraform project setup.*

![Original variables.tf validation](images/create-variables-and-validate.png)
*Original build: Terraform variables.*

![Original key-pair configuration](images/create-key_pair-and-validate.png)
*Original build: Terraform-managed SSH key.*

![Original network configuration](images/create-network-and-validate.png)
*Original build screenshot. This network implementation was later replaced with a dedicated Terraform-owned VPC/subnet/IGW/route-table design.*

![Original security group configuration](images/create-security-and-validate.png)
*Original build: security-group definition.*

![Original compute configuration](images/create-compute-and-validate.png)
*Original build: four EC2 instances defined in Terraform.*

![terraform init succeeding](images/initialize-terraform.png)
*Original build: `terraform init`.*

![terraform plan showing resources](images/terraform-plan.png)
*Original build: Terraform plan.*

![terraform apply completing with outputs shown](images/terraform-applied.png)
*Original build: successful apply.*

![A later apply fixing the SSH key path to an absolute path](images/outputs-fix.png)
*Original debugging evidence: relative SSH-key path replaced with an absolute path.*

![Proving SSH access into a freshly created VM](images/ssh-into-broker.png)
*Original build: SSH connectivity.*

![AWS console showing all four instances running](images/aws-console-4vms.png)
*Original build: four CA1 instances running.*

![Terraform-generated Ansible inventory](images/ansible-inventory-generated.png)
*Original build: inventory generated from Terraform outputs.*

![All four Ansible hosts responding](images/pinging-ansible.png)
*Original build: `ansible all -m ping`.*

## What Ansible Configures

### Docker

![Ansible installing Docker across all four hosts](images/installing-docker-using-ansible.png)
*Original evidence: Docker role execution.*

![Docker version confirmed on all four hosts](images/docker-versions.png)
*Original evidence: Docker installed on each VM.*

### Kafka

![Ansible configuring and starting Kafka](images/ansible-kafka-running.png)
*Original evidence: Kafka role.*

![Kafka container confirmed running](images/kafka-container-status.png)
*Original evidence: Kafka container status.*

### MongoDB

![Ansible configuring and starting MongoDB](images/ansible-mongo-running.png)
*Original evidence: MongoDB role.*

### Processor

![Processor role playbook run](images/processor-play-recap.png)
*Original evidence: processor role.*

![Processor logs confirming the topic](images/check-processor-logs.png)
*Original evidence: processor listening/processing logs.*

### Producer

![Producer role playbook run](images/producer-play-recap.png)
*Original evidence: producer role.*

![Producer image confirmed built](images/producer-image-exsist-and-built.png)
*Original evidence: producer image exists.*

### REST API

![REST API role playbook run](images/rest-api-play-recap.png)
*Original evidence: REST API role.*

![REST API health check returning 200](images/rest-api-health-200.png)
*Original evidence: `/health` returned 200.*

## Idempotency: Run It Twice

The original submission proved reproducibility through destroy/rebuild, but the professor correctly separated that from **idempotency**. The post-grading Ansible roles were changed so already-correct containers/services are left alone.

On a completely fresh rebuild, the first playbook run made the expected changes. The second full run, with nothing changed between runs, reported:

```text
database host : changed=0  unreachable=0  failed=0
producer host : changed=0  unreachable=0  failed=0
processor host: changed=0  unreachable=0  failed=0
broker host   : changed=0  unreachable=0  failed=0
```

That is the idempotency proof: the second run converged without unnecessary container recreation or service restarts.

## End-to-End Validation

Run from the CA1 root:

```bash
python3 scripts/smoke_test.py
```

The new smoke test creates one unique trace ID and follows **that exact event** through every stage instead of relying on a generic row count.

Fresh-rebuild proof:

```text
CA1 end-to-end smoke test
  path     : producer VM -> Kafka -> processor -> MongoDB -> REST API

  [PASS] REST API answers                                     GET /health -> 200
  [PASS] Event is not in the database yet                     GET /events/<id> -> 404 (found: false)
  [PASS] Producer sent it and Kafka stored it                 topic network-flows, partition 0, offset 0
  [PASS] Processor read the same Kafka offset and stored it   [TRACE] partition 0, offset 0
  [PASS] MongoDB holds exactly one copy                       ca1.flows: 1 document with this trace id
  [PASS] REST API returns the event                           GET /events/<id> -> 200, Label SMOKE-TEST
  cleanup  : test event removed; REST API now answers 404

RESULT: PASS - one event crossed producer -> Kafka -> processor -> MongoDB -> REST API
```

The test was also made to fail on purpose by stopping the processor. Kafka still accepted the event, but the test failed at the processor step with a non-zero exit code. Running the Ansible playbook repaired only the stopped processor, and the next smoke test passed. That proves the validator can detect a broken hop rather than always printing PASS.

The original submission's 5,000-row smoke-test screenshots are still useful as supporting evidence:

![Producer sending all 5000 rows](images/smoke-test-producer.png)
*Original validation: 5,000 rows sent.*

![Processor logs showing activity](images/smoke-test-processor.png)
*Original validation: processor consuming/inserting.*

![REST API returning MongoDB-backed data](images/smoke-test-rest-api.png)
*Original validation: REST response backed by MongoDB.*

## How to Destroy

```bash
cd CA1/terraform
terraform destroy
```

The final fresh rebuild ended with:

```text
Destroy complete! Resources: 14 destroyed.
```

Then verify AWS from the CA1 root:

```bash
cd ..
python3 scripts/aws_scan.py
```

Expected result:

```text
RESULT: CLEAN - nothing tagged Project=CA1 is left, and no billable resources are running in us-east-2.
```

The scanner checks the project-tagged EC2/VPC/subnet/security-group/internet-gateway/route-table/key-pair resources and also checks common billable leftovers such as EC2 instances, EBS volumes, Elastic IPs, NAT gateways, and load balancers.

The IAM user used for this project cannot call `tag:GetResources`. That optional cross-service tag check is therefore shown as `[skip]` and repeated in the final message. The required EC2/network/billable-resource checks still run; the scanner does not hide the missing permission.

During the earlier cleanup proof, `terraform state list` was empty after destroy, the generated key/inventory files were gone, and the all-regions safety scan was clean. The final rebuild was then destroyed again and the normal cleanup scan returned CLEAN.

![terraform destroy completing against the fully configured pipeline](images/final-teardown.png)
*Original submission teardown evidence.*

![AWS console confirming zero running CA1 instances after destroy](images/final-teardown-aws-console.png)
*Original submission: terminated CA1 instances.*

![Terraform destroyed output](images/terraform-destroyed.png)
*Original destroy output.*

![AWS console after destroy](images/terrafrom-aws-console-ec2-destroyed.png)
*Original AWS-console teardown check.*

![AWS console with prior CA0 VMs visible](images/aws-console-4vms-with-prev-ca0-vms.png)
*Historical evidence that old CA0 resources existed separately. The post-grading cleanup scanner later caught old untagged billable resources as part of its positive-control testing.*

## Operator IP / SSH Troubleshooting

### Symptom

All four Ansible hosts time out on SSH immediately after a successful Terraform rebuild.

### Cause seen during the final rebuild

`terraform.tfvars` still contained the public IP from the previous network. The CA1 security group correctly allowed only that old `/32`, so the new network could not SSH into any VM.

### Check

```bash
curl -s https://checkip.amazonaws.com
cat CA1/terraform/terraform.tfvars
```

The IPs must match, with `/32` added in `terraform.tfvars`.

### Fix

Update `my_ip_cidr`, then:

```bash
cd CA1/terraform
terraform apply
```

In the actual rebuild, this changed only the security group:

```text
Plan: 0 to add, 1 to change, 0 to destroy.
Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
```

After that, Ansible connected normally.

This is a **known manual prerequisite**, not an automatic-IP feature. Do not work around it by opening SSH to `0.0.0.0/0`.

## Security

The project keeps the original least-exposure design:

- SSH and the REST API are exposed only to the operator's `/32`.
- Kafka and MongoDB are only reachable from the CA1 security group / VMs.
- MongoDB authentication is enabled using Ansible Vault credentials.
- The generated private key is written locally with restrictive permissions and is gitignored.
- Application credentials are not placed directly into Docker command lines.
- The REST API is managed by systemd and restarts on failure.

![Security group details](images/security-group-details.png)
*Original evidence: security-group details.*

![Security group inbound rules](images/security-group-inbound-rules.png)
*Original evidence: inbound rule table.*

![Security group outbound rules](images/security-group-outbound-rules.png)
*Original evidence: outbound rule.*

## Outputs Summary

The exact public/private IPs change on every fresh deployment, so the README does not treat any example IP as permanent. Use:

```bash
cd CA1/terraform
terraform output public_ips
terraform output private_ips
```

Stable pipeline values:

- Kafka topic: `network-flows`
- Kafka port: `9092`
- MongoDB: database `ca1`, collection `flows`, port `27017`
- REST API port: `8080`
- REST endpoints: `/health`, `/alerts`, `/events/<trace_id>`
- Project tag: `Project=CA1`

Final post-grading validation results:

- fresh Terraform environment created successfully;
- first Ansible run succeeded;
- second full Ansible run reported `changed=0` everywhere;
- unique-event smoke test returned PASS;
- Terraform destroyed 14 resources;
- cleanup scanner returned CLEAN.

## Deviations From CA0

- **Dedicated disposable network.** CA0 used existing/default networking; current CA1 creates and destroys its own VPC, subnet, internet gateway, route table, and association.
- **Configuration is shared across tools.** Topic, ports, DB/collection, dataset, region, project tag, and the Kafka/MongoDB image tags come from one `config.yml` instead of being repeated independently.
- **Stale application fallback IPs were removed.** Missing required settings fail loudly instead of silently using CA0 addresses.
- **MongoDB authentication is enabled.** Credentials use Ansible Vault.
- **REST API runs under systemd.** It is deployed/configured rather than manually started.
- **Ansible is idempotent.** A second complete run reports `changed=0` when nothing changed.
- **Validation follows one exact event.** The smoke test checks the Kafka partition/offset, processor trace, database copy, and REST result for the same trace ID.
- **Cleanup is independently scanned.** Terraform resources carry `Project=CA1`, and `aws_scan.py` checks AWS after destroy.

## Known Limitations

These are intentionally documented instead of being hidden:

1. **Operator IP is manual.** `my_ip_cidr` must be updated when the computer's public IP changes. A stale value blocks SSH/REST until Terraform updates the security group.
2. **Terraform state is sensitive.** Terraform generates the SSH private key, so the private-key material exists inside local `terraform.tfstate` while the state exists. State and `*.pem` files are gitignored and must be protected like credentials.
3. **`tag:GetResources` is not allowed for the IAM user.** The cleanup scanner reports that optional cross-service check as `[skip]`; required resource/billing checks still run.
4. **MongoDB uses its administrative account for the application connections.** A production system would create a least-privilege application user.
5. **One rendered credential file needs careful handling with Ansible `--diff`.** The MongoDB Compose task renders the real credential on the VM; its permissions protect the file, but the task is not claimed to be safe for verbose secret-revealing diff output.
6. **REST API authentication is out of scope.** Access is restricted at the security-group level, but the HTTP endpoint itself has no application-layer authentication.
7. **The 5,000-row demo is not a full-scale benchmark.** It validates correctness, not the entire CICIDS2017-scale workload.

## Testing / Verification

- [x] Terraform configuration validated during development
- [x] Fresh apply from no CA1 infrastructure
- [x] Terraform-owned VPC/subnet/IGW/route-table lifecycle
- [x] Ansible first run `failed=0, unreachable=0`
- [x] Ansible second full run `changed=0` on every host
- [x] Shared-config change test propagated to the expected consumers and reverted cleanly
- [x] Applications fail when required settings are missing instead of using stale fallbacks
- [x] Unique-event end-to-end smoke test PASS
- [x] Intentional smoke-test failure with processor stopped
- [x] Ansible repaired the stopped processor and smoke test passed again
- [x] Positive-control cleanup scan found deployed resources
- [x] `terraform destroy` removed all 14 resources
- [x] Post-destroy cleanup scan returned CLEAN
- [x] Final rebuild-from-nothing repeated apply → Ansible twice → smoke test → destroy → scan
- [x] Git working tree was clean after the final verification

## Integrity Packet

See [`integritypacket.md`](./integritypacket.md) for the claim/evidence/assumption/validation/AI-review record, including the idempotency fixes, unique-event proof, cleanup scan, stale-operator-IP failure, and remaining limitations.

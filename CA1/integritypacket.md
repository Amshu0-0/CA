# Integrity Packet — CA1

*Last updated: October 8, 2026*

**Owner: Amshu Wagle. I own this outcome.**

## Outcome

I automated CA0's four-VM Producer → Kafka → Processor → MongoDB → REST API deployment using Terraform for infrastructure and Ansible for configuration. After CA1 was graded, I went back through the professor's debrief checklist and improved the parts that were still weak: the network is now disposable, shared application settings are centralized, the Kafka and MongoDB image tags are centralized with those settings, the Ansible roles converge without unnecessary changes, the smoke test follows one exact event through every stage, and an AWS scan checks that destroy really left nothing behind.

I also ran the final version from nothing: Terraform created a new environment, Ansible configured it, a second Ansible run reported `changed=0`, the unique-event smoke test passed, Terraform destroyed 14 resources, and the cleanup scanner returned CLEAN. That rebuild found one real operational problem that is still intentionally documented rather than hidden: my operator public IP had changed while `terraform.tfvars` still contained the old `/32`, so SSH was blocked until I updated `my_ip_cidr` and re-ran Terraform.

The professor described the Integrity Packet as a verification packet, not an AI confession form. Each claim below therefore follows the same pattern: **Claim → Evidence → Assumption → Validation → AI Review**.

## Assumptions That Apply to the Whole Project

- The person deploying has an AWS account and uses a scoped IAM user rather than root.
- AWS credentials are configured locally and are not stored in this repository.
- `terraform.tfstate` is the source of truth for Terraform-managed resources and is kept local/protected.
- The operator checks that `terraform/terraform.tfvars` contains the computer's current public IPv4 address as `/32` before deploying.
- `ansible/group_vars/all/vault.yml` and `.vault_pass` exist locally before running the playbook. The repository contains only the example file.
- Nobody manually changes CA1 resources in the AWS console between Terraform operations.

---

## Claim 1 — Terraform owns a disposable environment and destroy leaves no CA1 resources behind

**Claim.** CA1 no longer depends on the AWS account's default VPC/subnet. Terraform creates the network and compute resources needed for the pipeline and can remove them again.

**Evidence.** The post-grading Terraform code creates a dedicated VPC, public subnet, internet gateway, route table, route association, security group, key pair, four EC2 instances, local private-key file, and generated Ansible inventory. A fresh rebuild ended with 14 managed resources created. The final destroy ended:

```text
Destroy complete! Resources: 14 destroyed.
```

Immediately afterward, `python3 scripts/aws_scan.py` reported:

```text
RESULT: CLEAN - nothing tagged Project=CA1 is left, and no billable resources are running in us-east-2.
```

**Assumption.** The IAM user can perform the required EC2/network describe calls. It cannot call `tag:GetResources`, so the scanner explicitly marks that optional cross-service tag check as `[skip]` instead of pretending it ran.

**Validation.** I used a positive control before destroy: while the project was deployed, the scanner found the CA1 resources and returned a leftovers result. After destroy it returned CLEAN. During the first cleanup cycle, Terraform state was empty afterward, the generated key and inventory files were gone, and the all-regions safety scan was clean. I later repeated the full apply/configure/validate/destroy/scan sequence from a completely fresh CA1 environment and again finished with CLEAN.

**AI Review.** Claude helped design the scanner. Real AWS testing found problems in the first version: an optional permission denial was treated as fatal, and an AWS error string exposed the full account number. The scanner was changed so optional permission failures are shown as skipped, required failures cannot look clean, and account identifiers are masked. The real before/after scans proved the behavior.

---

## Claim 2 — Running Ansible again on an already-correct system makes no unnecessary changes

**Claim.** The configuration layer is idempotent. A second complete `ansible-playbook site.yml` run against an already-correct deployment reports `changed=0` on every host.

**Evidence.** On the final fresh rebuild, the first run changed the new machines as expected. Without changing any input, the second run ended with all four hosts at `changed=0`, `unreachable=0`, and `failed=0`.

**Assumption.** The same configuration is used for both runs and nobody manually edits the VMs between them.

**Validation.** I did not count destroy/rebuild as idempotency. I specifically ran the complete playbook twice against the same live environment. Earlier role implementations still recreated/restarted components on every run; the second-run evidence exposed that problem. After the fixes, the second run converged cleanly on the existing stack and again on the stack rebuilt from nothing.

**AI Review.** The first Ansible approach used imperative Docker commands such as removing/recreating a container and restarting services, which guarantees a change even when the desired state is already correct. Claude helped replace those patterns with desired-state Docker modules, handlers, and guarded builds. A later attempt still rebuilt images unnecessarily; the second-run output caught that too. The final acceptance test was not the explanation — it was the `changed=0` recap.

---

## Claim 3 — The validation follows one exact event through Producer → Kafka → Processor → MongoDB → REST

**Claim.** The smoke test proves the same uniquely tagged event reaches every pipeline stage; it does not rely on a generic statement like "the database has records."

**Evidence.** `scripts/smoke_test.py` creates a unique trace ID, verifies it is absent first, sends it through the producer, records the Kafka topic/partition/offset, checks the processor log for that same partition/offset, confirms exactly one MongoDB document with the trace ID, then retrieves that event through `GET /events/<trace_id>`.

Fresh-rebuild proof included:

```text
[PASS] REST API answers                                     GET /health -> 200
[PASS] Event is not in the database yet                     GET /events/<id> -> 404
[PASS] Producer sent it and Kafka stored it                 topic network-flows, partition 0, offset 0
[PASS] Processor read the same Kafka offset and stored it   [TRACE] partition 0, offset 0
[PASS] MongoDB holds exactly one copy                       ca1.flows: 1 document with this trace id
[PASS] REST API returns the event                           GET /events/<id> -> 200
RESULT: PASS
```

**Assumption.** Kafka, the processor, MongoDB, and REST API are reachable and the generated trace ID is new.

**Validation.** I deliberately stopped the processor and re-ran the smoke test with a short timeout. Kafka accepted the event, but the validator failed specifically at the processor hop and exited non-zero. Running Ansible again repaired only the processor, and the next smoke test passed. This proves the test can detect a broken stage rather than always succeeding.

**AI Review.** Claude helped write the trace-ID additions and validation script. I rejected the weaker row-count approach because it could pass on old data. Matching Kafka's exact offset to the processor trace is the evidence that the same event crossed the message-bus boundary.

---

## Claim 4 — Shared runtime settings and service image tags come from one config file, and old fallback IPs are gone

**Claim.** The settings that cross Terraform, Ansible, and the applications — topic, ports, database/collection, dataset, AWS region, project tag, plus the Kafka and MongoDB image tags — are written in `config.yml` and consumed by the relevant layers. The applications do not silently fall back to private IPs from CA0.

**Evidence.** The current shared settings include:

```text
kafka_topic
kafka_port
mongo_port
mongo_db
mongo_collection
rest_port
dataset_file
aws_region
project_tag
kafka_image
mongo_image
```

The two service image values are now:

```text
kafka_image: confluentinc/cp-kafka:7.7.1
mongo_image: mongo:7.0
```

and the Ansible Compose templates reference `{{ kafka_image }}` and `{{ mongo_image }}` instead of repeating those tags. The producer, processor, and REST API also require their runtime settings instead of using hardcoded CA0 addresses/defaults.

**Assumption.** `config.yml` contains non-secret configuration only. Credentials remain in Ansible Vault. The producer and processor Dockerfiles keep their existing `python:3.11-slim` base image; this final change addressed the Kafka and MongoDB image-tag gap specifically identified in the professor feedback.

**Validation.** Earlier missing-setting tests caused the applications to stop and name the missing variable instead of continuing with an old address. A deliberate shared-configuration change also propagated through the expected consumers and reverted cleanly to `changed=0`. For the final image-tag cleanup, I kept the exact versions already used by the working system and moved only their location. The repository check showed:

```text
config.yml: kafka_image: confluentinc/cp-kafka:7.7.1
config.yml: mongo_image: mongo:7.0
ansible/roles/kafka/templates/docker-compose.yml.j2: image: "{{ kafka_image }}"
ansible/roles/mongo/templates/docker-compose.yml.j2: image: "{{ mongo_image }}"
OK: Kafka and Mongo image tags now live only in config.yml
```

Because the AWS stack had already been destroyed and the image values themselves did not change, I did not spend another AWS deployment cycle solely to prove this small refactor.

**AI Review.** The original applications contained old CA0 fallback IPs, which were removed instead of being left as "safe defaults." The professor also specifically identified Kafka and MongoDB image tags as an incomplete parameterization gap. The final fix was intentionally narrow: add `kafka_image` and `mongo_image` to the existing shared config and make the two Compose templates consume them. I did not add unrelated automatic-IP or Python-image changes.

---

## Claim 5 — Secrets are kept out of Git and application passwords are not put on Docker command lines

**Claim.** MongoDB credentials are stored in a local Ansible Vault and are not committed to the repository. The processor and REST API receive credentials through protected files/configuration rather than plaintext Docker command-line arguments.

**Evidence.** The repository contains `vault.yml.example`, while the real `vault.yml` and `.vault_pass` are gitignored. `*.pem`, `*.tfstate*`, `*.tfvars`, and generated inventory are also local/ignored artifacts. The original evidence screenshot shows AES256 ciphertext in the local vault file.

**Assumption.** The operator protects `.vault_pass`, the local vault file, the generated SSH key, and Terraform state on disk.

**Validation.** The deployed processor, MongoDB, and REST API successfully authenticated using the vaulted values. A missing/wrong vault password causes Ansible to fail rather than silently disabling authentication.

**AI Review.** An earlier README draft incorrectly said the encrypted `vault.yml` was committed. Cross-checking the repository policy showed that was false: the real file is ignored. The documentation was corrected so a fresh clone explicitly creates its own local vault.

**Remaining limitation.** Because Terraform generates the SSH key itself, the private-key material is stored inside local Terraform state while that state exists. The state is gitignored, but it is sensitive and should be protected accordingly. Also, the MongoDB Compose render task should not be assumed safe for secret-revealing `--diff` output just because the resulting file has restrictive permissions.

---

## Debugging Case — The stale operator IP broke a clean rebuild

This is not presented as a fixed automatic feature. It is a real failure that exposed a manual prerequisite.

**What happened.** Terraform successfully rebuilt the environment, but Ansible timed out on SSH to all four new VMs.

**Evidence.** The security group allowed the `/32` stored in `terraform.tfvars`, but `curl https://checkip.amazonaws.com` showed that my current public IP had changed since the previous run.

**Correction.** I updated only `my_ip_cidr` to the current public IP with `/32` and ran `terraform apply` again. Terraform changed only the security-group rule:

```text
Plan: 0 to add, 1 to change, 0 to destroy.
Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
```

Ansible then connected and the full rebuild continued successfully.

**What this proves.** The security rule was working as intended — it blocked an address that was not allow-listed — but the deployment procedure depends on the operator keeping `my_ip_cidr` current. The README now tells the operator to compare `terraform.tfvars` with the current public IP before every deploy. I did **not** solve the problem by opening SSH to `0.0.0.0/0`.

---

## What Went Wrong, and How It Was Caught

| What was wrong | How it was caught | Correction | Proof |
|---|---|---|---|
| CA1 reused the default VPC/subnet | Professor feedback + class debrief | Terraform-owned VPC, subnet, IGW, route table/association | Fresh apply + full destroy/scan |
| Processor/REST changed on every playbook run | Second-run idempotency check | Desired-state containers/services and guarded builds | Second complete run `changed=0` |
| Old CA0 IP fallbacks remained in applications | Search/review against centralized-config guidance | Required environment settings; no old-IP fallbacks | Missing-setting tests + fresh rebuild |
| Original smoke test inferred Kafka rather than observing it directly | Professor feedback | Unique trace ID + Kafka partition/offset + processor trace | PASS output + deliberate failure test |
| Destroy had no independent AWS proof | Class checklist | `Project=CA1` tags + `aws_scan.py` | Positive control before destroy; CLEAN after |
| Scanner treated an optional permission denial badly / exposed too much error detail | Real AWS run | Optional `[skip]`, required-check failure behavior, account masking | Real scan before/after destroy |
| README had a claim that did not match `.gitignore` | Documentation audit | Correct fresh-clone vault setup | README and repository policy agree |
| `my_ip_cidr` went stale | Fresh rebuild: all four SSH connections timed out | Update current `/32` and re-apply SG | `0 added, 1 changed, 0 destroyed`, then Ansible succeeded |
| Kafka and MongoDB image tags were still repeated in Compose templates | Final comparison against professor parameterization feedback | Add `kafka_image` / `mongo_image` to `config.yml` and reference them from the templates | grep shows the literal tags only in `config.yml` |

---

## Ownership

I own this outcome. AI helped write and troubleshoot parts of the implementation, but the tests above are the reason I accept the claims. When an explanation and the tool output disagreed, I treated the output as authoritative and changed the implementation or documentation.

## Escalation Path

The project deliberately leaves some decisions to a person:

- **Partial Terraform failure:** read the error and re-run `terraform apply`; do not blindly auto-retry AWS indefinitely.
- **Operator IP changed:** update `my_ip_cidr` to the current `/32` and run `terraform apply`; never widen SSH/REST to `0.0.0.0/0` just to make the error disappear.
- **Vault missing/wrong:** stop and fix the local secret setup. Do not fall back to unauthenticated MongoDB.
- **Required cleanup-scan permission fails:** scanner exits as an error instead of reporting CLEAN. The person decides whether to fix the permission or investigate manually.
- **Destroy:** remains an explicit confirmation-gated operator action rather than an automatic timer.

## Risks / Known Limitations

1. **Operator IP is manual.** A network change can make `terraform.tfvars` stale and block access until Terraform updates the security group.
2. **Terraform state contains the generated SSH private key.** State is ignored by Git but must be protected locally.
3. **Applications use the MongoDB administrative credential.** A production system should use a least-privilege application account.
4. **The IAM user cannot call `tag:GetResources`.** The scanner reports the optional cross-service tag check as skipped; the required resource/billable checks still run.
5. **The MongoDB compose-render task should not be treated as safe for secret-revealing `--diff` output.** File permissions protect the rendered file on the host, but verbose diff output is a separate concern.
6. **The REST API has no application-layer authentication.** Network exposure is limited by the security group.
7. **The 5,000-row demo validates correctness, not full-scale performance.**

## AI-Assisted Work

I used Claude to help write and troubleshoot Terraform, Ansible, and the validation/cleanup scripts. I also used it to compare the implementation against the grading debrief. The important part was independent verification:

- AI suggestions that recreated containers every run were rejected after the second-run evidence showed the problem.
- The stronger smoke test was accepted only after it produced a unique-event PASS and a deliberate processor-stop FAIL.
- The cleanup scanner was changed after real AWS permission/error behavior exposed problems that local reasoning had missed.
- The final rebuild exposed a stale-IP problem that was not obvious from code review alone.
- The professor-identified Kafka/Mongo image-tag gap was closed with a minimal shared-config change, and a grep check proved the tags are no longer duplicated in the Compose templates.
- Documentation claims were checked against the repository and corrected when they did not match reality.

The Integrity Packet therefore records not just what AI suggested, but what I tested, what failed, what changed, and what evidence made me accept the result.

# Integrity Packet — CA1

*Last updated: September 19, 2026*

**Owner: Amshu Wagle. I own this outcome.**

## Outcome
I automated CA0's entire manual deployment using Terraform (infrastructure) and Ansible (configuration), so the same four-VM network intrusion detection pipeline — producer, Kafka, processor, MongoDB, REST API — can be created and destroyed with two commands each, with zero manual console-clicking and zero manual SSH-and-type-commands configuration. The goal was the same as CA0's: prove genuine, working data flow across four separate machines — but this time, prove that the *deployment itself* is repeatable, not just the pipeline's behavior once it happens to be running.

## Assumptions
- The person running this owns an AWS account and is willing to create a scoped IAM user rather than use root — this project doesn't assume access to my specific account.
- The default VPC exists in the target region with at least one subnet. I read both via Terraform data sources rather than hardcoding IDs, so this project follows whatever subnet AWS's data source happens to pick — which may land in a different AZ on a different run (see Risk, below).
- Terraform's local state file is authoritative for what currently exists. If AWS resources are changed by hand in the console between Terraform runs, the next `plan`/`apply` may not reconcile correctly without an explicit `terraform refresh` — this project assumes the console isn't touched manually between Terraform operations.
- Ansible's inventory (`inventory.ini`) always reflects the *current* apply's IPs, because Terraform regenerates it every time. The playbooks assume it's never a stale file from a previous run.
- The vault password file (`.vault_pass`) exists locally before `ansible-playbook` runs. Without it, every vault-dependent task fails immediately and loudly, rather than silently falling back to a wrong or default credential.

## Evidence
- A full `terraform apply` → `destroy` → `apply` round trip, producing entirely different instance IDs and IPs the second time — proof this isn't tied to leftover state from the first run, not just a claim that it "should" work.
- All six Ansible role `PLAY RECAP`s showing `failed=0, unreachable=0` on every host, for every role, in order.
- `cat group_vars/all/vault.yml` showing AES256 ciphertext, not plaintext, independently confirmed after encryption.
- The security group's actual AWS Console **Inbound rules tab** (not the instance-launch wizard) — this specific gap was flagged in CA0's grading, so closing it here with the correct evidence type was deliberate, not incidental.
- Producer logs showing all 5,000 rows sent; processor logs showing a climbing `[ALERT]` count reaching 5,000; REST API `/health` and `/alerts` both returning correct live data over `curl` run from outside AWS entirely.
- A final `terraform destroy` run against the *fully-configured* pipeline (Docker, Kafka, Mongo, everything Ansible touched) — not just the bare infrastructure from an earlier reproducibility test — followed by an independent AWS Console check confirming zero running instances.

## Validation
- Ran the full `terraform apply` → `ansible-playbook site.yml` → smoke test → `terraform destroy` cycle twice, with completely different resulting IPs each time, specifically to rule out "it worked because of leftover state" as an explanation.
- Ran `terraform validate` after adding each individual `.tf` file, not just once at the end. This caught a real bug immediately: a missing newline in an early version of `variables.tf` that broke parsing, found and fixed before any AWS credentials were even involved.
- Diagnosed an Ansible SSH failure (`no such identity: ./ca1-key.pem`) by checking which directory Ansible was actually being invoked from, rather than guessing at Ansible configuration flags — the real cause was a relative key path that only resolved correctly from one specific working directory. Fixed by switching to `abspath()` in Terraform's `key_pair.tf`, which is a fix at the source of truth rather than a workaround in Ansible.
- Verified the vault is genuinely *usable*, not just encrypted-looking, by confirming Mongo, the processor, and the REST API could all successfully authenticate using the values Ansible decrypted from it — encryption alone doesn't prove the credentials round-trip correctly.
- Ran a third, fully independent `terraform apply` → `ansible-playbook site.yml` → smoke test cycle specifically to record the demo video, producing four brand-new instance IPs with no relationship to any earlier example in this document. Producer sent 5,000/5,000 rows again; processor's alert count climbed to 5,000 again; both REST endpoints returned correct live data again — a third cold-start proof, not just the first two.
- During that same session, an ad-hoc `docker logs` command against the processor host failed with a Docker-socket permission error, even though the processor role's own build/run tasks had already succeeded moments earlier in the same playbook run. Likely cause: the `ubuntu` user's docker-group membership hadn't been picked up by that specific ad-hoc connection yet, and Ansible ad-hoc commands don't automatically use elevated privileges the way I could choose to configure playbook tasks to. Fixed by adding `--become` to that one command. Not a deployment defect — the deploy itself never failed — but real enough, and common enough for anyone else running these exact commands, that the README's validation command now includes `--become` by default rather than leaving it as a surprise.

## Ownership
I own this outcome. If any part of it turns out to be wrong, that's on me, not on the tools I used to help build it.

## Escalation Path
This automation is not fully self-healing, and I don't want it to look like it is. These are the specific places I deliberately left a human in the loop instead of having the code guess or auto-retry:

- **A `terraform apply` that fails partway through** (say, after 2 of 4 VMs are created) is not automatically rolled back or retried. Terraform's own state file reflects exactly what succeeded, and simply re-running `terraform apply` picks up from there correctly — but nothing auto-retries on failure, because a repeated failure (an AWS service limit, for instance) deserves a person actually reading the error, not the automation quietly hammering the API.
- **If Ansible can't SSH into a freshly-created VM** — most likely because its cloud-init hasn't finished yet — the playbook fails loudly for that host rather than silently skipping it or retrying on a timer. On a slow AWS day, a person may need to simply re-run `ansible-playbook site.yml` a few seconds later. I chose not to add automatic wait-and-retry logic here, since a loud failure is more honest than a silent delay.
- **If the vault password file is missing or wrong**, every vault-dependent task fails immediately with a clear Ansible error, instead of falling back to an unauthenticated MongoDB connection. A missing secret should stop the deploy, not silently make it less secure.
- **Nothing here decides "is my AWS bill too high" or "am I actually done working."** `terraform destroy` is a separate, explicit, confirmation-gated command — never run on a timer or automatically — because that's a judgment call for a person, not a rule a script should enforce.
- **Ad-hoc validation commands don't inherit the same privilege as the playbook that deployed everything**, as seen when a `docker logs` ad-hoc command hit a permission error the processor role's own tasks never did. Rather than silently retrying or masking that, the fix (`--become`) is now the documented default, so a person understands why it's there instead of the automation quietly working around a privilege boundary it hit.

## Risk
- **A static IAM access key, configured via `aws configure`, rather than a short-lived session token.** I chose this specifically because the Terraform AWS provider doesn't yet recognize `aws login`'s newer SSO-style credential format ([hashicorp/terraform-provider-aws#45316](https://github.com/hashicorp/terraform-provider-aws/issues/45316)). The trade-off: a static key is longer-lived and marginally more sensitive if leaked than a session token would be. Mitigated by scoping the IAM user to `AmazonEC2FullAccess` only — never admin, never root.
- **The vault password lives in a local, gitignored file (`.vault_pass`), not a managed secrets service.** For a single-developer class project on a personal AWS account, a cloud secrets manager (AWS Secrets Manager, a hosted Vault server) felt like disproportionate infrastructure for the actual risk. Ansible Vault's file-based encryption is still real AES256 encryption, and the plaintext password never touches git either way.
- **No automatic retry or self-healing**, as described under Escalation Path above — a deliberate design choice, worth stating plainly as a real limitation rather than leaving it implicit.
- **Subnet/AZ is chosen by a data source, not pinned.** A future run could land in a different AZ than either this run or CA0's, which is harmless for connectivity but means the exact AZ isn't a stable, citable fact about this project — it's whatever AWS's default subnet listing returns that day.
- **Carried forward from CA0, unchanged:** a 5,000-row demo dataset doesn't prove behavior at the full ~191k-row scale, and the REST API still has no authentication of its own. Both are acceptable for a graded class demo, not for anything resembling production use.

## AI-Assisted Work
I used Claude mainly to help write and troubleshoot the Terraform and Ansible code itself — the architecture decisions were mine, same as CA0.


- **Verified independently before trusting:** when Ansible failed to SSH with "no such identity," I confirmed the actual root cause myself — which working directory Ansible was invoked from, and how that interacted with the key's relative path — before accepting a fix, rather than applying the first suggestion untested. The `aws login` / Terraform incompatibility was checked against the real, open GitHub issue before I committed to the static-key approach, rather than taken on faith.
- **Decisions I made, not the AI:**
  - Choosing Terraform + Ansible over Puppet, Chef, or CloudFormation. My reasoning: Terraform's plan/apply/destroy lifecycle fits standing up and tearing down a small, disposable pipeline far better than Puppet or Chef, which are built around continuously reconciling long-lived server fleets rather than one-shot creation and destruction. Ansible's agentless, SSH-based model meant nothing extra had to be pre-installed on a VM before Ansible could start configuring it — unlike Chef or Puppet, which typically need an agent already present. CloudFormation would have locked this to AWS-only syntax for no real benefit here, since nothing about this pipeline needs AWS-specific resource types beyond what Terraform's `aws` provider already covers cleanly.
  - Adding MongoDB authentication, which CA0 didn't have at all — a deliberate security improvement I chose to make, not something suggested to me.
  - Parameterizing instance size specifically so CA2/CA3 can push the pipeline under load later by changing one variable, not editing four files.
- **Debugging approach:** most debugging came from reading Terraform's and Ansible's own output directly — both tools are unusually explicit about what failed and why. The `variables.tf` syntax error, the missing newline, and the relative-path SSH key issue were all diagnosable from the tool's own error message before I needed to ask anything. I used Claude more as a second pair of eyes once I already had a theory about the cause, not as the source of the diagnosis itself.

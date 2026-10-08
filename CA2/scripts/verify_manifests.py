# Checks that the Kubernetes manifests, the programs and the CA2 requirements agree with each other.
# It needs no cluster: it renders the manifests with "kubectl kustomize" and reads the result as data.
#
#   make verify          (or:  python3 scripts/verify_manifests.py)
#
#   PASS  checked and correct
#   FAIL  wrong: something will break, or a CA2 requirement is not met. Fix it.
#   WARN  it works, but it is weaker than the design (for example, a container that runs as root)
#   TODO  a requirement that a later step adds, so it is not an error yet

import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("This check needs PyYAML. Install it with:  python3 -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent

# Render the manifests exactly the way "kubectl apply -k ." will see them.
out = subprocess.run(["kubectl", "kustomize", str(ROOT)], capture_output=True, text=True)
if out.returncode != 0:
    sys.exit("kubectl kustomize failed:\n" + out.stderr)
docs = [d for d in yaml.safe_load_all(out.stdout) if d]

# The scaling manifests are applied on demand (make scale-up), but they are part of the project, so they are checked too.
scaling = []
for f in sorted((ROOT / "producer" / "scaling").glob("*.yaml")):
    scaling += [d for d in yaml.safe_load_all(f.read_text()) if d]
objects = docs + scaling


def kind(d): return d["kind"]
def name(d): return d["metadata"]["name"]
def of(k): return [d for d in objects if kind(d) == k]
def pod(w): return w["spec"]["template"]["spec"]
def labels(w): return w["spec"]["template"]["metadata"].get("labels", {})
def containers(w): return pod(w).get("containers", [])
def all_containers(w): return containers(w) + pod(w).get("initContainers", [])
def matches(selector, w): return all(labels(w).get(k) == v for k, v in selector.items())


by = {(kind(d), name(d)): d for d in objects}
workloads = of("Deployment") + of("StatefulSet") + of("Job")
services = {name(s): s for s in of("Service")}
config_maps = {name(d): d.get("data", {}) for d in of("ConfigMap")}
service_accounts = {name(d) for d in of("ServiceAccount")}
SECRET = "mongo-credentials"   # made by scripts/create-secrets.sh and never stored in git, so it is not among the manifests
OWN_CODE = {"processor", "restapi", "producer-replay", "producer-load"}   # the workloads that run our own code

rows = []


def judge(group, label, fails, warns=()):
    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    rows.append((status, group, label, list(fails) + list(warns)))


def todo(group, label, why):
    rows.append(("TODO", group, label, [why]))


# ------------------------------------------------------------ 1. every workload
G = "1. Every workload (Deployment, StatefulSet, Job)"
for w in workloads:
    n, ps, fails, warns = name(w), pod(w), [], []
    if ps.get("serviceAccountName") not in service_accounts:
        fails.append("serviceAccountName " + str(ps.get("serviceAccountName")) + " is not defined")
    volumes = {v["name"] for v in ps.get("volumes", [])} | {t["metadata"]["name"] for t in w["spec"].get("volumeClaimTemplates", [])}
    for c in all_containers(w):
        cn, ports = c["name"], {p["name"] for p in c.get("ports", []) if "name" in p}
        for ef in c.get("envFrom", []):
            ref = ef.get("configMapRef", {}).get("name")
            if ref and ref not in config_maps:
                fails.append(cn + ": ConfigMap " + ref + " is used but not defined")
        for vm in c.get("volumeMounts", []):
            if vm["name"] not in volumes:
                fails.append(cn + ": volume " + vm["name"] + " is mounted but not defined")
        tag = c["image"].split("/")[-1]
        if ":" not in tag or tag.endswith(":latest"):
            fails.append(cn + ": image " + c["image"] + " is not pinned to a version tag")
        for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
            port = (c.get(probe, {}).get("httpGet") or c.get(probe, {}).get("tcpSocket") or {}).get("port")
            if isinstance(port, str) and port not in ports:
                fails.append(cn + ": " + probe + " uses port name " + port + ", which the container does not declare")
        for e in c.get("env", []):
            if re.search(r"PASSWORD|SECRET|TOKEN", e["name"]) and "value" in e and not e["name"].endswith("_FILE"):
                fails.append(cn + ": " + e["name"] + " holds a literal value; credentials must come from the mounted Secret")
        if "requests" not in c.get("resources", {}) or "limits" not in c.get("resources", {}):
            warns.append(cn + ": no resource requests and limits (the autoscaler needs the CPU request)")
    for v in ps.get("volumes", []):
        if "secret" in v and v["secret"]["secretName"] != SECRET:
            fails.append("volume " + v["name"] + " uses Secret " + v["secret"]["secretName"] + ", expected " + SECRET)
    if ps.get("enableServiceLinks") is not False:
        if n == "kafka":
            fails.append("enableServiceLinks must be false: the Confluent image reads every KAFKA_* variable as configuration")
        else:
            warns.append("enableServiceLinks is not false (Kubernetes injects variables about every Service)")
    if ps.get("automountServiceAccountToken") is not False:
        warns.append("the Kubernetes API token is mounted, although no program here calls the Kubernetes API")
    if n in OWN_CODE:
        sc = ps.get("securityContext", {})
        if not (sc.get("runAsNonRoot") is True and isinstance(sc.get("runAsUser"), int)):
            warns.append("not set to run as a non-root user (runAsNonRoot: true plus a numeric runAsUser)")
        for c in containers(w):
            cs = c.get("securityContext", {})
            if cs.get("allowPrivilegeEscalation") is not False or "ALL" not in cs.get("capabilities", {}).get("drop", []):
                warns.append(c["name"] + ": allowPrivilegeEscalation is not false, or capabilities are not dropped")
    judge(G, n + " (" + kind(w) + ")", fails, warns)

# ------------------------------------------------------------ 2. every Service
G = "2. Every Service"
for n, s in services.items():
    matched = [w for w in workloads if matches(s["spec"].get("selector", {}), w)]
    declared = {p.get("name"): p["containerPort"] for w in matched for c in containers(w) for p in c.get("ports", [])}
    stype, fails = s["spec"].get("type", "ClusterIP"), []
    if not matched:
        fails.append("its selector matches no pod")
    for p in s["spec"]["ports"]:
        target = p.get("targetPort", p["port"])
        if (isinstance(target, str) and target not in declared) or (isinstance(target, int) and target not in declared.values()):
            fails.append("targetPort " + str(target) + " is not a port of the pods it selects")
    if n in ("kafka", "kafka-headless", "mongo", "mongo-headless", "processor") and stype != "ClusterIP":
        fails.append("is " + stype + ": it must be reachable only from inside the cluster")
    judge(G, n + " (" + stype + (", headless" if s["spec"].get("clusterIP") == "None" else "") + ")", fails)
for ss in of("StatefulSet"):
    headless = services.get(ss["spec"]["serviceName"])
    ok = headless is not None and headless["spec"].get("clusterIP") == "None"
    judge(G, name(ss) + " StatefulSet names a headless Service", [] if ok else ["serviceName " + ss["spec"]["serviceName"] + " is not a headless Service"])
published = [n for n, s in services.items() if s["spec"].get("type") in ("NodePort", "LoadBalancer")]
judge(G, "only the REST API is published to the outside", [] if published == ["restapi"] else ["published Services: " + str(published) + " (expected exactly ['restapi'])"])

# ------------------------------------------------------------ 3. the two stateful stages
G = "3. Kafka and MongoDB"


def env_of(w): return {e["name"]: e for c in containers(w) for e in c.get("env", [])}


kafka, mongo = by.get(("StatefulSet", "kafka")), by.get(("StatefulSet", "mongo"))
if kafka is None:
    judge(G, "Kafka StatefulSet exists", ["no StatefulSet named kafka"])
else:
    env = env_of(kafka)
    fails = [] if kafka["spec"].get("volumeClaimTemplates") else ["no volumeClaimTemplates: Kafka would lose its log when the pod restarts"]
    for needed in ("KAFKA_PROCESS_ROLES", "KAFKA_NODE_ID", "KAFKA_CONTROLLER_QUORUM_VOTERS", "KAFKA_CONTROLLER_LISTENER_NAMES", "KAFKA_LISTENERS",
                   "KAFKA_ADVERTISED_LISTENERS", "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "KAFKA_INTER_BROKER_LISTENER_NAME", "CLUSTER_ID"):
        if needed not in env:
            fails.append(needed + " is missing (needed for KRaft mode)")
    judge(G, "Kafka: KRaft settings and a PersistentVolumeClaim", fails)
    parts = env.get("KAFKA_NUM_PARTITIONS", {}).get("value")
    enough = parts is not None and int(parts) >= 3
    judge(G, "Kafka: at least 3 partitions, so the processor can scale to 3 replicas", [], [] if enough else ["KAFKA_NUM_PARTITIONS is " + str(parts) + ": extra processor replicas would have nothing to read"])
if mongo is None:
    judge(G, "MongoDB StatefulSet exists", ["no StatefulSet named mongo"])
else:
    env = env_of(mongo)
    fails = [] if mongo["spec"].get("volumeClaimTemplates") else ["no volumeClaimTemplates: MongoDB would lose its data when the pod restarts"]
    for needed in ("MONGO_INITDB_ROOT_USERNAME_FILE", "MONGO_INITDB_ROOT_PASSWORD_FILE"):
        if needed not in env:
            fails.append(needed + " is missing: MongoDB would start without authentication, or with a password in the manifest")
    judge(G, "MongoDB: password from a mounted file, and a PersistentVolumeClaim", fails)

# ------------------------------------------------------------ 4. the programs get everything they require
G = "4. Each program gets everything it requires"
PROGRAMS = {"processor/processor.py": ["processor"], "producer/producer.py": ["producer-replay", "producer-load"], "restapi/rest_app.py": ["restapi"]}
script = ROOT / "scripts" / "create-secrets.sh"
secret_keys = set(re.findall(r"--from-file=(\w+)=", script.read_text())) if script.exists() else set()
for program, names in PROGRAMS.items():
    if not (ROOT / program).exists():
        judge(G, program, [program + " does not exist"])
        continue
    text = (ROOT / program).read_text()
    required, required_secrets = re.findall(r'require\("([A-Z_]+)"\)', text), re.findall(r'require_secret\("([A-Z_]+)"\)', text)
    for wname in names:
        w = by.get(("Deployment", wname)) or by.get(("Job", wname))
        if w is None:
            todo(G, program + " in " + wname, "added in the scaling step") if wname == "producer-load" else judge(G, program + " in " + wname, ["no Deployment or Job named " + wname])
            continue
        provided, files, mounts = set(), {}, []
        secret_volumes = {v["name"] for v in pod(w).get("volumes", []) if "secret" in v}
        for c in containers(w):
            for ef in c.get("envFrom", []):
                provided |= set(config_maps.get(ef.get("configMapRef", {}).get("name"), {}))
            for e in c.get("env", []):
                provided.add(e["name"])
                if e["name"].endswith("_FILE"):
                    files[e["name"]] = e.get("value", "")
            mounts += [vm["mountPath"] for vm in c.get("volumeMounts", []) if vm["name"] in secret_volumes]
        fails = ["requires " + r + ", but nothing provides it" for r in required if r not in provided]
        for s_ in required_secrets:
            f = files.get(s_ + "_FILE")
            if not f:
                fails.append("requires " + s_ + ", but " + s_ + "_FILE is not set")
            elif not any(f.startswith(m.rstrip("/") + "/") for m in mounts):
                fails.append(s_ + "_FILE=" + f + " is not inside a mounted Secret")
            elif Path(f).name not in secret_keys:
                fails.append(f + " reads the key " + Path(f).name + ", which scripts/create-secrets.sh does not create")
        judge(G, program + " in " + wname + ": " + str(len(required)) + " settings + " + str(len(required_secrets)) + " secret files", fails)

merged = {}
for d in of("ConfigMap"):
    merged.update(d.get("data", {}))


def svc_ports(n): return {p["port"] for p in services[n]["spec"]["ports"]} if n in services else set()


def container_port(wname, pname):
    w = by.get(("Deployment", wname))
    return next((p["containerPort"] for c in containers(w) for p in c.get("ports", []) if p.get("name") == pname), None) if w else None


fails = []
host, _, port = merged.get("KAFKA_BROKER", "").partition(":")
if host not in services or not port.isdigit() or int(port) not in svc_ports(host):
    fails.append("KAFKA_BROKER=" + merged.get("KAFKA_BROKER", "") + " does not match a Service and port")
if merged.get("MONGO_HOST") not in services or not merged.get("MONGO_PORT", "").isdigit() or int(merged["MONGO_PORT"]) not in svc_ports(merged["MONGO_HOST"]):
    fails.append("MONGO_HOST / MONGO_PORT do not match a Service and port")
m = merged.get("METRICS_PORT", "")
if not m.isdigit() or container_port("processor", "metrics") != int(m) or int(m) not in svc_ports("processor"):
    fails.append("METRICS_PORT=" + m + " must equal the processor's containerPort and the processor Service port")
r = merged.get("REST_PORT", "")
if not r.isdigit() or container_port("restapi", "http") != int(r) or int(r) not in svc_ports("restapi"):
    fails.append("REST_PORT=" + r + " must equal the REST API's containerPort and the restapi Service port")
if not (ROOT / "producer" / merged.get("DATASET_FILE", "-")).is_file():
    fails.append("DATASET_FILE=" + merged.get("DATASET_FILE", "") + " is not in the producer/ folder, so the image cannot contain it")
if merged.get("MONGO_DB") != "ca2":
    fails.append("MONGO_DB=" + str(merged.get("MONGO_DB")) + ": use ca2 (an old value from an earlier assignment would be confusing in the README and logs)")
judge(G, "ConfigMap values match the Services, ports, dataset and database name", fails)

# ------------------------------------------------------------ 5. images and the build workflow
G = "5. Images and registry"
version = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else None
for comp in ("producer", "processor", "restapi"):
    fails, df, rq = [], ROOT / comp / "Dockerfile", ROOT / comp / "requirements.txt"
    if not df.exists():
        fails.append("Dockerfile is missing")
    else:
        user, base = re.findall(r"^USER\s+(\S+)", df.read_text(), flags=re.M), re.findall(r"^FROM\s+(\S+)", df.read_text(), flags=re.M)
        if not user or not user[-1].isdigit():
            fails.append("the Dockerfile must end with a numeric USER (for example USER 1000)")
        if not base or ":" not in base[0]:
            fails.append("FROM is not pinned to a tag")
    if not rq.exists():
        fails.append("requirements.txt is missing")
    else:
        fails += ["requirements.txt: " + line + " is not pinned with ==" for line in rq.read_text().split() if "==" not in line]
    images = [c["image"] for w in workloads for c in containers(w) if "/ca2-" + comp + ":" in c["image"]]
    if not images:
        fails.append("no manifest uses an image named ca2-" + comp)
    fails += [i + " does not end with the version in CA2/VERSION (" + str(version) + ")" for i in images if version is None or not i.endswith(":" + version)]
    judge(G, comp + ": Dockerfile, pinned requirements, image tag equals VERSION", fails)
workflow = ROOT.parent / ".github" / "workflows" / "ca2-images.yml"
ok = workflow.exists() and all(c in workflow.read_text() for c in ("producer", "processor", "restapi"))
judge(G, "GitHub Actions workflow builds and pushes all three images", [] if ok else [str(workflow) + " is missing or does not list the three components"])

# ------------------------------------------------------------ 6. security and scaling objects
G = "6. Network rules, access rules and autoscalers"
policies = of("NetworkPolicy")
if not policies:
    todo(G, "NetworkPolicies", "added in the security step")
else:
    fails = []
    for p in policies:
        selectors = [p["spec"].get("podSelector", {})]
        for rule in p["spec"].get("ingress", []) + p["spec"].get("egress", []):
            selectors += [t["podSelector"] for t in rule.get("from", []) + rule.get("to", []) if "podSelector" in t and "namespaceSelector" not in t]
        for sel in selectors:
            ml = sel.get("matchLabels", {})
            if ml and not any(matches(ml, w) for w in workloads) and ml.get("app.kubernetes.io/component") != "observer":
                fails.append(name(p) + ": selector " + str(ml) + " matches no pod, so the rule does nothing")
    judge(G, str(len(policies)) + " NetworkPolicies: every selector matches a real pod", fails)
    deny = any(p["spec"].get("podSelector") == {} and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"} and not p["spec"].get("ingress") and not p["spec"].get("egress") for p in policies)
    judge(G, "default-deny-all: no pod accepts or sends traffic unless a policy allows it", [] if deny else ["no policy with an empty podSelector, both policy types and no rules"])
roles = of("Role")
if not roles:
    todo(G, "RBAC Roles and RoleBindings", "added in the security step")
else:
    fails = [name(r_) + " allows access to secrets" for r_ in roles if any("secrets" in rule.get("resources", []) or "*" in rule.get("resources", []) for rule in r_.get("rules", []))]
    for b in of("RoleBinding"):
        if ("Role", b["roleRef"]["name"]) not in by:
            fails.append(name(b) + " refers to Role " + b["roleRef"]["name"] + ", which is not defined")
        fails += [name(b) + " gives a Role to ServiceAccount " + s_["name"] + ", which is not defined" for s_ in b.get("subjects", []) if s_["name"] not in service_accounts]
    judge(G, str(len(roles)) + " Roles, " + str(len(of("RoleBinding"))) + " RoleBindings: none can read Secrets, all bindings resolve", fails)
hpas = of("HorizontalPodAutoscaler")
if not hpas:
    todo(G, "HorizontalPodAutoscalers", "added in the scaling step")
for h in hpas:
    t, fails = by.get(("Deployment", h["spec"]["scaleTargetRef"]["name"])), []
    if t is None:
        fails.append("its target Deployment does not exist")
    else:
        if "replicas" in t["spec"]:
            fails.append("the target Deployment also sets replicas, which would fight the autoscaler on every apply")
        if "cpu" not in containers(t)[0].get("resources", {}).get("requests", {}):
            fails.append("the target has no CPU request, so the autoscaler cannot compute utilisation")
    judge(G, "HPA " + name(h) + ": " + str(h["spec"]["minReplicas"]) + " to " + str(h["spec"]["maxReplicas"]) + " replicas of " + h["spec"]["scaleTargetRef"]["name"], fails)

# ------------------------------------------------------------ 7. progress against the CA2 assignment
G = "7. The CA2 assignment, requirement by requirement"
makefile = (ROOT / "Makefile").read_text() if (ROOT / "Makefile").exists() else ""
has = lambda k, n: (k, n) in by
checks = [
    ("Kafka: StatefulSet + PVC + Service (KRaft)", has("StatefulSet", "kafka") and bool(kafka and kafka["spec"].get("volumeClaimTemplates")) and has("Service", "kafka"), False),
    ("Database: StatefulSet + PVC + Service", has("StatefulSet", "mongo") and bool(mongo and mongo["spec"].get("volumeClaimTemplates")) and has("Service", "mongo"), False),
    ("Processor: Deployment + ConfigMap + Secret + ClusterIP Service", has("Deployment", "processor") and has("ConfigMap", "processor-config") and services.get("processor", {}).get("spec", {}).get("type", "ClusterIP") == "ClusterIP", False),
    ("Producer: Job for a one-off replay", has("Job", "producer-replay"), False),
    ("ConfigMaps and a Secret for settings and credentials", len(config_maps) >= 2 and script.exists(), False),
    ("A REST endpoint declared and exposed", services.get("restapi", {}).get("spec", {}).get("type") in ("NodePort", "LoadBalancer"), False),
    ("Secrets mounted, not embedded in manifests", all(not (re.search(r"PASSWORD|SECRET|TOKEN", e["name"]) and "value" in e and not e["name"].endswith("_FILE")) for w in workloads for c in containers(w) for e in c.get("env", [])), False),
    ("Only the necessary ports are exposed", published == ["restapi"], False),
    ("One command to apply the stack and one to delete it (make up / make down)", bool(re.search(r"^up:", makefile, flags=re.M)) and bool(re.search(r"^down:", makefile, flags=re.M)), False),
    ("Dockerfiles and a registry push for the three custom images", all((ROOT / c / "Dockerfile").exists() for c in ("producer", "processor", "restapi")) and workflow.exists(), False),
    ("NetworkPolicy restricting traffic between services", bool(policies), True),
    ("RBAC: a Role and a RoleBinding", bool(roles) and bool(of("RoleBinding")), True),
    ("HorizontalPodAutoscaler for the producers (1 to N)", any(h["spec"]["scaleTargetRef"]["name"].startswith("producer") for h in hpas), True),
    ("HorizontalPodAutoscaler for the processor (optional elasticity)", any(h["spec"]["scaleTargetRef"]["name"] == "processor" for h in hpas), True),
]
for label, passed, later in checks:
    rows.append(("PASS" if passed else ("TODO" if later else "FAIL"), G, label, []))

# ------------------------------------------------------------ the report
counts, kinds = {"PASS": 0, "FAIL": 0, "WARN": 0, "TODO": 0}, {}
for d in docs:
    kinds[kind(d)] = kinds.get(kind(d), 0) + 1
print("CA2 manifest check: " + str(len(docs)) + " objects rendered by kubectl kustomize (" + ", ".join(str(v) + " " + k for k, v in sorted(kinds.items(), key=lambda x: -x[1])) + ")")
current = None
for status, group, label, detail in rows:
    if group != current:
        print("\n" + group)
        current = group
    counts[status] += 1
    print("  " + status + "  " + label)
    for line in detail:
        print("        - " + line)
print("\nRESULT: " + str(counts["PASS"]) + " passed, " + str(counts["FAIL"]) + " failed, " + str(counts["WARN"]) + " warnings, " + str(counts["TODO"]) + " still to do (later steps)")
sys.exit(1 if counts["FAIL"] else 0)

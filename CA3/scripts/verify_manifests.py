# Checks that the Kubernetes manifests, the programs and the CA3 requirements agree with each other.
# It needs no cluster: it renders the manifests with "kubectl kustomize" and reads the result as data.
# Section 9 also reads the README, the Integrity Packet, the AI log and the evidence folder, and compares what they say with the files.
#
#   make verify          (or:  python3 scripts/verify_manifests.py)
#
#   PASS  checked and correct
#   FAIL  wrong: something will break, or a CA3 requirement is not met. Fix it.
#   WARN  it works, but it is weaker than the design (for example, a container that runs as root)
#   TODO  a requirement that a later step adds, so it is not an error yet

import ast
import copy
import fnmatch
import os
import re
import struct
import subprocess
import sys
import urllib.parse
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("This check needs PyYAML. Install it with:  python3 -m pip install pyyaml")

ROOT = Path(__file__).resolve().parent.parent

# Render the manifests exactly the way "kubectl apply -k ." will see them. The overlays of the scaling test are rendered too.
def render(path):
    done = subprocess.run(["kubectl", "kustomize", str(path)], capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit("kubectl kustomize " + str(path) + " failed:\n" + done.stderr)
    return [d for d in yaml.safe_load_all(done.stdout) if d]


docs = render(ROOT)
OVERLAYS = {n: render(ROOT / "producer" / n) for n in ("load", "loadtest", "loadtest-one")}

# The smoke-test Job (make smoke) is applied on demand, but it is part of the project, so it is checked too.
smoke_docs = []
for f in sorted((ROOT / "producer" / "smoke").glob("*.yaml")):
    smoke_docs += [d for d in yaml.safe_load_all(f.read_text()) if d]
objects = docs + smoke_docs


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
OWN_CODE = {"bootstrap", "processor", "restapi", "producer-replay", "producer-load", "producer-smoke"}   # the workloads that run our own code


# The Secrets of the stack are made inside the cluster by processor/bootstrap.py, so they are not among the manifests. Their names and the
# names of the files inside them are read from the program: every items.append((name, description, {key: value})) is one Secret.
BOOTSTRAP = ROOT / "processor" / "bootstrap.py"
BOOTSTRAP_TEXT = BOOTSTRAP.read_text() if BOOTSTRAP.is_file() else ""


def secrets_made():
    made = {}
    if not BOOTSTRAP_TEXT:
        return made
    for node in ast.walk(ast.parse(BOOTSTRAP_TEXT)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "items" and node.args and isinstance(node.args[0], ast.Tuple) and len(node.args[0].elts) == 3):
            first, _, third = node.args[0].elts
            keys = {k.value for k in third.keys if isinstance(k, ast.Constant)} if isinstance(third, ast.Dict) else set()
            if isinstance(first, ast.Constant):
                made[first.value] = keys
            elif isinstance(first, ast.JoinedStr) and "kafka-client-" in ast.unparse(first):
                for who in ("producer", "processor", "exporter"):
                    made["kafka-client-" + who] = keys
    return made


SECRETS = secrets_made()

rows = []


def judge(group, label, fails, warns=()):
    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    rows.append((status, group, label, list(fails) + list(warns)))


def todo(group, label, why):
    rows.append(("TODO", group, label, [why]))


# What the Pod Security profile "restricted" demands of a pod (the namespace enforces it, see namespace.yaml). A pod that breaks one of these
# is refused by the API server, so a problem here would show up as a Deployment that never gets a pod.
ALLOWED_VOLUMES = {"configMap", "csi", "downwardAPI", "emptyDir", "ephemeral", "persistentVolumeClaim", "projected", "secret"}


def restricted_problems(w):
    ps, found = pod(w), []
    psc = ps.get("securityContext", {})
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if ps.get(key):
            found.append(key + " is set")
    for v in ps.get("volumes", []):
        kinds_ = set(v) - {"name"}
        if not kinds_ <= ALLOWED_VOLUMES:
            found.append("volume " + v["name"] + " is of a type that 'restricted' forbids (" + ", ".join(sorted(kinds_ - ALLOWED_VOLUMES)) + ")")
    for c in all_containers(w):
        cs = c.get("securityContext", {})
        who = c["name"] + ": "
        if cs.get("allowPrivilegeEscalation") is not False:
            found.append(who + "allowPrivilegeEscalation must be false")
        if "ALL" not in cs.get("capabilities", {}).get("drop", []):
            found.append(who + "capabilities must drop ALL")
        if cs.get("capabilities", {}).get("add") not in (None, [], ["NET_BIND_SERVICE"]):
            found.append(who + "capabilities.add may only be NET_BIND_SERVICE")
        if (cs.get("runAsNonRoot", psc.get("runAsNonRoot"))) is not True:
            found.append(who + "runAsNonRoot must be true")
        if cs.get("runAsUser", psc.get("runAsUser")) in (None, 0):
            found.append(who + "runAsUser must be a number other than 0 (Kubernetes cannot check a user name)")
        if (cs.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type") not in ("RuntimeDefault", "Localhost"):
            found.append(who + "seccompProfile must be RuntimeDefault")
        if cs.get("privileged") or any("hostPort" in p for p in c.get("ports", [])):
            found.append(who + "privileged or hostPort is set")
    return found


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
        if "secret" in v and v["secret"]["secretName"] not in SECRETS:
            fails.append("volume " + v["name"] + " uses Secret " + v["secret"]["secretName"] + ", which processor/bootstrap.py does not create (it makes: " + ", ".join(sorted(SECRETS)) + ")")
    if ps.get("enableServiceLinks") is not False:
        if n == "kafka":
            fails.append("enableServiceLinks must be false: the Confluent image reads every KAFKA_* variable as configuration")
        else:
            warns.append("enableServiceLinks is not false (Kubernetes injects variables about every Service)")
    if n == "bootstrap":
        # The one pod that talks to the Kubernetes API: it needs the token, and the token must be switched on for this pod only.
        if ps.get("automountServiceAccountToken") is not True:
            fails.append("the setup Job needs automountServiceAccountToken: true, or it cannot call the Kubernetes API")
    elif ps.get("automountServiceAccountToken") is not False:
        fails.append("the Kubernetes API token is mounted, although no program here calls the Kubernetes API (only the setup Job may)")
    fails += restricted_problems(w)
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


# {mount path: (name of the Secret, the files inside it)} for every Secret that a workload mounts. The files are the ones bootstrap.py puts in.
def secret_mounts(w):
    secret_of = {v["name"]: v["secret"]["secretName"] for v in pod(w).get("volumes", []) if "secret" in v}
    return {vm["mountPath"].rstrip("/"): (secret_of[vm["name"]], SECRETS.get(secret_of[vm["name"]], set()))
            for c in all_containers(w) for vm in c.get("volumeMounts", []) if vm["name"] in secret_of}


# The name of the Secret that holds the file at this path in this workload (mounted, and made by bootstrap.py with that file), or None.
def secret_of_file(w, path):
    for mount, (secret, files) in secret_mounts(w).items():
        if path.startswith(mount + "/") and path[len(mount) + 1:] in files:
            return secret
    return None


if kafka is None:
    judge(G, "Kafka StatefulSet exists", ["no StatefulSet named kafka"])
else:
    env = env_of(kafka)
    value = lambda k: env.get(k, {}).get("value", "")
    fails = [] if kafka["spec"].get("volumeClaimTemplates") else ["no volumeClaimTemplates: Kafka would lose its log when the pod restarts"]
    for needed in ("KAFKA_PROCESS_ROLES", "KAFKA_NODE_ID", "KAFKA_CONTROLLER_QUORUM_VOTERS", "KAFKA_CONTROLLER_LISTENER_NAMES", "KAFKA_LISTENERS",
                   "KAFKA_ADVERTISED_LISTENERS", "KAFKA_LISTENER_SECURITY_PROTOCOL_MAP", "KAFKA_INTER_BROKER_LISTENER_NAME", "CLUSTER_ID"):
        if needed not in env:
            fails.append(needed + " is missing (needed for KRaft mode)")
    judge(G, "Kafka: KRaft settings and a PersistentVolumeClaim", fails)
    parts = env.get("KAFKA_NUM_PARTITIONS", {}).get("value")
    enough = parts is not None and int(parts) >= 3
    judge(G, "Kafka: at least 3 partitions, so the processor can scale to 3 replicas", [], [] if enough else ["KAFKA_NUM_PARTITIONS is " + str(parts) + ": extra processor replicas would have nothing to read"])

    # TLS: a listener that other pods can reach must speak SSL and demand a client certificate. Plain text is allowed only on the loopback
    # address, which nothing outside the pod can reach (the broker's own traffic and the test tools use it).
    listeners = [(a.split("://")[0], a.split("://")[1].rsplit(":", 1)[0], a.rsplit(":", 1)[1]) for a in value("KAFKA_LISTENERS").split(",") if "://" in a]
    protocol = dict(a.split(":", 1) for a in value("KAFKA_LISTENER_SECURITY_PROTOCOL_MAP").split(",") if ":" in a)
    reachable = [(n_, h_, p_) for n_, h_, p_ in listeners if h_ not in ("127.0.0.1", "localhost")]
    fails = [] if reachable else ["no listener can be reached from other pods"]
    fails += ["listener " + n_ + " (port " + p_ + ") can be reached from other pods but its protocol is " + protocol.get(n_, "not set") + ", not SSL" for n_, _, p_ in reachable if protocol.get(n_) != "SSL"]
    if value("KAFKA_SSL_CLIENT_AUTH") != "required":
        fails.append("KAFKA_SSL_CLIENT_AUTH is " + (value("KAFKA_SSL_CLIENT_AUTH") or "not set") + ": a client without a certificate would be accepted (it must be required)")
    for var in ("KAFKA_SSL_KEYSTORE_LOCATION", "KAFKA_SSL_TRUSTSTORE_LOCATION"):
        if secret_of_file(kafka, value(var)) != "kafka-tls":
            fails.append(var + "=" + value(var) + " is not a file of the Secret kafka-tls (mounted, and made by bootstrap.py)")
    judge(G, "Kafka: SSL with a client certificate on every listener that other pods can reach, keys from the Secret kafka-tls", fails)

    # The name that Kafka tells its clients to use must be in the certificate, or the client refuses the certificate.
    advertised = {a.split("://")[0]: a.split("://")[1].rsplit(":", 1)[0] for a in value("KAFKA_ADVERTISED_LISTENERS").split(",") if "://" in a}
    headless = kafka["spec"].get("serviceName", "")
    expected = "$(POD_NAME)." + headless + ".$(POD_NAMESPACE).svc.cluster.local"
    fails = ["listener " + n_ + " advertises " + advertised.get(n_, "nothing") + ", expected " + expected for n_, _, _ in reachable if advertised.get(n_) != expected]
    if "kafka-0." + headless + "." not in BOOTSTRAP_TEXT:
        fails.append("bootstrap.py does not put kafka-0." + headless + " in the certificate of Kafka: clients that Kafka sends there would refuse it")
    judge(G, "Kafka: the address it advertises (kafka-0 on the headless Service) is a name in its certificate", fails)

    # The tests use Kafka's command-line tools inside the pod, on the administration listener.
    lib_text = (ROOT / "scripts" / "ca3lib.py").read_text() if (ROOT / "scripts" / "ca3lib.py").is_file() else ""
    admin = re.search(r'^KAFKA_ADMIN\s*=\s*"([^"]+)"', lib_text, flags=re.M)
    loopback = {h_ + ":" + p_ for n_, h_, p_ in listeners if h_ in ("127.0.0.1", "localhost") and protocol.get(n_) == "PLAINTEXT"}
    judge(G, "Kafka: the administration address of the test scripts is a loopback listener of the broker (no other pod can reach it)",
          [] if admin and admin.group(1) in loopback else ["KAFKA_ADMIN in scripts/ca3lib.py (" + (admin.group(1) if admin else "missing") + ") is not a loopback listener of the broker (" + ", ".join(sorted(loopback)) + ")"])
if mongo is None:
    judge(G, "MongoDB StatefulSet exists", ["no StatefulSet named mongo"])
else:
    env = env_of(mongo)
    fails = [] if mongo["spec"].get("volumeClaimTemplates") else ["no volumeClaimTemplates: MongoDB would lose its data when the pod restarts"]
    for needed in ("MONGO_INITDB_ROOT_USERNAME_FILE", "MONGO_INITDB_ROOT_PASSWORD_FILE"):
        if needed not in env:
            fails.append(needed + " is missing: MongoDB would start without authentication, or with a password in the manifest")
        elif secret_of_file(mongo, env[needed].get("value", "")) != "mongo-root":
            fails.append(needed + " does not point into the Secret mongo-root")
    judge(G, "MongoDB: administrator password from a mounted file, and a PersistentVolumeClaim", fails)
    # The official image starts as root by default. Here it starts as the user "mongodb" (user ID 999, group ID 999) from the first
    # instruction, so the files of the Secrets must belong to group 999, and Kubernetes does that only through fsGroup.
    sc = pod(mongo).get("securityContext", {})
    fails = [] if sc.get("fsGroup") == 999 else ["fsGroup is " + str(sc.get("fsGroup")) + ": the mongodb user (999) could not read the password files and MongoDB would crash at start"]
    if sc.get("runAsUser") != 999:
        fails.append("runAsUser is " + str(sc.get("runAsUser")) + ": MongoDB's data folder belongs to the user 999")
    judge(G, "MongoDB: runs as the user 999 with group 999, so it can read its Secret files and write its data", fails)
    # TLS is mandatory: mongod refuses a client that does not use it.
    # MongoDB 7.0.6 and newer also refuse to start with TLS and no chain of trust (SERVER-72839: "The use of TLS without specifying a chain
    # of trust is no longer supported"), so the CA file is required. With a CA file mongod asks every client for a certificate, unless it is
    # told that none is needed: none of our clients has one (they log in with a password), so that option is required too.
    args = containers(mongo)[0].get("args", [])
    key_file = next((a.split("=", 1)[1] for a in args if a.startswith("--tlsCertificateKeyFile=")), "")
    ca_file = next((a.split("=", 1)[1] for a in args if a.startswith("--tlsCAFile=")), "")
    fails = [] if "--tlsMode=requireTLS" in args else ["mongod is not started with --tlsMode=requireTLS: it would also accept connections without TLS"]
    if secret_of_file(mongo, key_file) != "mongo-tls":
        fails.append("--tlsCertificateKeyFile=" + key_file + " is not a file of the Secret mongo-tls (mounted, and made by bootstrap.py)")
    if not ca_file:
        fails.append("mongod has no --tlsCAFile: MongoDB 7.0.6 and newer stop at once with 'The use of TLS without specifying a chain of trust is no longer supported'")
    elif secret_of_file(mongo, ca_file) != "mongo-tls" or not ca_file.endswith("/ca.pem"):
        fails.append("--tlsCAFile=" + ca_file + " is not the file ca.pem of the Secret mongo-tls (mounted, and made by bootstrap.py)")
    if "--tlsAllowConnectionsWithoutCertificates" not in args:
        fails.append("mongod has --tlsCAFile but not --tlsAllowConnectionsWithoutCertificates: it would demand a client certificate that the processor, the REST API and the exporter do not have")
    judge(G, "MongoDB: TLS is required (--tlsMode=requireTLS), with the certificate and the CA certificate of the Secret mongo-tls, and no client certificate needed", fails)
    # Four accounts: the administrator, the processor (read and write on ca3), the REST API (read only on ca3) and the metrics reader
    # (monitoring numbers only, no flows). Each line of the init script is:  create_user <folder of the Secret> '[ { role: "x", db: "y" }, ... ]'
    init = config_maps.get("mongo-init", {})
    created = {p_: re.findall(r'role:\s*"(\w+)",\s*db:\s*"(\w+)"', roles_) for p_, roles_ in re.findall(r"^\s*create_user (/\S+) '(.*)'\s*$", "\n".join(init.values()), flags=re.M)}
    wanted_accounts = {"/etc/secrets/mongo-processor": [("readWrite", "ca3")], "/etc/secrets/mongo-restapi": [("read", "ca3")],
                       "/etc/secrets/mongo-exporter": [("clusterMonitor", "admin"), ("read", "local")]}
    fails = [] if created == wanted_accounts else ["the init script creates " + str(created) + ", expected " + str(wanted_accounts)]
    init_dir = [vm for c in containers(mongo) for vm in c.get("volumeMounts", []) if vm["mountPath"] == "/docker-entrypoint-initdb.d"]
    init_vol = [v for v in pod(mongo).get("volumes", []) if init_dir and v["name"] == init_dir[0]["name"]]
    if not init_vol or init_vol[0].get("configMap", {}).get("name") != "mongo-init":
        fails.append("the ConfigMap mongo-init is not mounted at /docker-entrypoint-initdb.d, so the accounts are never created")
    for path in created:
        if secret_of_file(mongo, path + "/password") != path.rsplit("/", 1)[1] or secret_of_file(mongo, path + "/username") != path.rsplit("/", 1)[1]:
            fails.append(path + " is not a mounted Secret with a username and a password")
    judge(G, "MongoDB: the first start creates three more accounts: the processor (readWrite on ca3), the REST API (read on ca3) and the metrics reader (clusterMonitor)", fails)

# Each program uses its own account and its own Kafka certificate. Nobody but MongoDB itself mounts the administrator account.
fails = []
for wname, secret in (("processor", "mongo-processor"), ("restapi", "mongo-restapi")):
    w = by.get(("Deployment", wname))
    if w is None:
        fails.append("no Deployment named " + wname)
        continue
    path = env_of(w).get("MONGO_USER_FILE", {}).get("value", "")
    if secret_of_file(w, path) != secret:
        fails.append(wname + " reads its MongoDB account from " + (path or "nowhere") + ", expected the Secret " + secret)
for w in workloads:
    clients = {"processor": "kafka-client-processor", "producer-replay": "kafka-client-producer", "producer-load": "kafka-client-producer", "producer-smoke": "kafka-client-producer"}
    if name(w) in clients:
        cert = next((e.get("value", "") for c in containers(w) for e in c.get("env", []) if e["name"] == "KAFKA_SSL_CERT_FILE"), config_maps.get("pipeline-config", {}).get("KAFKA_SSL_CERT_FILE", ""))
        if secret_of_file(w, cert) != clients[name(w)]:
            fails.append(name(w) + " shows the Kafka certificate " + cert + ", which is not a file of the Secret " + clients[name(w)])
judge(G, "the processor and the REST API have different MongoDB accounts; every Kafka client mounts its own certificate", fails)

# Who may mount which Secret (the least each program needs). A Secret that nobody mounts would be dead weight, and a program that mounts a
# Secret that it does not need could read what it should not.
ALLOWED = {
    "mongo-root": {"mongo"},
    "mongo-processor": {"mongo", "processor"},
    "mongo-restapi": {"mongo", "restapi"},
    "restapi-login": {"restapi"},
    "restapi-tls": {"restapi"},
    "pki-ca": {"processor", "restapi"},
    "kafka-tls": {"kafka"},
    "kafka-client-producer": {"producer-replay", "producer-load", "producer-smoke"},
    "kafka-client-processor": {"processor"},
    "kafka-client-exporter": {"kafka-exporter"},
    "mongo-exporter": {"mongo", "mongodb-exporter"},
    "grafana-admin": {"grafana"},
    "mongo-tls": {"mongo"},
}
# The pods that mount these Secrets belong to the observability part of the stack. Until their Deployments exist, a Secret that nobody
# mounts yet is reported as "still to do" and not as an error.
LATER_MOUNTERS = {"kafka-client-exporter": "kafka-exporter", "grafana-admin": "grafana"}
mounted_by = {}
for w in workloads:
    for mount, (secret, _) in secret_mounts(w).items():
        mounted_by.setdefault(secret, set()).add(name(w))
fails = []
for secret, who in sorted(mounted_by.items()):
    extra = who - ALLOWED.get(secret, set())
    if extra:
        fails.append("the Secret " + secret + " is mounted by " + ", ".join(sorted(extra)) + ", which does not need it")
pending = sorted(s_ for s_, w_ in LATER_MOUNTERS.items() if s_ in SECRETS and s_ not in mounted_by and ("Deployment", w_) not in by)
fails += ["bootstrap.py makes the Secret " + s_ + " but no pod mounts it" for s_ in sorted(set(SECRETS) - set(mounted_by) - set(pending))]
fails += ["bootstrap.py does not make the Secret " + s_ + " that this check expects" for s_ in sorted(set(ALLOWED) - set(SECRETS))] if SECRETS else ["bootstrap.py makes no Secret (or the program cannot be read)"]
judge(G, "each of the " + str(len(SECRETS)) + " Secrets is mounted only by the pods that need it (the administrator account only by MongoDB)", fails)
if pending:
    todo(G, "the Secrets of the observability pods are mounted: " + ", ".join(pending), "the pods that read them are added at Step 4")

# Kubernetes fills in four values in every disk template. If the manifest leaves them out, "kubectl apply" sees a difference on every run,
# prints "configured", and a second "make up" looks as if it changed something.
fails = []
for ss in of("StatefulSet"):
    for disk in ss["spec"].get("volumeClaimTemplates", []):
        complete = disk.get("apiVersion") == "v1" and disk.get("kind") == "PersistentVolumeClaim" and disk.get("spec", {}).get("volumeMode") == "Filesystem" and disk.get("status", {}).get("phase") == "Pending"
        if not complete:
            fails.append(name(ss) + ": disk template " + disk["metadata"]["name"] + " must spell out apiVersion, kind, volumeMode and status")
judge(G, "StatefulSet disk templates spell out the values Kubernetes fills in, so a second apply says unchanged", fails)

# ------------------------------------------------------------ 4. the programs get everything they require
G = "4. Each program gets everything it requires"
PROGRAMS = {"processor/processor.py": ["processor"], "producer/producer.py": ["producer-replay", "producer-load", "producer-smoke"], "restapi/rest_app.py": ["restapi"]}
# The REST API reads its certificate and key with os.environ.get, because a run on a laptop may leave them out (it then speaks plain HTTP). The container must not.
ALSO_REQUIRED = {"restapi/rest_app.py": ["REST_TLS_CERT_FILE", "REST_TLS_KEY_FILE"]}
for program, names in PROGRAMS.items():
    if not (ROOT / program).exists():
        judge(G, program, [program + " does not exist"])
        continue
    text = (ROOT / program).read_text()
    required, required_secrets = re.findall(r'require\("([A-Z_]+)"\)', text), re.findall(r'require_secret\("([A-Z_]+)"\)', text)
    required += ALSO_REQUIRED.get(program, [])
    for wname in names:
        w = by.get(("Deployment", wname)) or by.get(("Job", wname))
        if w is None:
            judge(G, program + " in " + wname, ["no Deployment or Job named " + wname])
            continue
        provided, settings = set(), {}
        for c in containers(w):
            for ef in c.get("envFrom", []):
                data = config_maps.get(ef.get("configMapRef", {}).get("name"), {})
                provided |= set(data)
                settings.update(data)
            for e in c.get("env", []):
                provided.add(e["name"])
                settings[e["name"]] = e.get("value", "")
        fails = ["requires " + r + ", but nothing provides it" for r in required if r not in provided]
        for s_ in required_secrets:
            f = settings.get(s_ + "_FILE")
            if not f:
                fails.append("requires " + s_ + ", but " + s_ + "_FILE is not set")
            elif secret_of_file(w, f) is None:
                fails.append(s_ + "_FILE=" + f + " is not a file of a Secret that this pod mounts and that bootstrap.py makes")
        # Every other setting that names a file (the certificates and keys) must also lead to a file of a mounted Secret.
        for r in required:
            if r.endswith("_FILE") and r != "DATASET_FILE" and r in settings and r not in [s_ + "_FILE" for s_ in required_secrets] and secret_of_file(w, settings[r]) is None:
                fails.append(r + "=" + settings[r] + " is not a file of a Secret that this pod mounts and that bootstrap.py makes")
        judge(G, program + " in " + wname + ": " + str(len(required)) + " settings + " + str(len(required_secrets)) + " secret files", fails)

# The setup Job runs bootstrap.py, which is part of the processor image.
boot = by.get(("Job", "bootstrap"))
fails = []
if boot is None:
    fails.append("no Job named bootstrap: nothing creates the Secrets, so the pods would wait for them for ever")
else:
    c = containers(boot)[0]
    if "/ca3-processor:" not in c["image"]:
        fails.append("the Job uses " + c["image"] + ", not the processor image that contains bootstrap.py")
    if "bootstrap.py" not in " ".join(c.get("command", []) + c.get("args", [])):
        fails.append("the Job does not run bootstrap.py")
    if "wait-for" in " ".join(i["name"] for i in pod(boot).get("initContainers", [])):
        fails.append("the Job waits for another pod, but it has to run before all of them")
    if not BOOTSTRAP.is_file():
        fails.append("processor/bootstrap.py does not exist")
    docker = (ROOT / "processor" / "Dockerfile").read_text() if (ROOT / "processor" / "Dockerfile").is_file() else ""
    if not re.search(r"^COPY\s+.*\bbootstrap\.py\b", docker, flags=re.M):
        fails.append("processor/Dockerfile does not copy bootstrap.py into the image")
    if "cryptography" not in (ROOT / "processor" / "requirements.txt").read_text():
        fails.append("processor/requirements.txt has no cryptography package, which bootstrap.py needs for the certificates")
judge(G, "the Job bootstrap runs bootstrap.py from the processor image, and the image contains it", fails)

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
if not r.isdigit() or container_port("restapi", "https") != int(r) or int(r) not in svc_ports("restapi"):
    fails.append("REST_PORT=" + r + " must equal the REST API's containerPort (named https) and the restapi Service port")
for key_, wname_ in (("PRODUCER_METRICS_PORT", "producer-load"), ("REST_METRICS_PORT", "restapi")):
    v_ = merged.get(key_, "")
    if not v_.isdigit() or container_port(wname_, "metrics") != int(v_):
        fails.append(key_ + "=" + v_ + " must equal the containerPort named metrics of " + wname_)
if not (ROOT / "producer" / merged.get("DATASET_FILE", "-")).is_file():
    fails.append("DATASET_FILE=" + merged.get("DATASET_FILE", "") + " is not in the producer/ folder, so the image cannot contain it")
if merged.get("MONGO_DB") != "ca3":
    fails.append("MONGO_DB=" + str(merged.get("MONGO_DB")) + ": use ca3 (an old value from an earlier assignment would be confusing in the README and logs)")
judge(G, "ConfigMap values match the Services, ports, dataset and database name", fails)

# The REST API speaks HTTPS and nothing else, so its probes must too (a plain HTTP probe would fail and the pod would never be ready).
rest = by.get(("Deployment", "restapi"))
fails = []
for c in containers(rest) if rest else []:
    for probe in ("readinessProbe", "livenessProbe"):
        if c.get(probe, {}).get("httpGet", {}).get("scheme") != "HTTPS":
            fails.append(c["name"] + ": " + probe + " must be an HTTPS probe")
judge(G, "the REST API's readiness and liveness probes use HTTPS", fails if rest else ["no Deployment named restapi"])

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
    images = [c["image"] for w in workloads for c in containers(w) if "/ca3-" + comp + ":" in c["image"]]
    if not images:
        fails.append("no manifest uses an image named ca3-" + comp)
    fails += [i + " does not end with the version in CA3/VERSION (" + str(version) + ")" for i in images if version is None or not i.endswith(":" + version)]
    judge(G, comp + ": Dockerfile, pinned requirements, image tag equals VERSION", fails)
workflow = ROOT.parent / ".github" / "workflows" / "ca3-images.yml"
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
    # The setup Job is the only pod with a Kubernetes API token. With default-deny it can reach the API server and nothing else.
    try:
        subnet = (yaml.safe_load((ROOT / "infra" / "config.yml").read_text()) or {}).get("subnet_cidr")
    except (OSError, yaml.YAMLError):
        subnet = None
    boot_policy = by.get(("NetworkPolicy", "bootstrap-egress"))
    fails = []
    if boot_policy is None:
        fails.append("no NetworkPolicy bootstrap-egress: with default-deny the setup Job could not reach the API server")
    else:
        egress = boot_policy["spec"].get("egress", [])
        blocks = [t_["ipBlock"]["cidr"] for r_ in egress for t_ in r_.get("to", []) if "ipBlock" in t_]
        others = [t_ for r_ in egress for t_ in r_.get("to", []) if "ipBlock" not in t_]
        ports_ = {p_["port"] for r_ in egress for p_ in r_.get("ports", [])}
        if 6443 not in ports_:
            fails.append("port 6443 (the API server) is not allowed")
        if subnet and subnet not in blocks:
            fails.append("the node subnet " + subnet + " (infra/config.yml) is not a destination: the API server runs on a node of it")
        if others or not blocks:
            fails.append("the destinations must be address blocks only (the API server is not a pod); found other kinds or none")
        if boot_policy["spec"].get("policyTypes") != ["Egress"] or boot_policy["spec"]["podSelector"].get("matchLabels") is None:
            fails.append("the policy must be an egress policy with a pod selector")
        elif any(matches(boot_policy["spec"]["podSelector"]["matchLabels"], w) for w in workloads if name(w) != "bootstrap"):
            fails.append("the selector also matches a pod other than the setup Job")
    judge(G, "bootstrap-egress: the setup Job may reach the API server (node subnet, port 6443) and nothing else", fails)
    deny = any(p["spec"].get("podSelector") == {} and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"} and not p["spec"].get("ingress") and not p["spec"].get("egress") for p in policies)
    judge(G, "default-deny-all: no pod accepts or sends traffic unless a policy allows it", [] if deny else ["no policy with an empty podSelector, both policy types and no rules"])
roles = of("Role")
if not roles:
    todo(G, "RBAC Roles and RoleBindings", "added in the security step")
else:
    # Only the Role of the setup Job may mention Secrets at all, and it may only create them: no Role can read, change or delete a password or a key.
    fails = []
    for r_ in roles:
        for rule in r_.get("rules", []):
            if "secrets" in rule.get("resources", []) or "*" in rule.get("resources", []):
                if name(r_) != "bootstrap":
                    fails.append(name(r_) + " allows access to secrets (only the setup Job's Role may mention them)")
                elif set(rule.get("verbs", [])) != {"create"}:
                    fails.append("Role bootstrap allows " + "/".join(rule.get("verbs", [])) + " on secrets; it needs only create")
    if ("Role", "bootstrap") not in by:
        fails.append("no Role named bootstrap: the setup Job could not create the Secrets")
    for b in of("RoleBinding"):
        if ("Role", b["roleRef"]["name"]) not in by:
            fails.append(name(b) + " refers to Role " + b["roleRef"]["name"] + ", which is not defined")
        fails += [name(b) + " gives a Role to ServiceAccount " + s_["name"] + ", which is not defined" for s_ in b.get("subjects", []) if s_["name"] not in service_accounts]
        if b["roleRef"]["name"] == "bootstrap" and [s_["name"] for s_ in b.get("subjects", [])] != ["bootstrap"]:
            fails.append("the Role bootstrap must be given to the ServiceAccount bootstrap only")
    judge(G, str(len(roles)) + " Roles, " + str(len(of("RoleBinding"))) + " RoleBindings: only the setup Job may touch Secrets (create only), all bindings resolve", fails)
# The files of a Secret volume belong to root. A program that does not run as root can read them only through the group (fsGroup),
# so every pod that mounts a Secret needs an fsGroup, and the files need the group-read permission but not the world-read one.
fails = []
for w in workloads:
    for v in pod(w).get("volumes", []):
        if "secret" in v:
            mode, group = v["secret"].get("defaultMode", 0o644), pod(w).get("securityContext", {}).get("fsGroup")
            if not isinstance(group, int):
                fails.append(name(w) + ": volume " + v["name"] + " belongs to root and the pod sets no fsGroup, so a program that does not run as root cannot read it")
            if not mode & 0o040:
                fails.append(name(w) + ": volume " + v["name"] + " has mode " + oct(mode) + ", which the group cannot read")
            if mode & 0o007:
                fails.append(name(w) + ": volume " + v["name"] + " has mode " + oct(mode) + ", which every user on the machine can read")
judge(G, "every Secret volume can be read by the pod's group and by nobody else", fails)
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


# The scaling test switches the producers' load on and off with three Kustomize folders. They must not differ from each other in anything
# but what they claim: the stack and producer/load hold the same idle load generator; producer/loadtest sends (PRODUCER_IDLE=false);
# producer/loadtest-one sends and also holds the autoscaler at one pod (the "before" measurement).
def load_view(objs):
    deployment = next((d for d in objs if kind(d) == "Deployment" and name(d) == "producer-load"), None)
    hpa = next((d for d in objs if kind(d) == "HorizontalPodAutoscaler" and name(d) == "producer-load"), None)
    if deployment is None or hpa is None:
        return None
    idle = {e["name"]: e.get("value") for e in containers(deployment)[0].get("env", [])}.get("PRODUCER_IDLE")
    return idle, hpa["spec"]["maxReplicas"], deployment, hpa


def without_switches(objs):
    out = copy.deepcopy(sorted(objs, key=lambda d: (kind(d), name(d))))
    for d in out:
        if kind(d) == "Deployment":
            # A patch that changes one variable also moves it to the front of the list; the order of the variables does not matter.
            for e in containers(d)[0].get("env", []):
                if e["name"] == "PRODUCER_IDLE":
                    e["value"] = "-"
            containers(d)[0]["env"] = sorted(containers(d)[0].get("env", []), key=lambda e: e["name"])
        if kind(d) == "HorizontalPodAutoscaler":
            d["spec"]["maxReplicas"] = 0
    return out


fails, views = [], {n_: load_view(o) for n_, o in OVERLAYS.items()}
for n_, v in views.items():
    if v is None:
        fails.append("producer/" + n_ + " does not hold both the Deployment and the HorizontalPodAutoscaler producer-load")
if not fails:
    stack = load_view(docs)
    if stack is None or stack[0] != "true" or stack[2] != views["load"][2] or stack[3] != views["load"][3]:
        fails.append("the stack (kubectl apply -k .) does not contain producer/load unchanged: an idle load generator (PRODUCER_IDLE=true)")
    if (views["loadtest"][0], views["loadtest"][1]) != ("false", 5):
        fails.append("producer/loadtest must send (PRODUCER_IDLE=false) with the autoscaler at 1 to 5, it has " + str(views["loadtest"][:2]))
    if (views["loadtest-one"][0], views["loadtest-one"][1]) != ("false", 1):
        fails.append("producer/loadtest-one must send (PRODUCER_IDLE=false) with the autoscaler held at 1, it has " + str(views["loadtest-one"][:2]))
    for n_ in ("loadtest", "loadtest-one"):
        if without_switches(OVERLAYS[n_]) != without_switches(OVERLAYS["load"]):
            fails.append("producer/" + n_ + " changes more than PRODUCER_IDLE and the autoscaler's maximum")
judge(G, "the scaling test's three folders (load, loadtest, loadtest-one) differ only in PRODUCER_IDLE and the autoscaler's maximum; the stack holds the idle one", fails)

# ------------------------------------------------------------ 7. the cluster is built by code
G = "7. Cluster as code (Terraform and Ansible)"
INFRA = ROOT / "infra"
infra_ok = False
if not INFRA.is_dir():
    todo(G, "Terraform and Ansible build a 3-node Kubernetes cluster", "added in the cluster step")
else:
    needed = ["config.yml", "terraform/versions.tf", "terraform/config.tf", "terraform/network.tf", "terraform/security.tf",
              "terraform/compute.tf", "terraform/outputs.tf", "terraform/inventory.tpl", "ansible/ansible.cfg", "ansible/site.yml",
              "ansible/roles/k3s_server/tasks/main.yml", "ansible/roles/k3s_server/templates/config.yaml.j2",
              "ansible/roles/k3s_agent/tasks/main.yml", "ansible/roles/k3s_agent/templates/config.yaml.j2"]
    missing = ["infra/" + n for n in needed if not (INFRA / n).is_file()]
    judge(G, "Terraform (network, security, compute) and Ansible (server and agent roles) are all present", ["missing " + m for m in missing])
    try:
        icfg = yaml.safe_load((INFRA / "config.yml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        icfg = {}
    fails = [k + " is missing from infra/config.yml" for k in ("region", "vpc_cidr", "subnet_cidr", "instance_type", "project_tag", "admin_cidr", "k3s_version") if not icfg.get(k)]
    if int(icfg.get("agent_count", 0) or 0) < 2:
        fails.append("agent_count is " + str(icfg.get("agent_count")) + ": the assignment needs at least 3 nodes (1 server and 2 agents)")
    node_ports = [p.get("nodePort") for p in services.get("restapi", {}).get("spec", {}).get("ports", [])]
    if icfg.get("rest_node_port") not in node_ports:
        fails.append("rest_node_port " + str(icfg.get("rest_node_port")) + " is not the restapi Service nodePort " + str(node_ports) + ": the firewall would block the REST API")
    if not re.fullmatch(r"v\d+\.\d+\.\d+\+k3s\d+", str(icfg.get("k3s_version", ""))):
        fails.append("k3s_version must be a pinned release such as v1.37.1+k3s1")
    if not re.fullmatch(r"[0-9a-f]{64}", str(icfg.get("k3s_installer_sha256", ""))):
        fails.append("k3s_installer_sha256 must be the 64-character checksum of the pinned installer")
    judge(G, "infra/config.yml: 3 nodes, REST node port equals the Service, k3s release and installer checksum pinned", fails)
    stray = []
    code_files = sorted((INFRA / "terraform").glob("*.tf")) + [INFRA / "terraform" / "inventory.tpl"] + sorted((INFRA / "ansible").rglob("*.yml")) + sorted((INFRA / "ansible").rglob("*.j2"))
    for f in code_files:
        if f.is_file():
            stray += [str(f.relative_to(ROOT)) + ": " + ip for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", f.read_text()) if ip not in ("0.0.0.0", "127.0.0.1")]
    judge(G, "no IP address is written into the Terraform or Ansible code (everything comes from config.yml or AWS)", stray)
    ignored = {line.strip() for line in ((ROOT / ".gitignore").read_text().splitlines() if (ROOT / ".gitignore").exists() else [])}
    judge(G, "keys, Terraform state and the generated inventory cannot be committed (.gitignore)", [x + " is not a line in .gitignore" for x in (".secrets/", "infra/terraform/*.tfstate", "infra/ansible/inventory.ini") if x not in ignored])
    infra_ok = all(row[0] == "PASS" for row in rows if row[1] == G)
    # The cleanup scan finds resources by their tag. If it looked for a tag that Terraform never sets, it would say CLEAN while things are left.
    scan_file, tf_dir = ROOT / "scripts" / "cleanup-scan.py", INFRA / "terraform"
    scan_key = re.search(r'^TAG_KEY\s*=\s*"(\w+)"', scan_file.read_text() if scan_file.is_file() else "", flags=re.M)
    config_tf = (tf_dir / "config.tf").read_text() if (tf_dir / "config.tf").is_file() else ""
    versions_tf = (tf_dir / "versions.tf").read_text() if (tf_dir / "versions.tf").is_file() else ""
    tf_tags = re.search(r"\btags\s*=\s*\{(.*?)\}", config_tf, flags=re.S)
    fails = []
    if not scan_file.is_file():
        fails.append("scripts/cleanup-scan.py is missing")
    elif not scan_key:
        fails.append("scripts/cleanup-scan.py has no TAG_KEY line")
    elif not tf_tags or not re.search(r"^\s*" + scan_key.group(1) + r"\s*=\s*local\.config\.project_tag\s*$", tf_tags.group(1), flags=re.M):
        fails.append("the scan looks for the tag " + scan_key.group(1) + ", but Terraform does not set that tag from project_tag, so the scan could say CLEAN while things are left")
    if not re.search(r"default_tags\s*\{\s*tags\s*=\s*local\.tags", versions_tf):
        fails.append("versions.tf does not put local.tags on every resource (default_tags), so the scan could not find them")
    judge(G, "the cleanup scan looks for the tag that Terraform puts on every resource (it cannot say CLEAN by looking for the wrong tag)", fails)

# ------------------------------------------------------------ 8. progress against the CA3 assignment
G = "8. The CA3 assignment, requirement by requirement"
makefile = (ROOT / "Makefile").read_text() if (ROOT / "Makefile").exists() else ""
has = lambda k, n: (k, n) in by
checks = [
    ("Kafka: StatefulSet + PVC + Service (KRaft)", has("StatefulSet", "kafka") and bool(kafka and kafka["spec"].get("volumeClaimTemplates")) and has("Service", "kafka"), False),
    ("Database: StatefulSet + PVC + Service", has("StatefulSet", "mongo") and bool(mongo and mongo["spec"].get("volumeClaimTemplates")) and has("Service", "mongo"), False),
    ("Processor: Deployment + ConfigMap + Secret + ClusterIP Service", has("Deployment", "processor") and has("ConfigMap", "processor-config") and services.get("processor", {}).get("spec", {}).get("type", "ClusterIP") == "ClusterIP", False),
    ("Producer: Job for a one-off replay", has("Job", "producer-replay"), False),
    ("ConfigMaps for settings, and Secrets for credentials and certificates (made inside the cluster by the setup Job, no script outside it)", len(config_maps) >= 2 and bool(SECRETS) and has("Job", "bootstrap"), False),
    ("A bare kubectl apply -k . is enough: no script has to make anything first, and the Makefile does not call one",
     has("Job", "bootstrap") and not (ROOT / "scripts" / "create-secrets.sh").exists() and "create-secrets" not in makefile, False),
    ("Hardening: TLS on Kafka (client certificates), MongoDB and the REST API; a login on the REST API; one MongoDB account per program; each Secret only where it is needed; Pod Security restricted enforced",
     all(r_[0] != "FAIL" for r_ in rows if r_[1][0] in "136" and not r_[2].startswith(("HPA ", "the scaling test")))
     and by.get(("Namespace", "ca3"), {"metadata": {"labels": {}}})["metadata"].get("labels", {}).get("pod-security.kubernetes.io/enforce") == "restricted", False),
    ("A REST endpoint declared and exposed", services.get("restapi", {}).get("spec", {}).get("type") in ("NodePort", "LoadBalancer"), False),
    ("Secrets mounted, not embedded in manifests", all(not (re.search(r"PASSWORD|SECRET|TOKEN", e["name"]) and "value" in e and not e["name"].endswith("_FILE")) for w in workloads for c in containers(w) for e in c.get("env", [])), False),
    ("Only the necessary ports are exposed", published == ["restapi"], False),
    ("Tests: make smoke, make netpol-check and make heal-check exist (the smoke Job stays out of make up)",
     has("Job", "producer-smoke") and ("Job", "producer-smoke") not in {(kind(d), name(d)) for d in docs}
     and all(re.search(r"^" + t + r":", makefile, flags=re.M) for t in ("smoke", "netpol-check", "heal-check"))
     and all((ROOT / "scripts" / f).is_file() for f in ("smoke-test.py", "netpol-check.py", "heal-check.py", "ca3lib.py")), False),
    ("Scaling test: the producers' autoscaler and the load Deployment are part of make up (idle); make scale-test switches the load on and off with the overlays and measures before, during, after and the shrink",
     ("Deployment", "producer-load") in {(kind(d), name(d)) for d in docs}
     and ("HorizontalPodAutoscaler", "producer-load") in {(kind(d), name(d)) for d in docs}
     and all((ROOT / "producer" / f / "kustomization.yaml").is_file() for f in ("load", "loadtest", "loadtest-one"))
     and all(re.search(r"^" + t + r":", makefile, flags=re.M) for t in ("scale-test", "scale-report"))
     and all((ROOT / "scripts" / f).is_file() for f in ("scale-test.py", "scalereport.py")), True),
    ("One command to apply the stack and one to delete it (make up / make down)", bool(re.search(r"^up:", makefile, flags=re.M)) and bool(re.search(r"^down:", makefile, flags=re.M)), False),
    ("Tip of the assignment: every object has the label app.kubernetes.io/part-of=ca3-pipeline and a description annotation, and so does every Secret that the setup Job makes (group and filter with kubectl -l)",
     all(d["metadata"].get("labels", {}).get("app.kubernetes.io/part-of") == "ca3-pipeline" and len(d["metadata"].get("annotations", {}).get("description", "")) >= 20 for d in objects)
     and '"app.kubernetes.io/part-of": "ca3-pipeline"' in BOOTSTRAP_TEXT and re.search(r'"annotations":\s*\{"description":\s*description\}', BOOTSTRAP_TEXT) is not None, False),
    ("Cleanup: make teardown deletes the stack, destroys the cluster and then scans AWS for leftovers (make scan)",
     all(re.search(r"^" + t + r":", makefile, flags=re.M) for t in ("scan", "teardown")) and (ROOT / "scripts" / "cleanup-scan.py").is_file(), False),
    ("Dockerfiles and a registry push for the three custom images", all((ROOT / c / "Dockerfile").exists() for c in ("producer", "processor", "restapi")) and workflow.exists(), False),
    ("Metrics: the producer, the processor and the REST API each serve /metrics with the numbers the assignment names (producer rate, database inserts, requests, errors, latency)",
     all(all(m_ in (ROOT / f_).read_text() for m_ in ms_) for f_, ms_ in {
         "producer/producer.py": ("producer_messages_sent_total", "producer_send_errors_total", "start_http_server"),
         "processor/processor.py": ("processor_messages_total", "processor_insert_batch_seconds", "processor_end_to_end_seconds", "generate_latest"),
         "restapi/rest_app.py": ("restapi_requests_total", "restapi_request_duration_seconds", "start_http_server")}.items() if (ROOT / f_).is_file())
     and all((ROOT / f_).is_file() for f_ in ("producer/producer.py", "processor/processor.py", "restapi/rest_app.py")), False),
    ("Structured logs: every program writes one JSON object per line with ts, level, service and msg",
     all(all(m_ in (ROOT / f_).read_text() for m_ in ("def log(", "json.dumps", '"service"', '"ts"', '"level"', '"msg"'))
         for f_ in ("producer/producer.py", "processor/processor.py", "restapi/rest_app.py")), False),
    ("The consumer group is called processor (the assignment asks for kafka_consumergroup_lag{group=\"processor\"})",
     config_maps.get("processor-config", {}).get("CONSUMER_GROUP") == "processor", False),
    ("Observability in the stack: Prometheus, Grafana, Loki, Alloy (a DaemonSet), kafka-exporter and mongodb-exporter are part of make up",
     all(has("Deployment", n_) for n_ in ("prometheus", "grafana", "loki", "kafka-exporter", "mongodb-exporter")) and has("DaemonSet", "alloy"), True),
    ("Resilience drill: a script deletes a pod in every tier (make drill) and the operator steps are written down",
     (ROOT / "scripts" / "drill.py").is_file() and bool(re.search(r"^drill:", makefile, flags=re.M)), True),
    ("A 3-node Kubernetes cluster is built by code (Terraform and Ansible)", infra_ok, not INFRA.is_dir()),
    ("NetworkPolicy restricting traffic between services", bool(policies), True),
    ("RBAC: a Role and a RoleBinding", bool(roles) and bool(of("RoleBinding")), True),
    ("HorizontalPodAutoscaler for the producers (1 to N)", any(h["spec"]["scaleTargetRef"]["name"].startswith("producer") for h in hpas), True),
    ("HorizontalPodAutoscaler for the processor (optional elasticity)", any(h["spec"]["scaleTargetRef"]["name"] == "processor" for h in hpas), True),
]
for label, passed, later in checks:
    rows.append(("PASS" if passed else ("TODO" if later else "FAIL"), G, label, []))

# ------------------------------------------------------------ 9. the documents agree with the files and the evidence
# The README, the Integrity Packet and the AI log make claims about the files and the saved evidence. These checks read the documents
# as data and compare every claim that a program can compare. They cannot tell whether a sentence is well written or true in spirit.
G = "9. The documents agree with the files and the evidence (README, Integrity Packet, AI log)"
DOC_NAMES = ("README.md", "integritypacket.md", "ai-guidance-log.md")
SHOT = "images/kubectl-get-all.png"


UNREADABLE = []   # a file that cannot be read must never count as "nothing found" (it is reported by the privacy checks)


def read_text(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        UNREADABLE.append(str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) else str(p))
        return ""


def no_fences(t): return re.sub(r"```.*?```", "", t, flags=re.S)
def no_code(t): return re.sub(r"`[^`\n]*`", "", no_fences(t))
def cap(items, n=8): return list(items[:n]) + (["... and " + str(len(items) - n) + " more"] if len(items) > n else [])


# What Git would publish: every file that no line of .gitignore (here or one folder up) matches.
IGNORE = [l.strip() for f in (ROOT / ".gitignore", ROOT.parent / ".gitignore") for l in read_text(f).splitlines() if l.strip() and l.strip()[0] not in "#!"]


def ignored(rel, is_dir=False):
    parts = rel.split("/")
    for pat in IGNORE:
        only_dir, p = pat.endswith("/"), pat.strip("/")
        if "/" in p:
            if any(fnmatch.fnmatch("/".join(parts[:i]), p) and (not only_dir or i < len(parts) or is_dir) for i in range(1, len(parts) + 1)):
                return True
        elif any(fnmatch.fnmatch(part, p) and (not only_dir or i < len(parts) - 1 or is_dir) for i, part in enumerate(parts)):
            return True
    return False


published = []
for dirpath, dirnames, filenames in os.walk(str(ROOT), onerror=lambda e: UNREADABLE.append(str(e.filename))):
    here = Path(dirpath).relative_to(ROOT).as_posix()
    here = "" if here == "." else here + "/"
    dirnames[:] = sorted(d for d in dirnames if d != ".git" and not ignored(here + d, True))
    published += [here + f for f in sorted(filenames) if not ignored(here + f)]

doc = {n: read_text(ROOT / n) for n in DOC_NAMES}
readme, packet, ailog = doc["README.md"], doc["integritypacket.md"], doc["ai-guidance-log.md"]
top = {p.name for p in ROOT.iterdir()}

# Until the first of the three documents exists (they are written at Step 9) the checks about them are "still to do", not errors.
# From then on every check is strict. The two privacy checks at the end are strict from the start.
docs_started = any((ROOT / n).is_file() for n in DOC_NAMES)


def judge_doc(group, label, fails, warns=()):
    if docs_started:
        judge(group, label, fails, warns)
    else:
        todo(group, label, "the documents are written at Step 9")

# 9.1 the three documents and the screenshot
fails, note = ["missing " + n for n in DOC_NAMES if not (ROOT / n).is_file()], ""
if not (ROOT / SHOT).is_file():
    fails.append(SHOT + " is missing (the assignment asks for a screenshot of kubectl get all -A)")
else:
    data = (ROOT / SHOT).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        fails.append(SHOT + " is not a PNG image")
    else:
        width, height = struct.unpack(">II", data[16:24])
        note = " (" + str(width) + " x " + str(height) + " pixels, " + str(len(data) // 1024) + " KB)"
        if width < 600 or height < 300:
            fails.append(SHOT + " is only " + str(width) + " x " + str(height) + " pixels, too small to read")
        if len(data) > 10 * 1024 * 1024:
            fails.append(SHOT + " is larger than 10 MB")
    if not re.search(r"!\[[^\]]*\]\(" + re.escape(SHOT) + r"\)", readme):
        fails.append("the README does not show " + SHOT)
judge_doc(G, "README.md, integritypacket.md and ai-guidance-log.md exist; " + SHOT + " is a PNG image and the README shows it" + note, fails)

# 9.2 nothing unfinished
fails = []
for n in DOC_NAMES:
    if "\t" in doc[n]:
        fails.append(n + ": contains a TAB character")
    fails += [n + ": unfinished text " + m for m in sorted(set(re.findall(r"\{\{[^}]*\}\}|\bTODO\b|\bTBD\b|\bFIXME\b", doc[n])))]
judge_doc(G, "no unfilled placeholder ({{ }}, TODO, TBD, FIXME) and no TAB character in the three documents", fails)

# 9.3 the sections that the assignment and the packet guidance ask for
wanted = {
    "README.md": ("prerequisites", "how to deploy", "how to destroy", "scaling test", "where to find logs", "deviations from ca0", "known limitations", "smoke test", "security", "integrity packet"),
    "integritypacket.md": ("outcome", "claims", "assumptions", "failures found and fixed", "escalation", "risks that remain", "ai-assisted work", "ownership", "evidence index"),
    "ai-guidance-log.md": ("decisions the ai recommended", "where the ai was wrong"),
}
fails = []
for n, words in wanted.items():
    heads = [h.lower() for h in re.findall(r"^#{1,6}[ ]+(.+?)[ #]*$", no_fences(doc[n]), flags=re.M)]
    fails += [n + ": no heading with '" + w + "'" for w in words if not any(w in h for h in heads)]
judge_doc(G, "the README, the packet and the AI log have the sections that were asked for (" + str(sum(len(v) for v in wanted.values())) + " headings)", fails)


# 9.4 every link points to something inside this folder
def slugs(t):
    seen, found = {}, set()
    for h in re.findall(r"^#{1,6}[ ]+(.+?)[ #]*$", no_fences(t), flags=re.M):
        s = re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
        seen[s] = seen.get(s, 0) + 1
        found.add(s if seen[s] == 1 else s + "-" + str(seen[s] - 1))
    return found


fails, links = [], 0
for n in DOC_NAMES:
    for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", no_code(doc[n])):
        links += 1
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, frag = urllib.parse.unquote(target).partition("#")
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", path):
            fails.append(n + ": " + target + " is not a link inside this folder")
        elif path.startswith("/") or ".." in path.split("/"):
            fails.append(n + ": " + target + " leaves the CA3 folder, so it breaks when the folder is downloaded alone")
        elif path and not (ROOT / path).exists():
            fails.append(n + ": " + target + " does not exist")
        elif frag and (not path or path.endswith(".md")) and frag.lower() not in slugs(doc[n] if not path else read_text(ROOT / path)):
            fails.append(n + ": " + target + " points to a heading that does not exist")
judge_doc(G, "every link points to a file or folder inside CA3 that exists (" + str(links) + " links)", cap(fails))

# 9.5 every file name in backticks exists (or is a file that .gitignore keeps out of Git)
fails, names = [], 0
for n in DOC_NAMES:
    for span in sorted(set(re.findall(r"`([^`\n]+)`", no_fences(doc[n])))):
        span = span.rstrip(".,:;")
        if not re.fullmatch(r"[\w.*/-]+", span):
            continue
        if "/" in span and span.split("/")[0] in top:
            names += 1
            rel = span.rstrip("/")
            if ignored(rel, span.endswith("/")):
                continue
            found = list(ROOT.glob(rel)) if "*" in rel else ([ROOT / rel] if (ROOT / rel).exists() else [])
            if not found:
                fails.append(n + ": `" + span + "` does not exist")
        elif "/" not in span and re.fullmatch(r"[\w.-]+\.(md|yaml|yml|py|sh|tf|tpl|j2|txt|csv|json|svg|png)|Makefile|VERSION|Dockerfile", span):
            names += 1
            if span not in top and not any(p.rsplit("/", 1)[-1] == span for p in published) and not ignored(span):
                fails.append(n + ": `" + span + "` is not a file of this repository")
judge_doc(G, "every file name written in backticks exists (" + str(names) + " names)", cap(fails))

# 9.6 every "make X" written in the documents is a target of the Makefile
mk_text = (ROOT / "Makefile").read_text() if (ROOT / "Makefile").exists() else ""
mk_targets = set(re.findall(r"^([a-z][a-z0-9-]*):", mk_text, flags=re.M))
fails, used = [], set()
for n in DOC_NAMES:
    lines = re.findall(r"`([^`\n]+)`", no_fences(doc[n]))
    for info, body in re.findall(r"```([A-Za-z]*)\n(.*?)\n```", doc[n], flags=re.S):
        if info in ("", "bash", "sh", "shell", "console"):
            lines += [l.split("#")[0] for l in body.splitlines()]
    for l in lines:
        for m in re.finditer(r"\bmake\s+([a-z][a-z0-9-]*)", l):
            used.add(m.group(1))
            if m.group(1) not in mk_targets:
                fails.append(n + ": 'make " + m.group(1) + "' is not a target of the Makefile")
judge_doc(G, "every make command written in the documents is a target of the Makefile (" + str(len(used)) + " different commands)", cap(sorted(set(fails))))

# 9.7 every quoted line is a line of the evidence file named above it
fails, blocks, quoted = [], 0, 0
for n in DOC_NAMES:
    found = re.findall(r"<!-- from: (\S+) -->\n```text\n(.*?)\n```", doc[n], flags=re.S)
    if len(found) != len(re.findall(r"<!-- from: ", doc[n])):
        fails.append(n + ": a '<!-- from: file -->' comment is not followed by a text block")
    for f, body in found:
        blocks += 1
        if not (ROOT / f).is_file():
            fails.append(n + ": the quoted file " + f + " does not exist")
            continue
        have = {l.rstrip() for l in read_text(ROOT / f).splitlines()}
        for line in body.splitlines():
            if line.strip() and line.strip() not in ("...", "…"):
                quoted += 1
                if line.rstrip() not in have:
                    fails.append(n + ": the line '" + line.strip()[:60] + "' is not in " + f)
judge_doc(G, "every line of the " + str(blocks) + " quoted blocks (" + str(quoted) + " lines) is a line of the evidence file named above it", cap(fails))

# 9.8 the numbers in the documents agree with the manifests and with the AI log
fails = []
kinds_n = {}
for d in docs:
    kinds_n[kind(d)] = kinds_n.get(kind(d), 0) + 1
want = "(" + str(len(docs)) + " objects: namespace, " + str(kinds_n.get("NetworkPolicy", 0)) + " NetworkPolicies, " + str(kinds_n.get("ServiceAccount", 0)) + " service accounts, " + \
    str(kinds_n.get("Role", 0)) + " roles and " + str(kinds_n.get("RoleBinding", 0)) + " role bindings, " + str(kinds_n.get("ConfigMap", 0)) + " ConfigMaps, " + \
    str(kinds_n.get("Service", 0)) + " Services, " + str(kinds_n.get("StatefulSet", 0)) + " StatefulSets, " + str(kinds_n.get("Deployment", 0)) + " Deployments, " + \
    str(kinds_n.get("HorizontalPodAutoscaler", 0)) + " autoscalers and " + str(kinds_n.get("Job", 0)) + " Jobs: the setup Job and the replay Job)"
if want not in readme:
    fails.append("README.md has no sentence that lists the objects like this: " + want)
policy_names = sorted(name(d) for d in docs if kind(d) == "NetworkPolicy")
table = readme.split("| Policy | What it allows (everything else is dropped) |", 1)
if len(table) < 2:
    fails.append("README.md has no policy table that starts with '| Policy | What it allows (everything else is dropped) |'")
elif sorted(re.findall(r"^\| `([a-z-]+)` \|", table[1].split("\n\n", 1)[0], flags=re.M)) != policy_names:
    fails.append("the policy table of README.md does not list exactly the " + str(len(policy_names)) + " NetworkPolicies of the manifests")
for n in ("README.md", "integritypacket.md"):
    fails += [n + ": '" + m.group(0) + "' but the manifests hold " + str(len(policy_names)) + " NetworkPolicies" for m in re.finditer(r"\b(\d+) (?:NetworkPolicies|policies)\b", no_code(doc[n])) if int(m.group(1)) != len(policy_names)]
log_rows = [r for r in ailog.splitlines() if re.match(r"^\| \d+ \|", r)]
log_mistakes = [r for r in ailog.splitlines() if re.match(r"^\| [A-Z] \|", r)]
stated = {"recommendations": re.search(r"holds (\d+) consequential recommendations", packet), "mistakes": re.search(r"holds (\d+) mistakes", packet)}
for key, m in stated.items():
    if m is None:
        fails.append("integritypacket.md has no sentence with the number of " + key + " in the AI log")
if stated["recommendations"] and int(stated["recommendations"].group(1)) != len(log_rows):
    fails.append("the packet says " + stated["recommendations"].group(1) + " recommendations, the AI log has " + str(len(log_rows)))
if stated["mistakes"] and int(stated["mistakes"].group(1)) != len(log_mistakes):
    fails.append("the packet says " + stated["mistakes"].group(1) + " mistakes, the AI log has " + str(len(log_mistakes)))
judge_doc(G, "the numbers agree: " + str(len(docs)) + " objects and " + str(len(policy_names)) + " policies against the manifests; " + str(len(log_rows)) + " recommendations and " + str(len(log_mistakes)) + " mistakes against the AI log", cap(fails))

# 9.9 every evidence file is listed in the packet
evidence_files = [p for p in published if p.startswith("evidence/") and not p.rsplit("/", 1)[-1].startswith(".")]
judge_doc(G, "every file in evidence/ is listed in the Integrity Packet (" + str(len(evidence_files)) + " files)", cap([p + " is not mentioned in integritypacket.md" for p in evidence_files if p.rsplit("/", 1)[-1] not in packet]))

# 9.10 nothing private in the evidence or in the documents
PRIVATE = re.compile(r"^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0$|255\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")
IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\.?\d)")
dataset_ips = set()
for csv_file in sorted((ROOT / "producer").glob("*.csv")):
    dataset_ips |= {m.group(0) for m in IPV4.finditer(read_text(csv_file))}
home = str(Path.home())
scan_files = evidence_files
fails = []
for rel in scan_files + [n for n in DOC_NAMES if (ROOT / n).is_file()]:
    text = read_text(ROOT / rel)
    pub = [m.group(0) for m in IPV4.finditer(text) if all(int(g) <= 255 for g in m.groups()) and not PRIVATE.match(m.group(0)) and m.group(0) not in dataset_ips]
    if pub:
        fails.append(rel + ": " + str(len(pub)) + " public IP address(es) that are not in the dataset")
    if re.search(r"\bec2-(?:\d{1,3}-){3}\d{1,3}\b", text):
        fails.append(rel + ": an EC2 host name (it contains the machine's public address)")
    if re.search(r"(?<!\d)\d{12}(?!\d)", text):
        fails.append(rel + ": a 12-digit number (an AWS account number looks like this)")
    if re.search(r"/Users/[\w.-]+", text) or (len(home) > 6 and home in text):
        fails.append(rel + ": a path into a home folder")
    if re.search(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----", text):
        fails.append(rel + ": an AWS access key or a private key")
fails += ["could not read " + u + ", so it was not checked" for u in sorted(set(UNREADABLE))]
judge(G, "nothing private in the evidence or in the documents: no public address outside the dataset, host name, account number, home folder or key (" + str(len(scan_files) + sum((ROOT / n).is_file() for n in DOC_NAMES)) + " files)", cap(fails))

# 9.11 no value from .secrets/ in any file that Git would publish
needles, secret_files = set(), 0
secrets_dir = ROOT / ".secrets"
for f in sorted(secrets_dir.iterdir()) if secrets_dir.is_dir() else []:
    if f.is_file() and not f.name.endswith(".pub") and f.name != "known_hosts":
        secret_files += 1
        try:
            body = f.read_bytes()
        except OSError:
            UNREADABLE.append(".secrets/" + f.name)
            continue
        # A value that looks random: 20 or more letters and digits with at least two digits and two capitals (a word such as "certificate-authority-data" is not a secret).
        needles |= {m.group(0) for m in re.finditer(rb"[A-Za-z0-9+/=_-]{20,}", body) if len(re.findall(rb"[0-9]", m.group(0))) >= 2 and len(re.findall(rb"[A-Z]", m.group(0))) >= 2}
        if len(body.strip()) >= 12 and b"\n" not in body.strip():
            needles.add(body.strip())
fails = []
for rel in published:
    try:
        if (ROOT / rel).stat().st_size > 40 * 1024 * 1024:
            continue
        blob = (ROOT / rel).read_bytes()
    except OSError:
        UNREADABLE.append(rel)
        continue
    fails += [rel + " contains a value that is also in .secrets/" for n_ in needles if n_ in blob][:1]
fails += ["could not read " + u + ", so it was not searched" for u in sorted(set(UNREADABLE))]
if secret_files:
    judge(G, "no value from .secrets/ is in any file that Git would publish (" + str(secret_files) + " secret files, " + str(len(published)) + " files searched)", cap(fails))
else:
    judge(G, "no value from .secrets/ is in a published file (this computer has no .secrets/ folder, so there was nothing to search for)", cap(fails))

# ------------------------------------------------------------ the report
counts, kinds = {"PASS": 0, "FAIL": 0, "WARN": 0, "TODO": 0}, {}
for d in docs:
    kinds[kind(d)] = kinds.get(kind(d), 0) + 1
print("CA3 manifest check: " + str(len(docs)) + " objects rendered by kubectl kustomize (" + ", ".join(str(v) + " " + k for k, v in sorted(kinds.items(), key=lambda x: -x[1])) + ")")
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

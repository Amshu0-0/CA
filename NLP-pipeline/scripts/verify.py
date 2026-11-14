# Checks, before anything is started and without a cluster, that the parts of the stack agree with each other.
#
#   make verify
#   python3 NLP-pipeline/scripts/verify.py                          the manifests of the folder NLP-pipeline
#   python3 NLP-pipeline/scripts/verify.py --rendered FILE          a file that "kubectl kustomize" wrote, instead of rendering
#
# The mistakes that cost the most time are the small ones that only show when a pod starts: a setting that the program needs and the ConfigMap does not
# have, a Secret that is mounted but never created, a port that is 8000 in one file and 8002 in another, a network rule whose label has a typo (the
# pod then cannot reach anything and says nothing), an image tag that was never built. Each of them is a few lines of YAML, and all of them can be found
# by reading the files against each other. That is what this script does. It reads the manifests the way  kubectl apply -k .  does (with
# "kubectl kustomize" when kubectl is installed, otherwise with a small reader of its own, which understands what this stack uses), adds the two Jobs
# that are not in the list (the replay and the smoke test), reads the programs' source for the settings they need, and compares.
#
# What it does NOT do: it does not start anything, so it cannot say that a program works. It does not check YAML against the Kubernetes schemas
# (make validate does that, with kubeconform). A passing "make verify" means the files are consistent, not that the cluster will accept them.
# Nothing is changed anywhere. Needs python3 and PyYAML (the NLP project has it).

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NS = "nlp"
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job")
EXTRA_MANIFESTS = ("producer/job-replay.yaml", "producer/smoke/job-smoke.yaml")   # started by make replay and make smoke, not by kubectl apply -k .
SERVICE_API_ADDRESS = "10.43.0.1/32"
CLUSTER_KINDS = {"Namespace", "ClusterRole", "ClusterRoleBinding", "StorageClass", "PersistentVolume", "CustomResourceDefinition"}   # have no namespace

# The programs of the stack: the workload that runs it, and its source. The settings that a program needs are the names in its require(...) calls.
PROGRAMS = {"processor": "processor/processor.py", "restapi": "restapi/rest_app.py", "producer-replay": "producer/producer.py", "producer-smoke": "producer/producer.py"}

# Settings that name a port, and the container whose port it must be.
PORT_SETTINGS = {("processor", "METRICS_PORT"), ("producer-replay", "PRODUCER_METRICS_PORT"), ("restapi", "REST_PORT"), ("restapi", "REST_METRICS_PORT")}

Pod = namedtuple("Pod", "name kind labels spec obj")


class Problem(Exception):
    pass


# ---------------------------------------------------------------- reading the manifests

def read_docs(path):
    with open(path, encoding="utf-8") as f:
        return [d for d in yaml.safe_load_all(f) if d]


# What kubectl kustomize does for this stack, and nothing more: a list of files and folders, and ConfigMaps made from files. Anything else in a
# kustomization.yaml (patches, a namespace, name prefixes, images) is refused rather than ignored, so that a wrong answer is never given quietly.
def render_builtin(directory):
    kustomization = yaml.safe_load((directory / "kustomization.yaml").read_text(encoding="utf-8"))
    unknown = set(kustomization) - {"apiVersion", "kind", "resources", "configMapGenerator"}

    if unknown:
        raise Problem(f"{directory / 'kustomization.yaml'} uses {', '.join(sorted(unknown))}, which the built-in reader does not do. Install kubectl, or pass --rendered FILE")

    objs = []

    for entry in kustomization.get("resources", []):
        path = directory / entry
        objs += render_builtin(path) if path.is_dir() else read_docs(path)

    for generator in kustomization.get("configMapGenerator", []):
        options = generator.get("options", {})

        if not options.get("disableNameSuffixHash"):
            raise Problem(f"the ConfigMap {generator['name']} gets a hash at the end of its name, which the built-in reader cannot compute. Install kubectl, or pass --rendered FILE")

        data = {}

        for item in generator.get("files", []):
            key, _, relative = item.partition("=") if "=" in item else (Path(item).name, "", item)
            data[key] = (directory / relative).read_text(encoding="utf-8")

        for item in generator.get("literals", []):
            key, _, value = item.partition("=")
            data[key] = value

        meta = {"name": generator["name"], "namespace": generator.get("namespace")}
        meta.update({k: dict(options[k]) for k in ("labels", "annotations") if options.get(k)})
        objs.append({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {k: v for k, v in meta.items() if v}, "data": data})

    return objs


# (objects, how they were made)
def render(root, rendered=None, use_kubectl=True):
    if rendered:
        objs, how = read_docs(rendered), "from " + str(rendered)
    elif use_kubectl and shutil.which("kubectl"):
        done = subprocess.run(["kubectl", "kustomize", str(root)], capture_output=True, text=True, timeout=120)

        if done.returncode != 0:
            raise Problem("kubectl kustomize failed:\n" + done.stderr.strip()[:800])

        objs, how = [d for d in yaml.safe_load_all(done.stdout) if d], "with kubectl kustomize"
    else:
        objs, how = render_builtin(Path(root)), "with the built-in reader" + ("" if shutil.which("kubectl") else " (kubectl was not found)")

    for relative in EXTRA_MANIFESTS:
        objs += read_docs(Path(root) / relative)

    return objs, how


class Stack:
    def __init__(self, root, objs, how=""):
        self.root = Path(root)
        self.objs = objs
        self.how = how

    def of(self, kind):
        return [o for o in self.objs if o.get("kind") == kind]

    def named(self, kind, name):
        return next((o for o in self.of(kind) if o["metadata"]["name"] == name), None)

    def text(self, relative):
        return (self.root / relative).read_text(encoding="utf-8")

    def pods(self):
        return [Pod(o["metadata"]["name"], o["kind"], o["spec"]["template"].get("metadata", {}).get("labels", {}), o["spec"]["template"]["spec"], o)
                for o in self.objs if o.get("kind") in WORKLOADS]

    # The Secrets that bootstrap.py makes, and the file names inside each: read from its source (the names are written next to the data).
    def secrets(self):
        source = self.text("producer/bootstrap.py")
        people = re.search(r"for who in \(([^)]*)\)", source)
        names = re.findall(r'"([a-z]+)"', people.group(1)) if people else []
        found = {}

        for block in source.split("items.append((")[1:]:
            head = re.match(r'(f?)"([^"]+)"', block)
            keys = re.findall(r'"([A-Za-z0-9_.\-]+)"\s*:', block.split("))")[0])

            if not head or not keys:
                continue

            for name in ([head.group(2).replace("{who}", w) for w in names] if "{who}" in head.group(2) else [head.group(2)]):
                found[name] = set(keys)

        if len(found) < 10:
            raise Problem("could not read the list of Secrets from producer/bootstrap.py (found " + str(len(found)) + ")")

        return found

    def config_maps(self):
        return {o["metadata"]["name"]: {**o.get("data", {}), **o.get("binaryData", {})} for o in self.of("ConfigMap")}


def label(o):
    return o["kind"] + "/" + o["metadata"]["name"]


def containers(pod, init=True):
    return (pod.spec.get("initContainers", []) if init else []) + pod.spec["containers"]


def own_container(pod):
    return next((c for c in pod.spec["containers"] if c["name"] in ("processor", "restapi", "producer")), None)


def ports_of(pods):
    found = set()

    for pod in pods:
        for c in pod.spec["containers"]:
            for p in c.get("ports", []):
                found |= {p["containerPort"], p.get("name")}

    return found - {None}


def selected(pods, match):
    return [p for p in pods if all(p.labels.get(k) == v for k, v in match.items())]


# The settings a container ends up with from its own list and from the ConfigMaps it takes in whole: name -> value (None when the value comes from a Secret or the pod).
def settings_of(stack, container):
    maps, found = stack.config_maps(), {}

    for source in container.get("envFrom", []):
        if "configMapRef" in source:
            found.update(maps.get(source["configMapRef"]["name"], {}))

    for item in container.get("env", []):
        found[item["name"]] = item.get("value")

    return found


def required_settings(stack, name):
    source = stack.text(PROGRAMS[name])
    return set(re.findall(r'\brequire\("([A-Z0-9_]+)"\)', source)), set(re.findall(r'\brequire_secret\("([A-Z0-9_]+)"\)', source)), source


# ---------------------------------------------------------------- the checks (each returns a list of problems; none = the check passed)

def check_namespace(s):
    problems = [f"{label(o)} is in the namespace {o['metadata'].get('namespace')!r}, not {NS!r}" for o in s.objs if o["kind"] not in CLUSTER_KINDS and o["metadata"].get("namespace") != NS]
    namespace = s.named("Namespace", NS)

    if namespace is None:
        return problems + ["there is no Namespace " + NS]

    if namespace["metadata"].get("labels", {}).get("pod-security.kubernetes.io/enforce") != "restricted":
        problems.append("the Namespace does not enforce the Pod Security level 'restricted' (label pod-security.kubernetes.io/enforce)")

    return problems


def check_images(s):
    version = (s.root / "VERSION").read_text(encoding="utf-8").strip()
    problems, ours = [], set()

    for pod in s.pods():
        for c in containers(pod):
            image = c["image"]
            name, tag = (image.rsplit(":", 1) if ":" in image.rsplit("/", 1)[-1] else (image, ""))

            if not tag or tag == "latest":
                problems.append(f"{pod.name}/{c['name']}: image {image} has no fixed tag")
            elif image.startswith("ghcr.io/"):
                ours.add(name.rsplit("/", 1)[-1])

                if tag != version:
                    problems.append(f"{pod.name}/{c['name']}: image {image} has the tag {tag}, but the file VERSION says {version}")

    if ours != {"nlp-producer", "nlp-processor", "nlp-restapi"}:
        problems.append("the stack uses these images of ghcr.io: " + ", ".join(sorted(ours)) + " (expected nlp-producer, nlp-processor and nlp-restapi)")

    return problems


def check_pod_security(s):
    problems = []

    for pod in s.pods():
        podsc = pod.spec.get("securityContext", {})

        for flag in ("hostNetwork", "hostPID", "hostIPC"):
            if pod.spec.get(flag):
                problems.append(f"{pod.name}: {flag} is on")

        problems += [f"{pod.name}: volume {v['name']} is a hostPath" for v in pod.spec.get("volumes", []) if "hostPath" in v]

        for c in containers(pod):
            sc, where = c.get("securityContext", {}), f"{pod.name}/{c['name']}"

            if sc.get("allowPrivilegeEscalation") is not False:
                problems.append(f"{where}: allowPrivilegeEscalation is not false")

            if "ALL" not in sc.get("capabilities", {}).get("drop", []):
                problems.append(f"{where}: does not drop all capabilities")

            if sc.get("privileged"):
                problems.append(f"{where}: is privileged")

            if not (sc.get("runAsNonRoot") or podsc.get("runAsNonRoot")) or sc.get("runAsUser") == 0 or podsc.get("runAsUser") == 0:
                problems.append(f"{where}: is not required to run as a non-root user")

            if (sc.get("seccompProfile") or podsc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
                problems.append(f"{where}: has no seccompProfile RuntimeDefault")

    return problems


def check_resources(s):
    return [f"{pod.name}/{c['name']}: no {kind} for {what}" for pod in s.pods() for c in containers(pod)
            for kind in ("requests", "limits") for what in ("cpu", "memory") if what not in c.get("resources", {}).get(kind, {})]


def check_config_and_secrets(s):
    maps, secrets, problems, used_maps, used_secrets = s.config_maps(), s.secrets(), [], set(), set()

    def need_map(where, name, key=None):
        used_maps.add(name)

        if name not in maps:
            problems.append(f"{where}: the ConfigMap {name} does not exist")
        elif key and key not in maps[name]:
            problems.append(f"{where}: the ConfigMap {name} has no key {key}")

    def need_secret(where, name, key=None):
        used_secrets.add(name)

        if name not in secrets:
            problems.append(f"{where}: the Secret {name} is not one that bootstrap.py creates")
        elif key and key not in secrets[name]:
            problems.append(f"{where}: the Secret {name} has no key {key} (it has {', '.join(sorted(secrets[name]))})")

    for pod in s.pods():
        for volume in pod.spec.get("volumes", []):
            if "configMap" in volume:
                need_map(pod.name, volume["configMap"]["name"])
            if "secret" in volume:
                need_secret(pod.name, volume["secret"]["secretName"])

        for c in containers(pod):
            where = f"{pod.name}/{c['name']}"

            for source in c.get("envFrom", []):
                if "configMapRef" in source:
                    need_map(where, source["configMapRef"]["name"])
                if "secretRef" in source:
                    need_secret(where, source["secretRef"]["name"])

            for item in c.get("env", []):
                ref = item.get("valueFrom", {})

                if "configMapKeyRef" in ref:
                    need_map(where, ref["configMapKeyRef"]["name"], ref["configMapKeyRef"]["key"])
                if "secretKeyRef" in ref:
                    need_secret(where, ref["secretKeyRef"]["name"], ref["secretKeyRef"]["key"])

        defined = {v["name"] for v in pod.spec.get("volumes", [])} | {t["metadata"]["name"] for t in pod.obj["spec"].get("volumeClaimTemplates", [])}

        for volume_name in {m["name"] for c in containers(pod) for m in c.get("volumeMounts", [])}:
            if volume_name not in defined:
                problems.append(f"{pod.name}: a container mounts the volume {volume_name}, which the pod does not define")

    problems += [f"the ConfigMap {name} is not used by any pod" for name in maps if name not in used_maps]
    problems += [f"the Secret {name} is created by bootstrap.py but no pod uses it" for name in secrets if name not in used_secrets]
    return problems


# What each program needs (its require(...) calls) against what its container is given, and the files named by the *_FILE settings against what is mounted.
def check_settings(s):
    problems, secrets, maps = [], s.secrets(), s.config_maps()

    for pod in s.pods():
        container = own_container(pod)
        wanted_files = {}

        if pod.name in PROGRAMS and container is not None:
            given, where = settings_of(s, container), f"{pod.name}/{container['name']}"
            plain, secret, _ = required_settings(s, pod.name)
            problems += [f"{where}: the program needs the setting {n}, which neither env nor a ConfigMap of the pod gives" for n in sorted(plain - set(given))]
            problems += [f"{where}: the program needs {n} (or {n}_FILE), which neither env nor a ConfigMap of the pod gives" for n in sorted(secret) if n not in given and n + "_FILE" not in given]
            wanted_files = {n: given[n] for n in {*plain, *(x + "_FILE" for x in secret)} if n.endswith("_FILE") and (given.get(n) or "").startswith("/")}

        # the files that a container names in its own env (the REST API's certificate, the logins of Grafana and MongoDB) must be mounted as well
        for c in containers(pod):
            wanted_files.update({i["name"]: i["value"] for i in c.get("env", []) if i["name"].endswith("_FILE") and (i.get("value") or "").startswith("/")})

        for name, path in sorted(wanted_files.items()):
            problems += file_problems(pod, name, path, secrets, maps)

    return problems


# The file that a *_FILE setting names must lie inside a mount of the container, and it must be a key of what is mounted there.
def file_problems(pod, name, path, secrets, maps):
    mounts = [(m["mountPath"], m["name"]) for c in pod.spec["containers"] for m in c.get("volumeMounts", []) if path == m["mountPath"] or path.startswith(m["mountPath"].rstrip("/") + "/")]

    if not mounts:
        return [f"{pod.name}: {name} is {path}, and no volume is mounted there"]

    mount, volume_name = max(mounts)
    volume = next((v for v in pod.spec.get("volumes", []) if v["name"] == volume_name), None)
    key = path[len(mount):].lstrip("/").split("/")[0]

    if volume is None or "secret" not in volume and "configMap" not in volume:
        return []

    if "secret" in volume:
        keys = secrets.get(volume["secret"]["secretName"], set())
        owner = "the Secret " + volume["secret"]["secretName"]
    else:
        keys = set(maps.get(volume["configMap"]["name"], {}))
        owner = "the ConfigMap " + volume["configMap"]["name"]

    if "items" in volume.get("secret", volume.get("configMap", {})):
        keys = {i["path"] for i in volume.get("secret", volume.get("configMap", {}))["items"]}

    return [] if key in keys else [f"{pod.name}: {name} is {path}, but {owner} has no file {key} (it has {', '.join(sorted(keys))})"]


def check_ports(s):
    problems, pods = [], s.pods()

    # a setting that names a port must be a port that the container declares
    for pod in pods:
        container = own_container(pod)

        for program, setting in PORT_SETTINGS:
            if pod.name == program and container is not None:
                value = settings_of(s, container).get(setting)
                declared = {p["containerPort"] for p in container.get("ports", [])}

                if value is None or int(value) not in declared:
                    problems.append(f"{pod.name}: {setting} is {value}, but the container declares the ports {sorted(declared)}")

    # a Service must lead to a port that exists
    for service in s.of("Service"):
        selector = service["spec"].get("selector")

        if not selector:
            continue

        pods_of = selected(pods, selector)

        if not pods_of:
            problems.append(f"Service/{service['metadata']['name']}: no pod has the labels {selector}")
            continue

        for p in service["spec"]["ports"]:
            target = p.get("targetPort", p["port"])

            if target not in ports_of(pods_of):
                problems.append(f"Service/{service['metadata']['name']}: its port {p['port']} leads to {target}, which the pods behind it do not declare")

    # a probe must look at a declared port, and (for our own programs) at a path that the program answers
    for pod in pods:
        for c in pod.spec["containers"]:
            for kind in ("readinessProbe", "livenessProbe", "startupProbe"):
                probe = c.get(kind)
                http = (probe or {}).get("httpGet") or (probe or {}).get("tcpSocket")

                if not http:
                    continue

                declared = {p["containerPort"] for p in c.get("ports", [])} | {p.get("name") for p in c.get("ports", [])}

                if http["port"] not in declared:
                    problems.append(f"{pod.name}/{c['name']}: the {kind} looks at the port {http['port']}, which the container does not declare")

                if pod.name in PROGRAMS and probe.get("httpGet") and f'"{probe["httpGet"]["path"]}"' not in required_settings(s, pod.name)[2]:
                    problems.append(f"{pod.name}/{c['name']}: the {kind} asks for {probe['httpGet']['path']}, which {PROGRAMS[pod.name]} does not mention")

    return problems


def check_network_policies(s):
    problems, pods = [], s.pods()
    subnet = yaml.safe_load(s.text("infra/config.yml"))["subnet_cidr"]
    policies = s.of("NetworkPolicy")

    if not any(p["metadata"]["name"] == "default-deny-all" and p["spec"].get("podSelector") == {} and set(p["spec"].get("policyTypes", [])) == {"Ingress", "Egress"} for p in policies):
        problems.append("there is no policy default-deny-all that blocks everything in both directions")

    for policy in policies:
        name, spec = policy["metadata"]["name"], policy["spec"]
        match = spec.get("podSelector", {}).get("matchLabels", {})
        own = selected(pods, match)

        if match and not own:
            problems.append(f"{name}: no pod has the labels {match}, so it protects nothing")

        for direction, key, peers_key in (("ingress", "ingress", "from"), ("egress", "egress", "to")):
            for rule in spec.get(key, []):
                targets = list(own) if direction == "ingress" else []

                for peer in rule.get(peers_key, []):
                    if "ipBlock" in peer:
                        if peer["ipBlock"]["cidr"] not in (subnet, SERVICE_API_ADDRESS):
                            problems.append(f"{name}: the address block {peer['ipBlock']['cidr']} is not subnet_cidr of infra/config.yml ({subnet})")
                    elif "namespaceSelector" not in peer:
                        peer_match = peer.get("podSelector", {}).get("matchLabels", {})
                        found = selected(pods, peer_match)

                        if not found:
                            problems.append(f"{name}: no pod has the labels {peer_match}")

                        if direction == "egress":
                            targets += found

                # the ports are those of the pods that receive the connection: the pods of the policy (ingress) or the peers (egress)
                for port in rule.get("ports", []) if targets else []:
                    if port["port"] not in ports_of(targets):
                        problems.append(f"{name}: allows the port {port['port']}, which the pods it leads to do not declare")

    return problems


def check_prometheus(s):
    problems, services, pods = [], {o["metadata"]["name"]: o for o in s.of("Service")}, s.pods()
    config = yaml.safe_load(s.named("ConfigMap", "prometheus-config")["data"]["prometheus.yml"])

    for job in config["scrape_configs"]:
        for dns in job.get("dns_sd_configs", []):
            for full in dns["names"]:
                host, _, rest = full.partition(".")
                service = services.get(host)

                if rest != f"{NS}.svc.cluster.local" or service is None:
                    problems.append(f"{job['job_name']}: {full} is not a Service of the namespace {NS}")
                    continue

                if service["spec"].get("clusterIP") != "None":
                    problems.append(f"{job['job_name']}: Service/{host} is not headless, so the DNS answer holds one address and not every pod's")

                if dns["port"] not in ports_of(selected(pods, service["spec"].get("selector", {}))):
                    problems.append(f"{job['job_name']}: Prometheus reads the port {dns['port']} of {host}, which the pods behind it do not declare")

        for static in job.get("static_configs", []):
            for target in static["targets"]:
                host, _, port = target.partition(":")

                if host in ("localhost", "127.0.0.1"):
                    continue

                if host not in services or int(port) not in {p["port"] for p in services[host]["spec"]["ports"]}:
                    problems.append(f"{job['job_name']}: {target} is not a Service and port of the stack")

    return problems


def check_design(s):
    problems, maps = [], s.config_maps()
    pods = {p.name: p for p in s.pods()}
    processor = pods["processor"].obj["spec"] if "processor" in pods else {}

    if s.of("HorizontalPodAutoscaler"):
        problems.append("there is a HorizontalPodAutoscaler: the processor must be ONE pod (the windows need the lines in order)")

    if processor.get("replicas") != 1 or processor.get("strategy", {}).get("type") != "Recreate":
        problems.append("the processor Deployment must have replicas: 1 and strategy Recreate (two processors would both score the same windows)")

    kafka = pods.get("kafka")
    partitions = {e["name"]: e.get("value") for c in kafka.spec["containers"] for e in c.get("env", [])}.get("KAFKA_NUM_PARTITIONS") if kafka else None

    if partitions != "1":
        problems.append(f"Kafka makes topics with {partitions} partitions: it must be 1, or the lines of one window arrive by different paths and in no order")

    dataset = maps.get("pipeline-config", {}).get("DATASET_FILE")
    dockerfile = s.text("producer/Dockerfile")

    if f"ARG DATASET_FILE={dataset}" not in dockerfile or f"!NLP-pipeline/bundle/{dataset}" not in s.text("producer/Dockerfile.dockerignore"):
        problems.append(f"DATASET_FILE is {dataset}, but producer/Dockerfile and producer/Dockerfile.dockerignore do not both name that file")

    for key in ("KAFKA_TOPIC", "MONGO_DB", "MONGO_COLLECTION", "MONGO_WINDOWS_COLLECTION", "MONGO_RUNS_COLLECTION", "CONSUMER_GROUP"):
        if not maps.get("pipeline-config", {}).get(key) and not maps.get("processor-config", {}).get(key):
            problems.append(f"neither pipeline-config nor processor-config has {key}")

    return problems


def check_rbac(s):
    problems, accounts = [], {a["metadata"]["name"]: a for a in s.of("ServiceAccount")}

    if s.of("ClusterRole") or s.of("ClusterRoleBinding"):
        problems.append("there is a ClusterRole or ClusterRoleBinding: every right of this stack is limited to the namespace")

    for role in s.of("Role"):
        for rule in role.get("rules", []):
            if "secrets" in rule.get("resources", []) or "*" in rule.get("resources", []):
                if not (role["metadata"]["name"] == "bootstrap" and rule["verbs"] == ["create"]):
                    problems.append(f"Role/{role['metadata']['name']} reaches Secrets with {rule['verbs']}; only the setup Job may, and only to create")

    for binding in s.of("RoleBinding"):
        if s.named("Role", binding["roleRef"]["name"]) is None:
            problems.append(f"RoleBinding/{binding['metadata']['name']} names the Role {binding['roleRef']['name']}, which does not exist")

        problems += [f"RoleBinding/{binding['metadata']['name']} names the ServiceAccount {x['name']}, which does not exist" for x in binding["subjects"] if x["name"] not in accounts]

    for pod in s.pods():
        account = pod.spec.get("serviceAccountName")

        if account not in accounts:
            problems.append(f"{pod.name}: the ServiceAccount {account} does not exist")
        elif pod.name not in ("bootstrap", "alloy") and pod.spec.get("automountServiceAccountToken") is not False and accounts[account].get("automountServiceAccountToken") is not False:
            problems.append(f"{pod.name}: the pod gets a Kubernetes API token that it does not need")

    return problems


# The Makefile and the scripts call things by name (deployment/processor, job/producer-replay...). A typo there only shows when the command runs.
def check_names_in_scripts(s):
    known = {(o["kind"].lower(), o["metadata"]["name"]) for o in s.objs}
    known |= {("secret", n) for n in s.secrets()}
    kinds = {"deploy": "deployment", "deployment": "deployment", "statefulset": "statefulset", "sts": "statefulset", "daemonset": "daemonset", "ds": "daemonset", "job": "job",
             "svc": "service", "service": "service", "cm": "configmap", "configmap": "configmap", "secret": "secret"}
    problems = []

    for path in [s.root / "Makefile", *sorted((s.root / "scripts").glob("*.py")), *sorted((s.root / "scripts").glob("*.sh"))]:
        if path.name == "verify.py":
            continue

        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for word, name in re.findall(r"(?<![\w/.-])(deploy|deployment|statefulset|sts|daemonset|ds|job|svc|service|cm|configmap|secret)/([a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![\w/-])", line):
                if (kinds[word], name) not in known:
                    problems.append(f"{path.relative_to(s.root)}:{number}: {word}/{name} is not in the stack")

    return problems


# The files of the folder evidence/ are published with the repository: no public address, no EC2 host name, no account number, no home folder, no key.
# The test is the one of scripts/scrub-evidence.py (make scrub), so what that program replaces is exactly what is reported here.
def check_evidence(s):
    folder = s.root / "evidence"

    if not folder.is_dir():
        return []

    spec = importlib.util.spec_from_file_location("scrub_evidence", s.root / "scripts" / "scrub-evidence.py")
    scrubber = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scrubber)
    problems = []

    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        text, name = path.read_text(encoding="utf-8", errors="replace"), str(path.relative_to(s.root))

        if scrubber.SECRET.search(text):
            problems.append(f"{name}: holds an access key or a private key. Delete this file")
            continue

        problems += [f'{name}: {n} {what}. Run  make scrub FILES="{name}"' for what, n in scrubber.scrub(text)[1].items() if n]

    return problems


CHECKS = (
    ("everything is in the namespace nlp, which enforces the restricted pod security level", check_namespace),
    ("the images are the three of the stack, with the tag of the file VERSION", check_images),
    ("every container drops its rights (pod security 'restricted')", check_pod_security),
    ("every container has cpu and memory requests and limits", check_resources),
    ("every ConfigMap and Secret that a pod names exists, and every one that exists is used", check_config_and_secrets),
    ("every setting the programs need is given, and every *_FILE setting names a file that is mounted", check_settings),
    ("ports agree: settings, containers, Services and probes", check_ports),
    ("every network rule names pods and ports that exist, and the address blocks are the subnet of infra/config.yml", check_network_policies),
    ("Prometheus reads Services and ports that exist", check_prometheus),
    ("the design holds: one partition, one processor, no autoscaler, one replay file", check_design),
    ("rights: nothing cluster-wide, Secrets only created by the setup Job, every account exists", check_rbac),
    ("the names that the Makefile and the scripts use are in the stack", check_names_in_scripts),
    ("the evidence files hold no public address, account number, home folder or key", check_evidence),
)


def run(root, rendered=None, use_kubectl=True, say=print):
    objs, how = render(root, rendered, use_kubectl)
    stack = Stack(root, objs, how)
    say(f"Read {len(objs)} objects {how}.\n")
    failed = 0

    for title, check in CHECKS:
        problems = check(stack)
        failed += bool(problems)
        say(("FAIL " if problems else "ok   ") + title)

        for problem in problems[:10]:
            say("       - " + problem)

        if len(problems) > 10:
            say(f"       ... and {len(problems) - 10} more")

    say("")
    say(f"RESULT: FAIL - {failed} of {len(CHECKS)} checks failed" if failed else f"RESULT: PASS - all {len(CHECKS)} checks passed. The files agree with each other; nothing was started, so this does not say that the cluster will run it.")
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the manifests, the programs and the settings of the stack agree.")
    parser.add_argument("--root", default=str(ROOT), help="the folder with kustomization.yaml (default: NLP-pipeline)")
    parser.add_argument("--rendered", help="a YAML file made by  kubectl kustomize  (default: render the folder)")
    parser.add_argument("--no-kubectl", action="store_true", help="use the built-in reader even if kubectl is installed")
    args = parser.parse_args(argv)

    try:
        return run(args.root, args.rendered, not args.no_kubectl)
    except Problem as problem:
        print("Not done: " + str(problem), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

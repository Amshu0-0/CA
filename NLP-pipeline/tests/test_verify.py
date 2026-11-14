# scripts/verify.py (make verify) on the real manifests, and on copies of them with one mistake put in on purpose. A check that has never been
# seen to fail proves nothing, so every check below is shown to catch a mistake of the kind it is there for, and nothing else is reported with it.

import importlib.util
import shutil

import pytest
import yaml

import verify
from conftest import PIPELINE

TITLES = [title for title, _check in verify.CHECKS]
NAMESPACE, IMAGES, SECURITY, RESOURCES, CONFIG, SETTINGS, PORTS, NETPOL, PROMETHEUS, DESIGN, RBAC, NAMES, EVIDENCE = range(13)


@pytest.fixture
def copy(tmp_path):
    root = tmp_path / "NLP-pipeline"
    shutil.copytree(PIPELINE, root, ignore=shutil.ignore_patterns("tests", "bundle", "__pycache__", ".pytest_cache", "*.pyc", "evidence", ".secrets"))
    return root


def verdict(root, **options):
    lines = []
    code = verify.run(str(root), use_kubectl=False, say=lines.append, **options)
    failed = {i for i, title in enumerate(TITLES) if any(line == "FAIL " + title for line in lines)}
    return code, failed, "\n".join(lines)


def change_yaml(root, relative, change):
    path = root / relative
    docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    change(docs)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False), encoding="utf-8")


def find(docs, kind, name):
    return next(d for d in docs if d and d["kind"] == kind and d["metadata"]["name"] == name)


def container(docs, kind, name, which=None):
    spec = find(docs, kind, name)["spec"]["template"]["spec"]
    return next(c for c in spec["containers"] if which in (None, c["name"]))


def edit_text(root, relative, old, new):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= 1, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_the_real_stack_passes_every_check(copy):
    code, failed, shown = verdict(copy)

    assert code == 0 and not failed, shown
    assert "RESULT: PASS - all 13 checks passed" in shown and "nothing was started" in shown


def test_the_real_stack_passes_when_read_from_the_folder_itself():
    code, failed, shown = verdict(PIPELINE)

    assert code == 0 and not failed, shown


# (what is broken, how, the checks that must fail with it, a word that must be in the message)
def mistakes():
    def processor_ns(root):
        change_yaml(root, "processor/processor.yaml", lambda docs: find(docs, "Deployment", "processor")["metadata"].update(namespace="default"))

    def no_pod_security(root):
        change_yaml(root, "namespace.yaml", lambda docs: docs[0]["metadata"]["labels"].pop("pod-security.kubernetes.io/enforce"))

    def wrong_tag(root):
        change_yaml(root, "processor/processor.yaml", lambda docs: container(docs, "Deployment", "processor", "processor").update(image="ghcr.io/amshu0-0/nlp-processor:1.0.1"))

    def latest_image(root):
        change_yaml(root, "observability/kafka-exporter.yaml", lambda docs: container(docs, "Deployment", "kafka-exporter").update(image="danielqsj/kafka-exporter"))

    def privileged(root):
        change_yaml(root, "processor/processor.yaml", lambda docs: container(docs, "Deployment", "processor", "processor")["securityContext"].update(allowPrivilegeEscalation=True))

    def no_limits(root):
        change_yaml(root, "restapi/restapi.yaml", lambda docs: container(docs, "Deployment", "restapi", "restapi")["resources"].pop("limits"))

    def secret_typo(root):
        def change(docs):
            volumes = find(docs, "Deployment", "processor")["spec"]["template"]["spec"]["volumes"]
            next(v for v in volumes if v["name"] == "kafka-client-processor")["secret"]["secretName"] = "kafka-client-processorr"

        change_yaml(root, "processor/processor.yaml", change)

    def unused_secret(root):
        edit_text(root, "producer/bootstrap.py", '    items.append(("restapi-tls",', '    items.append(("never-used", "x", {"a": b"1"}))\n    items.append(("restapi-tls",')

    def setting_renamed(root):
        change_yaml(root, "config/pipeline-config.yaml", lambda docs: find(docs, "ConfigMap", "pipeline-config")["data"].update(KAFKA_TOPICS=find(docs, "ConfigMap", "pipeline-config")["data"].pop("KAFKA_TOPIC")))

    def wrong_file_name(root):
        change_yaml(root, "config/pipeline-config.yaml", lambda docs: find(docs, "ConfigMap", "pipeline-config")["data"].update(KAFKA_SSL_CERT_FILE="/etc/pki/kafka-client/client.crt"))

    def file_not_mounted(root):
        change_yaml(root, "config/pipeline-config.yaml", lambda docs: find(docs, "ConfigMap", "pipeline-config")["data"].update(KAFKA_SSL_CA_FILE="/somewhere/else/ca.crt"))

    def port_moved(root):
        change_yaml(root, "processor/processor.yaml", lambda docs: container(docs, "Deployment", "processor", "processor")["ports"][0].update(containerPort=8002))

    def service_selector(root):
        change_yaml(root, "processor/processor.yaml", lambda docs: find(docs, "Service", "processor")["spec"]["selector"].update({"app.kubernetes.io/name": "procesor"}))

    def probe_path(root):
        edit_text(root, "processor/processor.yaml", "path: /readyz", "path: /ready")

    def netpol_label(root):
        def change(docs):
            policy = find(docs, "NetworkPolicy", "kafka-ingress")
            policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] = "producerr"

        change_yaml(root, "policies/networkpolicies.yaml", change)

    def netpol_port(root):
        change_yaml(root, "policies/networkpolicies.yaml", lambda docs: find(docs, "NetworkPolicy", "processor-ingress")["spec"]["ingress"][0]["ports"][0].update(port=8002))

    def netpol_cidr(root):
        change_yaml(root, "policies/networkpolicies.yaml", lambda docs: find(docs, "NetworkPolicy", "bootstrap-egress")["spec"]["egress"][0]["to"][0]["ipBlock"].update(cidr="10.20.2.0/24"))

    def prometheus_target(root):
        edit_text(root, "observability/prometheus.yaml", 'targets: ["kafka-exporter:9308"]', 'targets: ["kafka-exporter:9309"]')

    def two_processors(root):
        change_yaml(root, "processor/processor.yaml", lambda docs: find(docs, "Deployment", "processor")["spec"].update(replicas=2))

    def three_partitions(root):
        def change(docs):
            kafka = container(docs, "StatefulSet", "kafka")
            next(e for e in kafka["env"] if e["name"] == "KAFKA_NUM_PARTITIONS")["value"] = "3"

        change_yaml(root, "kafka/kafka.yaml", change)

    def autoscaler(root):
        edit_text(root, "processor/processor.yaml", "---\n", "---\napiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: processor\n  namespace: nlp\nspec:\n  scaleTargetRef: {apiVersion: apps/v1, kind: Deployment, name: processor}\n  minReplicas: 1\n  maxReplicas: 3\n---\n")

    def secrets_readable(root):
        change_yaml(root, "rbac/roles.yaml", lambda docs: find(docs, "Role", "pipeline-viewer")["rules"][0]["resources"].append("secrets"))

    def cluster_wide(root):
        edit_text(root, "rbac/roles.yaml", "---\n", "---\napiVersion: rbac.authorization.k8s.io/v1\nkind: ClusterRole\nmetadata:\n  name: x\nrules: []\n---\n")

    def makefile_typo(root):
        edit_text(root, "Makefile", "rollout status deployment/processor", "rollout status deployment/procesor")

    def script_typo(root):
        edit_text(root, "scripts/heal-check.py", "deployment/processor", "deployment/procesor")

    return [
        ("a pod in another namespace", processor_ns, {NAMESPACE}, "is in the namespace 'default'"),
        ("pod security switched off", no_pod_security, {NAMESPACE}, "does not enforce the Pod Security level"),
        ("an image tag that was never built", wrong_tag, {IMAGES}, "has the tag 1.0.1, but the file VERSION says"),
        ("an image without a tag", latest_image, {IMAGES}, "has no fixed tag"),
        ("a container that may escalate", privileged, {SECURITY}, "allowPrivilegeEscalation is not false"),
        ("a container without limits", no_limits, {RESOURCES}, "no limits for cpu"),
        ("a Secret name with a typo", secret_typo, {CONFIG, SETTINGS}, "kafka-client-processorr is not one that bootstrap.py creates"),
        ("a Secret that nothing uses", unused_secret, {CONFIG}, "never-used is created by bootstrap.py but no pod uses it"),
        ("a setting renamed in the ConfigMap", setting_renamed, {SETTINGS, DESIGN}, "needs the setting KAFKA_TOPIC"),
        ("a certificate file name that the Secret does not have", wrong_file_name, {SETTINGS}, "has no file client.crt"),
        ("a certificate path that is not mounted", file_not_mounted, {SETTINGS}, "no volume is mounted there"),
        ("the metrics port moved in one place", port_moved, {PORTS, NETPOL, PROMETHEUS}, "METRICS_PORT is 8000"),
        ("a Service selector with a typo", service_selector, {PORTS}, "no pod has the labels"),
        ("a probe path the program does not answer", probe_path, {PORTS}, "asks for /ready"),
        ("a network rule label with a typo", netpol_label, {NETPOL}, "no pod has the labels {'app.kubernetes.io/name': 'producerr'}"),
        ("a network rule for the wrong port", netpol_port, {NETPOL}, "allows the port 8002"),
        ("an address block that is not the subnet", netpol_cidr, {NETPOL}, "is not subnet_cidr of infra/config.yml"),
        ("Prometheus reading a port that does not exist", prometheus_target, {PROMETHEUS}, "kafka-exporter:9309 is not a Service and port"),
        ("two processors", two_processors, {DESIGN}, "replicas: 1 and strategy Recreate"),
        ("three partitions", three_partitions, {DESIGN}, "topics with 3 partitions"),
        ("an autoscaler", autoscaler, {DESIGN}, "there is a HorizontalPodAutoscaler"),
        ("a Role that reads Secrets", secrets_readable, {RBAC}, "reaches Secrets"),
        ("a ClusterRole", cluster_wide, {RBAC}, "ClusterRole or ClusterRoleBinding"),
        ("a name with a typo in the Makefile", makefile_typo, {NAMES}, "Makefile:"),
        ("a name with a typo in a script", script_typo, {NAMES}, "scripts/heal-check.py:"),
    ]


@pytest.mark.parametrize("what,mistake,expected,message", mistakes(), ids=[m[0] for m in mistakes()])
def test_a_mistake_is_found_by_the_check_that_is_there_for_it(copy, what, mistake, expected, message):
    mistake(copy)
    code, failed, shown = verdict(copy)

    assert code == 1, shown
    assert failed == expected, "the checks that failed were " + str(sorted(failed)) + ":\n" + shown
    assert message in shown, shown


def test_the_built_in_reader_refuses_what_it_does_not_understand(copy):
    edit_text(copy, "kustomization.yaml", "resources:", "namespace: other\nresources:")

    with pytest.raises(verify.Problem, match="does not do"):
        verify.run(str(copy), use_kubectl=False, say=lambda line: None)


def test_a_rendered_file_can_be_checked_instead(copy, tmp_path):
    objs, _how = verify.render(str(copy), use_kubectl=False)
    rendered = tmp_path / "rendered.yaml"
    rendered.write_text(yaml.safe_dump_all([o for o in objs if o["kind"] != "Job" or o["metadata"]["name"] == "bootstrap"]), encoding="utf-8")

    code, failed, shown = verdict(copy, rendered=rendered)

    assert code == 0 and "from " in shown.splitlines()[0], shown


def test_evidence_with_what_must_not_be_published_is_reported_until_it_is_scrubbed(copy):
    folder = copy / "evidence"
    folder.mkdir()
    (folder / "up.txt").write_text("node at 8.8.8.8 in account 123456789012, run from /Users/someone/Desktop, host ec2-8-8-8-8.compute-1.amazonaws.com\n", encoding="utf-8")
    (folder / "key.txt").write_text("AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    (folder / "fine.txt").write_text("pods ready: 10.42.0.17 is a cluster address, run run-20261114-091500\n", encoding="utf-8")

    code, failed, shown = verdict(copy)

    assert code == 1 and failed == {EVIDENCE}, shown
    assert "evidence/up.txt: 1 public IP addresses" in shown and "evidence/up.txt: 1 12-digit numbers" in shown and "evidence/up.txt: 1 EC2 host names" in shown
    assert "evidence/up.txt: 1 home folder paths" in shown and "evidence/key.txt: holds an access key or a private key" in shown and "fine.txt" not in shown

    # a file with a key must be deleted, not scrubbed; what make scrub leaves behind in the other files passes
    spec = importlib.util.spec_from_file_location("scrubber", copy / "scripts" / "scrub-evidence.py")
    scrubber = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scrubber)

    assert scrubber.main([str(folder / "key.txt")]) == 1
    (folder / "key.txt").unlink()
    assert scrubber.main([str(folder / "up.txt")]) == 0
    assert "<public-ip>" in (folder / "up.txt").read_text(encoding="utf-8")

    code, failed, shown = verdict(copy)

    assert code == 0 and not failed, shown

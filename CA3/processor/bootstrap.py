# This program creates the Secrets of the stack, inside the cluster, so that "kubectl apply -k ." is all it takes.
#
# It runs once, as the Job "bootstrap" (see bootstrap/job.yaml), and makes:
#   - random passwords: the MongoDB administrator, one MongoDB user for the processor, one for the REST API, one that only the
#     MongoDB metrics reader (exporter) uses, the REST API login, and the Grafana administrator;
#   - a private certificate authority (CA), and from it the certificates that turn on TLS:
#       Kafka (server), MongoDB (server), the REST API (server), and the producer, the processor and the Kafka metrics reader (Kafka clients).
# Each one is stored as its own Secret (13 in all), so every pod can mount only the Secrets it needs.
#
# What it keeps and what it forgets:
#   - The CA private key exists only in this program's memory. It is never written anywhere, so after the Job ends nobody
#     (no pod, no person) can sign another certificate. To renew the certificates, delete the Secrets and run the Job again.
#   - The program prints which Secrets it made and the fingerprint of the CA certificate. It never prints a password or a key.
#
# What it may do in Kubernetes: create Secrets in its own namespace. Nothing else (see rbac/bootstrap.yaml). It cannot read,
# change or delete a Secret, so a Job that is broken or hijacked cannot read anything that exists already.
#
# It is safe to run again. It first asks the API server, with a "dry run" create (nothing is stored), which of its Secrets exist:
#   all exist    -> nothing to do (a second "make up")
#   none exist   -> make all of them
#   some exist   -> stop with an error: the new certificates would not match the old ones. "make down" removes them all.
#
# It needs only the Python standard library and the "cryptography" package.

import base64
import datetime
import hashlib
import ipaddress
import json
import os
import secrets
import ssl
import string
import sys
import time
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"

# Where the Kubernetes API is, and how to prove who we are. Inside a pod the defaults are right.
# The environment variables exist so that the program can be tried against any cluster (the tests do that).
API = os.environ.get("KUBE_API", "https://kubernetes.default.svc")
TOKEN_FILE = os.environ.get("KUBE_TOKEN_FILE", SA_DIR + "/token")
CA_FILE = os.environ.get("KUBE_CA_FILE", SA_DIR + "/ca.crt")
NAMESPACE_FILE = SA_DIR + "/namespace"

VALID_DAYS = 365
ALPHABET = string.ascii_letters + string.digits

LABELS = {
    "app.kubernetes.io/part-of": "ca3-pipeline",
    "app.kubernetes.io/managed-by": "bootstrap-job",
}


def namespace():
    if os.environ.get("NAMESPACE"):
        return os.environ["NAMESPACE"]
    with open(NAMESPACE_FILE) as f:
        return f.read().strip()


def password():
    # 32 characters of letters and digits: about 190 bits. Letters and digits only, so no program ever has to quote it.
    return "".join(secrets.choice(ALPHABET) for _ in range(32))


# ---------------------------------------------------------------- certificates

def new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def key_pem(key):
    # PKCS#8 without a passphrase: the format that Kafka (Java), MongoDB (OpenSSL) and Python all read.
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def cert_pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def name(common_name):
    return x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ca3-pipeline"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])


def make_ca():
    key = new_key()
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = name("ca3 pipeline CA")
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False, key_encipherment=False,
                          data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_leaf(ca_key, ca_cert, common_name, usage, dns_names=(), ip_addresses=()):
    # usage is "server" or "client": it decides the "extended key usage" of the certificate, so a client certificate
    # cannot be used to run a server and the other way round.
    key = new_key()
    now = datetime.datetime.now(datetime.timezone.utc)
    eku = ExtendedKeyUsageOID.SERVER_AUTH if usage == "server" else ExtendedKeyUsageOID.CLIENT_AUTH

    builder = (
        x509.CertificateBuilder()
        .subject_name(name(common_name))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, key_encipherment=True, key_cert_sign=False, crl_sign=False, content_commitment=False,
                          data_encipherment=False, key_agreement=False, encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
    )

    names = [x509.DNSName(d) for d in dns_names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ip_addresses]
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)

    return key, builder.sign(ca_key, hashes.SHA256())


def service_names(service, ns, extra=()):
    # Every name that a client may use for the Service: the short name, and the longer forms Kubernetes DNS also answers to.
    return [service, f"{service}.{ns}", f"{service}.{ns}.svc", f"{service}.{ns}.svc.cluster.local", *extra]


# ---------------------------------------------------------------- what to make

def build(ns):
    """Returns a list of (secret name, description, {key: bytes}) and a list of lines to print about the certificates."""
    ca_key, ca_cert = make_ca()
    ca = cert_pem(ca_cert)
    report = [f"CA: {ca_cert.subject.rfc4514_string()}  valid until {ca_cert.not_valid_after_utc:%Y-%m-%d}  sha256 {ca_cert.fingerprint(hashes.SHA256()).hex()}"]

    def leaf(label, common_name, usage, dns=(), ips=()):
        key, cert = make_leaf(ca_key, ca_cert, common_name, usage, dns, ips)
        sans = ", ".join(list(dns) + list(ips)) or "(no names)"
        report.append(f"{label}: CN={common_name}  {usage} certificate  names: {sans}")
        return key_pem(key), cert_pem(cert)

    items = []

    # Passwords and logins.
    items.append(("mongo-root", "MongoDB administrator. Only the MongoDB pod mounts it (and the tests that run inside it).",
                  {"username": b"ca3admin", "password": password().encode()}))
    items.append(("mongo-processor", "MongoDB user of the processor: may read and write the database ca3. Mounted by the processor, and by MongoDB to create the user.",
                  {"username": b"processor", "password": password().encode()}))
    items.append(("mongo-restapi", "MongoDB user of the REST API: may only read the database ca3. Mounted by the REST API, and by MongoDB to create the user.",
                  {"username": b"restapi", "password": password().encode()}))
    items.append(("mongo-exporter", "MongoDB user of the metrics reader (mongodb-exporter): may only read monitoring numbers, no flows. Mounted by the exporter, and by MongoDB to create the user.",
                  {"username": b"exporter", "password": password().encode()}))
    items.append(("restapi-login", "Login for the REST API (HTTP basic authentication). Read it with kubectl to call the API.",
                  {"username": b"ca3api", "password": password().encode()}))
    items.append(("grafana-admin", "Administrator login of Grafana (the dashboards). Read it with kubectl to log in.",
                  {"username": b"admin", "password": password().encode()}))

    # The CA certificate: public, every client needs it to check the server's certificate.
    items.append(("pki-ca", "Certificate of the private CA that signed the other certificates (public). The CA private key was not stored.",
                  {"ca.crt": ca}))

    # Kafka: server certificate for the broker. Kafka reads one PEM file with the key and the certificate.
    key, cert = leaf("kafka", "kafka", "server",
                     dns=service_names("kafka", ns, [f"kafka-headless.{ns}.svc.cluster.local", f"kafka-0.kafka-headless.{ns}.svc.cluster.local", "localhost"]),
                     ips=["127.0.0.1"])
    items.append(("kafka-tls", "Server certificate of the Kafka broker, and the CA certificate it uses to check client certificates.",
                  {"keystore.pem": key + cert, "ca.pem": ca}))

    # Kafka clients: the certificate is the login. Kafka accepts only clients whose certificate this CA signed.
    # The "exporter" is the metrics reader that asks Kafka how far behind the processor is (the consumer lag).
    for who in ("producer", "processor", "exporter"):
        key, cert = leaf(f"kafka client {who}", who, "client")
        items.append((f"kafka-client-{who}", f"Client certificate that the {who} shows to Kafka (mutual TLS).",
                      {"tls.crt": cert, "tls.key": key, "ca.crt": ca}))

    # MongoDB: server certificate. mongod reads one PEM file with the certificate and the key.
    key, cert = leaf("mongo", "mongo", "server",
                     dns=service_names("mongo", ns, [f"mongo-headless.{ns}.svc.cluster.local", f"mongo-0.mongo-headless.{ns}.svc.cluster.local", "localhost"]),
                     ips=["127.0.0.1"])
    items.append(("mongo-tls", "Server certificate of MongoDB (certificate and key in one file), and the CA certificate.",
                  {"mongo.pem": cert + key, "ca.pem": ca}))

    # REST API: server certificate. "restapi.ca3.test" is the name that the tests on the administrator's computer use
    # (they connect to a node's address but check the certificate against this name).
    key, cert = leaf("restapi", "restapi", "server", dns=service_names("restapi", ns, ["restapi.ca3.test", "localhost"]), ips=["127.0.0.1"])
    items.append(("restapi-tls", "Server certificate of the REST API.", {"tls.crt": cert, "tls.key": key}))

    return items, report


# ---------------------------------------------------------------- talking to Kubernetes

def read_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def post(ctx, token, ns, body, dry_run):
    """POST one Secret. Returns the HTTP status (201 created, 409 exists). Transient errors are retried a few times."""
    url = f"{API}/api/v1/namespaces/{ns}/secrets" + ("?dryRun=All" if dry_run else "")
    data = json.dumps(body).encode()
    delay = 1

    for attempt in range(1, 7):
        request = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        try:
            with urllib.request.urlopen(request, context=ctx, timeout=20) as response:
                return response.status
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", "replace")

            if error.code == 409:
                return 409

            if error.code in (429, 500, 502, 503, 504) and attempt < 6:
                print(f"  the API server answered {error.code}; trying again in {delay} s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue

            # A real refusal (403 forbidden, 422 invalid, ...): say what the API server said and stop.
            try:
                message = json.loads(text).get("message", text)
            except ValueError:
                message = text

            sys.exit(f"the API server refused {body['metadata']['name']}: HTTP {error.code}: {message}")
        except (urllib.error.URLError, OSError) as error:
            if attempt < 6:
                print(f"  cannot reach the API server ({error}); trying again in {delay} s", flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 16)
                continue

            sys.exit(f"cannot reach the API server at {API}: {error}")

    sys.exit("gave up")


def secret_body(ns, secret_name, description, data):
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": ns,
            "labels": dict(LABELS),
            "annotations": {"description": description},
        },
        "type": "Opaque",
        "data": {key: base64.b64encode(value).decode() for key, value in data.items()},
    }


def main():
    ns = namespace()
    token = read_token()
    ctx = ssl.create_default_context(cafile=CA_FILE)

    print(f"bootstrap: namespace {ns}, API server {API}", flush=True)

    items, report = build(ns)
    bodies = [secret_body(ns, n, d, data) for n, d, data in items]

    # Which of the Secrets exist? A dry-run create changes nothing: 409 means "exists", 201 means "would have been created".
    existing = []
    missing = []

    for body in bodies:
        (existing if post(ctx, token, ns, body, dry_run=True) == 409 else missing).append(body["metadata"]["name"])

    if not missing:
        print(f"bootstrap: all {len(existing)} Secrets exist already. Nothing to do.", flush=True)
        return

    if existing:
        sys.exit("bootstrap: only some of the Secrets exist (" + ", ".join(existing) + "). Missing: " + ", ".join(missing)
                 + ". New certificates would not match the old ones, so nothing was created. Remove them all with: make down   (then: make up)")

    for line in report:
        print("bootstrap: " + line, flush=True)

    for body in bodies:
        status = post(ctx, token, ns, body, dry_run=False)

        if status == 409:
            sys.exit(f"bootstrap: {body['metadata']['name']} appeared while the program was running. Nothing more was created. Remove them all with: make down")

        print(f"bootstrap: created Secret {body['metadata']['name']} ({', '.join(sorted(body['data']))})", flush=True)

    print(f"bootstrap: done. {len(bodies)} Secrets created. The CA private key was not stored.", flush=True)


if __name__ == "__main__":
    main()

# Checks that the container images of the stack that live on ghcr.io can be pulled WITHOUT a login, which is how the cluster pulls them.
#
#   make check-images
#   python3 NLP-pipeline/scripts/check-images.py --registry https://ghcr.io
#
# The workflow .github/workflows/nlp-pipeline-images.yml pushes the three images (nlp-producer, nlp-processor, nlp-restapi) to the GitHub
# Container Registry. A package that GitHub creates is PRIVATE at first, and a private package cannot be pulled by the cluster: the pods would
# stay in ImagePullBackOff. Making a package public is a click on GitHub (README, Step 3). This script asks the registry the way a stranger
# would: it asks for an anonymous token, then for the manifest of each image, and says for each one whether that worked.
#
# The images are the ones named in the manifests of the stack (every "image: ghcr.io/..." line of the YAML files). Nothing is changed anywhere.
# Needs only python3 and an internet connection (not the cluster).

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = re.compile(r"^\s*-?\s*image:\s*(ghcr\.io/[^\s#]+)", re.M)
ACCEPT = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])


# Every distinct "ghcr.io/owner/name:tag" in the YAML files under `root`, sorted.
def images_of(root):
    found = set()

    for path in sorted(Path(root).rglob("*.yaml")):
        if any(part in (".git", ".secrets", ".terraform") for part in path.parts):
            continue

        found.update(IMAGE.findall(path.read_text(encoding="utf-8", errors="replace")))

    return sorted(found)


# "ghcr.io/owner/name:1.0.0" -> ("owner/name", "1.0.0")
def split_image(image):
    name = image.split("/", 1)[1]
    repository, _, tag = name.partition(":")
    return repository, tag or "latest"


def fetch(url, headers=None, timeout=20):
    request = urllib.request.Request(url, headers=headers or {})

    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return answer.status, answer.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


# (True, "linux/amd64, ...") when the image can be pulled anonymously, else (False, the reason).
def check_image(registry, repository, tag):
    token = ""

    try:
        status, body = fetch(registry + "/token?service=ghcr.io&scope=" + urllib.parse.quote("repository:" + repository + ":pull", safe=":/"))

        if status == 200:
            token = json.loads(body).get("token", "")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return False, "could not reach the registry: " + str(error)

    headers = {"Accept": ACCEPT}

    if token:
        headers["Authorization"] = "Bearer " + token

    try:
        status, body = fetch(registry + "/v2/" + repository + "/manifests/" + tag, headers)
    except (urllib.error.URLError, OSError) as error:
        return False, "could not reach the registry: " + str(error)

    if status in (401, 403, 404):
        return False, ("HTTP " + str(status) + ": the package is private, or it was never pushed (or the tag " + tag + " does not exist). "
                       "GitHub > your profile > Packages > " + repository.split("/")[-1] + " > Package settings > Change visibility > Public")

    if status != 200:
        return False, "HTTP " + str(status) + " from the registry"

    try:
        manifest = json.loads(body)
    except ValueError:
        return False, "the registry answered with something that is not a manifest"

    platforms = sorted({m["platform"]["os"] + "/" + m["platform"]["architecture"] for m in manifest.get("manifests", []) if m.get("platform") and m["platform"].get("os") != "unknown"})

    if manifest.get("manifests") and "linux/amd64" not in platforms:
        return False, "pullable, but there is no linux/amd64 image in it (the machines of the cluster are amd64); it has: " + ", ".join(platforms or ["nothing"])

    return True, ", ".join(platforms) if platforms else "a single image"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the ghcr.io images of the stack can be pulled without a login.")
    parser.add_argument("--registry", default="https://ghcr.io", help="the registry to ask (default https://ghcr.io)")
    parser.add_argument("--root", default=str(ROOT), help="the folder whose YAML files name the images (default: NLP-pipeline)")
    args = parser.parse_args(argv)

    images = images_of(args.root)

    if not images:
        print("No image of ghcr.io is named in the YAML files under " + args.root, file=sys.stderr)
        return 1

    print("Asking " + args.registry + " for " + str(len(images)) + " images without a login:\n")
    bad = 0

    for image in images:
        ok, said = check_image(args.registry.rstrip("/"), *split_image(image))
        bad += not ok
        print("  " + ("ok   " if ok else "FAIL ") + image.ljust(52) + said)

    if bad:
        print("\nRESULT: FAIL - " + str(bad) + " of " + str(len(images)) + " images cannot be pulled without a login. The pods that use them would stay in ImagePullBackOff.")
        return 1

    print("\nRESULT: PASS - all " + str(len(images)) + " images can be pulled without a login")
    return 0


if __name__ == "__main__":
    sys.exit(main())

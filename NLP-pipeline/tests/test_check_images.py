# scripts/check-images.py against a small stand-in registry on this computer: a public image, a private one (401), a missing one (404), an
# image that has no amd64 build, and a registry that is not there at all. (The real ghcr.io is not asked from here.)

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PIPELINE

spec = importlib.util.spec_from_file_location("check_images", PIPELINE / "scripts" / "check-images.py")
check_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_images)

INDEX = {"manifests": [{"platform": {"os": "linux", "architecture": "amd64"}}, {"platform": {"os": "unknown", "architecture": "unknown"}}]}
ARM_ONLY = {"manifests": [{"platform": {"os": "linux", "architecture": "arm64"}}]}


class Registry(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/token"):
            return self.reply(200, {"token": "anonymous"})

        known = {"/v2/me/public/manifests/1.0.0": (200, INDEX), "/v2/me/private/manifests/1.0.0": (401, {"errors": [{"code": "UNAUTHORIZED"}]}),
                 "/v2/me/armonly/manifests/1.0.0": (200, ARM_ONLY), "/v2/me/single/manifests/1.0.0": (200, {"layers": []})}
        status, body = known.get(self.path, (404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]}))
        self.reply(status, body)


@pytest.fixture
def registry():
    server = HTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % server.server_port
    server.shutdown()


def test_the_images_of_the_stack_are_found_in_the_yaml_files():
    found = check_images.images_of(PIPELINE)

    assert found and all(image.startswith("ghcr.io/") and ":" in image for image in found)
    assert {check_images.split_image(i)[0].split("/")[1] for i in found} == {"nlp-producer", "nlp-processor", "nlp-restapi"}
    assert {check_images.split_image(i)[1] for i in found} == {(PIPELINE / "VERSION").read_text().strip()}


def test_a_public_image_passes_and_names_its_platforms(registry):
    assert check_images.check_image(registry, "me/public", "1.0.0") == (True, "linux/amd64")
    assert check_images.check_image(registry, "me/single", "1.0.0") == (True, "a single image")


def test_a_private_or_missing_image_is_refused_with_the_way_to_make_it_public(registry):
    ok, said = check_images.check_image(registry, "me/private", "1.0.0")
    assert not ok and "HTTP 401" in said and "Change visibility > Public" in said

    ok, said = check_images.check_image(registry, "me/nothing", "1.0.0")
    assert not ok and "HTTP 404" in said


def test_an_image_without_an_amd64_build_is_refused(registry):
    ok, said = check_images.check_image(registry, "me/armonly", "1.0.0")
    assert not ok and "no linux/amd64" in said and "linux/arm64" in said


def test_a_registry_that_is_not_there_is_reported():
    ok, said = check_images.check_image("http://127.0.0.1:9", "me/public", "1.0.0")
    assert not ok and "could not reach the registry" in said


def test_the_script_fails_when_one_image_is_not_public(registry, tmp_path, capsys):
    (tmp_path / "a.yaml").write_text("spec:\n  containers:\n    - name: a\n      image: ghcr.io/me/public:1.0.0\n    - name: b\n      image: ghcr.io/me/private:1.0.0\n    - image: busybox:1.36\n")

    assert check_images.main(["--registry", registry, "--root", str(tmp_path)]) == 1
    shown = capsys.readouterr().out

    assert "ok   ghcr.io/me/public:1.0.0" in shown and "FAIL ghcr.io/me/private:1.0.0" in shown and "RESULT: FAIL - 1 of 2" in shown and "busybox" not in shown


def test_the_script_passes_when_every_image_is_public(registry, tmp_path, capsys):
    (tmp_path / "a.yaml").write_text("image: ghcr.io/me/public:1.0.0\n")

    assert check_images.main(["--registry", registry, "--root", str(tmp_path)]) == 0
    assert "RESULT: PASS - all 1 images" in capsys.readouterr().out

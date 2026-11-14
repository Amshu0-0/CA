# The workflow that builds the three images (.github/workflows/nlp-pipeline-images.yml) against the files it builds from.
# GitHub's servers and Docker are not available here, so the build itself is not run. What is checked is everything around it that can go
# wrong and would only show on GitHub, after a push: the names, the files each Dockerfile copies and each .dockerignore lets through,
# the paths that start the workflow, the shell code of the steps (run with bash), and the Python code of the two "try the image" steps
# (run here against a stand-in bundle).

import importlib.util
import re
import subprocess

import pytest
import yaml

import engine
from conftest import PIPELINE, REPO
from test_real_model import FILES, FakeDrain, make_bundle, template_texts

WORKFLOW = REPO / ".github" / "workflows" / "nlp-pipeline-images.yml"
COMPONENTS = ("producer", "processor", "restapi")
BUNDLE = "NLP-pipeline/bundle/"

spec = importlib.util.spec_from_file_location("check_images", PIPELINE / "scripts" / "check-images.py")
check_images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_images)


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def step(start):
    found = [s for s in workflow()["jobs"]["build"]["steps"] if s["name"].startswith(start)]
    assert len(found) == 1, start
    return found[0]


# The lines "!path" of a .dockerignore: the only things that reach the image.
def allowed(component):
    lines = (PIPELINE / component / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    return [line[1:].strip() for line in lines if line.startswith("!")]


# The sources of every COPY line of a Dockerfile (the last word is where the files go). ARG defaults are filled in.
def copied(component):
    text = (PIPELINE / component / "Dockerfile").read_text(encoding="utf-8")
    args = dict(re.findall(r"^ARG (\w+)=(\S+)", text, re.M))
    sources = []

    for line in text.splitlines():
        if line.startswith("COPY "):
            words = [re.sub(r"\$\{(\w+)\}", lambda m: args[m.group(1)], w) for w in line.split()[1:] if not w.startswith("--")]
            sources += words[:-1]

    return sources


def related(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def bash(code, cwd, **env):
    return subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", code], cwd=cwd, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", **env})


def run_of(name, component):
    return step(name)["run"].replace("${{ matrix.component }}", component)


def trial_code(name):
    run = step(name)["run"]
    match = re.search(r'python -c "(.*)"\s*$', run, re.S)

    assert match, "the step must end with  python -c \"...\""
    return match.group(1), run


# ---------------------------------------------------------------- names and files

def test_it_builds_the_three_images_that_the_manifests_name():
    job = workflow()["jobs"]["build"]
    build = step("Build ")["with"]

    assert tuple(job["strategy"]["matrix"]["component"]) == COMPONENTS
    assert build["context"] == "." and build["file"] == "NLP-pipeline/${{ matrix.component }}/Dockerfile"
    assert build["platforms"] == "linux/amd64"  # the machines of the cluster; CA3's arm64 build would install PyTorch under emulation
    assert all((PIPELINE / c / "Dockerfile").is_file() and (PIPELINE / c / "Dockerfile.dockerignore").is_file() for c in COMPONENTS)
    assert {check_images.split_image(i)[0].split("/")[1] for i in check_images.images_of(PIPELINE)} == {"nlp-" + c for c in COMPONENTS}


@pytest.mark.parametrize("component", COMPONENTS)
def test_the_image_name_and_version_are_the_ones_the_manifests_pull(component, tmp_path):
    (tmp_path / "NLP-pipeline").mkdir()
    (tmp_path / "NLP-pipeline" / "VERSION").write_text((PIPELINE / "VERSION").read_text(), encoding="utf-8")
    version = (PIPELINE / "VERSION").read_text().strip()
    pulled = [i for i in check_images.images_of(PIPELINE) if i.split("/")[2] == "nlp-" + component + ":" + version]
    assert len(pulled) == 1
    owner = pulled[0].split("/")[1]
    out = tmp_path / "out"

    # GitHub gives the owner as it is spelled on GitHub; the registry wants lowercase
    done = bash(run_of("Work out the image name", component), tmp_path, GITHUB_REPOSITORY_OWNER=owner.upper(), GITHUB_OUTPUT=str(out))
    values = dict(line.split("=", 1) for line in out.read_text().splitlines())

    assert done.returncode == 0, done.stderr
    assert values["image"] + ":" + values["version"] == pulled[0]


@pytest.mark.parametrize("component", ("producer", "processor"))
def test_the_check_before_the_build_asks_for_exactly_the_files_the_dockerfile_takes(component, tmp_path):
    wanted = [a.split("/")[-1] for a in allowed(component) if a.startswith(BUNDLE)]
    assert wanted
    bundle = tmp_path / "NLP-pipeline" / "bundle"
    bundle.mkdir(parents=True)

    for name in wanted:
        (bundle / name).write_text("x")

    assert bash(run_of("Check that the files", component), tmp_path).returncode == 0

    for name in wanted:
        (bundle / name).unlink()
        done = bash(run_of("Check that the files", component), tmp_path)
        assert done.returncode == 1 and "::error title=a file of the bundle is missing::NLP-pipeline/bundle/" + name + " " in done.stdout and "make export-bundle" in done.stdout

        (bundle / name).write_text("")  # a file with nothing in it is as bad as none
        assert bash(run_of("Check that the files", component), tmp_path).returncode == 1
        (bundle / name).write_text("x")


def test_the_restapi_image_needs_nothing_from_the_bundle(tmp_path):
    assert not [a for a in allowed("restapi") if a.startswith("NLP-pipeline/bundle")]
    assert bash(run_of("Check that the files", "restapi"), tmp_path).returncode == 0


def test_the_export_script_makes_every_file_the_images_take():
    script = (PIPELINE / "scripts" / "export-nlp-bundle.py").read_text(encoding="utf-8")

    for component in COMPONENTS:
        for entry in allowed(component):
            if entry.startswith(BUNDLE):
                assert '"' + entry[len(BUNDLE):] + '"' in script, entry


@pytest.mark.parametrize("component", COMPONENTS)
def test_each_dockerfile_copies_only_what_its_dockerignore_lets_through_and_nothing_else_is_let_through(component):
    sources, entries = copied(component), allowed(component)

    for source in sources:
        assert any(related(source, e) for e in entries), source + " is copied by the Dockerfile but the .dockerignore leaves it out"

        if not source.startswith(BUNDLE.rstrip("/")):  # the bundle is made on the laptop by  make export-bundle
            assert (REPO / source).exists(), source + " does not exist"

    for entry in entries:
        assert any(related(entry, s) for s in sources), entry + " is let through by the .dockerignore but no COPY line takes it"
        assert entry.startswith(BUNDLE) or (REPO / entry).exists(), entry + " does not exist"


def test_every_file_of_an_image_starts_the_workflow_when_it_changes():
    patterns = workflow()[True]["push"]["paths"]
    # GitHub: ** matches anything including "/", * matches anything but "/"
    matchers = [re.compile("^" + re.escape(p).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") + "$") for p in patterns]

    for component in COMPONENTS:
        for entry in allowed(component):
            if entry == "NLP/README.md":
                continue  # only read by pip while it installs the package (pyproject.toml names it as the readme); no change to it changes the image

            probe = entry if (REPO / entry).is_file() or entry.startswith(BUNDLE) else entry + "/anything.py"
            assert any(m.match(probe) for m in matchers), probe + " is not among the paths that start the workflow"

    assert any(m.match(".github/workflows/nlp-pipeline-images.yml") for m in matchers)
    assert (PIPELINE / "VERSION").is_file() and any(m.match("NLP-pipeline/VERSION") for m in matchers)


# ---------------------------------------------------------------- the two "try the image" steps

@pytest.mark.parametrize("name", ("Try the processor image", "Try the producer image"))
def test_the_python_of_a_trial_step_survives_the_shell(name):
    code, run = trial_code(name)

    # the code sits inside "...": nothing in it may be something that the shell would change
    assert not re.search(r'["$`\\!]', code), "a character in the code would be changed by the shell"
    assert run.count('"') == 4 and run.startswith("docker run --rm ")
    compile(code, name, "exec")


@pytest.mark.skipif(not all(p.exists() for p in FILES.values()), reason="the NLP project's results are not in this checkout")
def test_the_processor_trial_loads_the_bundle_and_prints_what_it_found(tmp_path, monkeypatch, capsys):
    code, _run = trial_code("Try the processor image")
    bundle = tmp_path / "bundle"
    make_bundle(bundle, None)
    texts = template_texts()
    monkeypatch.setattr(engine.TemplateExtractor, "load", staticmethod(lambda path: FakeDrain(texts)))

    exec(compile(code.replace("/bundle", str(bundle)), "trial", "exec"), {})
    shown = capsys.readouterr().out

    assert "the models load:" in shown and "LogBERT keys" in shown and "keyword rules" in shown and "saved LLM decisions" in shown and "g = 8" in shown
    assert "a test line:" in shown


def test_the_processor_trial_fails_when_the_models_do_not_belong_together(tmp_path, monkeypatch):
    # an image whose bundle is broken must not be pushed: the trial has to end with an error, which stops the job
    code, _run = trial_code("Try the processor image")
    monkeypatch.setattr(engine.Detectors, "load", classmethod(lambda cls, directory: (_ for _ in ()).throw(engine.BundleError("drain.pkl does not belong to the model"))))

    with pytest.raises(engine.BundleError):
        exec(compile(code, "trial", "exec"), {})


def test_the_producer_trial_reads_the_replay_file_of_the_working_folder(tmp_path, monkeypatch, capsys):
    code, _run = trial_code("Try the producer image")
    (tmp_path / "bgl-replay.log").write_text("".join("- 1117838570 2005.06.03 R02-M1-N0 2005-06-03-15.42.50.675872 R02-M1-N0 RAS KERNEL INFO line %d\n" % i for i in range(300)))
    monkeypatch.chdir(tmp_path)

    exec(compile(code, "trial", "exec"), {})

    assert "the replay file has 300 lines" in capsys.readouterr().out


def test_the_producer_trial_fails_on_a_replay_file_that_is_nearly_empty(tmp_path, monkeypatch):
    code, _run = trial_code("Try the producer image")
    (tmp_path / "bgl-replay.log").write_text("- 1117838570 2005.06.03 R02-M1-N0 2005-06-03-15.42.50.675872 R02-M1-N0 RAS KERNEL INFO one line\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(AssertionError):
        exec(compile(code, "trial", "exec"), {})

# Removes from saved evidence files what must not be published, so that the files can be committed to a public repository.
#
#   make scrub FILES="evidence/step10-cluster-up.txt evidence/step10-up.txt"
#   python3 scripts/scrub-evidence.py evidence/step10-*.txt
#
# It rewrites each file in place and prints what it changed. It replaces:
#   a public IPv4 address (one that is not private, reserved for examples, or part of the dataset)   with  <public-ip>
#   an EC2 host name (it contains the machine's public address)                                      with  <ec2-host>
#   a 12-digit number (an AWS account number looks like this)                                        with  <12-digit-number>
#   a path into a home folder (/Users/name, /home/name, or this computer's home folder)              with  /Users/<user>, /home/<user> or ~
# It refuses to touch a file that holds an access key or a private key: such a file must be deleted, not scrubbed.
# "make verify" (section 9) runs the same tests on the evidence folder, so a value that this program misses is reported there.
# Running it twice changes nothing the second time.

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = re.compile(r"^(10\.|127\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|0\.0\.0\.0$|255\.|192\.0\.2\.|198\.51\.100\.|203\.0\.113\.)")
IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\.?\d)")
EC2 = re.compile(r"\bec2-(?:\d{1,3}-){3}\d{1,3}(?:\.[A-Za-z0-9.-]+)?")
TWELVE = re.compile(r"(?<!\d)\d{12}(?!\d)")
SECRET = re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----")
HOME = str(Path.home())

# The dataset is public and full of addresses; they are data, not private.
dataset = set()
for csv_file in sorted((ROOT / "producer").glob("*.csv")):
    dataset |= {m.group(0) for m in IPV4.finditer(csv_file.read_text(errors="replace"))}


def scrub(text):
    counts = {"public IP addresses": 0, "EC2 host names": 0, "12-digit numbers": 0, "home folder paths": 0}

    def ip(m):
        if all(int(g) <= 255 for g in m.groups()) and not PRIVATE.match(m.group(0)) and m.group(0) not in dataset:
            counts["public IP addresses"] += 1
            return "<public-ip>"
        return m.group(0)

    text = EC2.sub(lambda m: counts.__setitem__("EC2 host names", counts["EC2 host names"] + 1) or "<ec2-host>", text)
    text = IPV4.sub(ip, text)
    text = TWELVE.sub(lambda m: counts.__setitem__("12-digit numbers", counts["12-digit numbers"] + 1) or "<12-digit-number>", text)

    for pattern, replacement in ((r"/Users/[\w.-]+", "/Users/<user>"), (r"/home/[\w.-]+", "/home/<user>")):
        text, n = re.subn(pattern, replacement, text)
        counts["home folder paths"] += n

    if len(HOME) > 6 and HOME in text:
        counts["home folder paths"] += text.count(HOME)
        text = text.replace(HOME, "~")

    return text, counts


if len(sys.argv) < 2:
    sys.exit("usage: python3 scripts/scrub-evidence.py FILE [FILE ...]")

refused, changed = [], 0

for name in sys.argv[1:]:
    path = Path(name)

    if not path.is_file():
        print(name + ": not a file, skipped")
        continue

    original = path.read_text(encoding="utf-8", errors="replace")

    if SECRET.search(original):
        refused.append(name)
        print(name + ": holds an access key or a private key. NOT scrubbed. Delete this file.")
        continue

    cleaned, counts = scrub(original)

    if cleaned != original:
        path.write_text(cleaned, encoding="utf-8")
        changed += 1
        print(name + ": changed (" + ", ".join(str(v) + " " + k for k, v in counts.items() if v) + ")")
    else:
        print(name + ": nothing to change")

print("\n" + str(len(sys.argv) - 1 - len(refused)) + " files looked at, " + str(changed) + " changed" + (", " + str(len(refused)) + " refused" if refused else ""))
sys.exit(1 if refused else 0)

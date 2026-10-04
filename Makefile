IMAGE ?= ghcr.io/nosini/pakseal
TAG ?= latest
PYTHON ?= python3

.PHONY: test run run-fake image pin

test:
	$(PYTHON) -m unittest discover -s tests -t .

# Run against the cpak on this machine. Needs a cpak with `cpak permissions`.
run:
	$(PYTHON) -m pakseal

# Run against sample data, without cpak.
run-fake:
	PAKSEAL_CPAK=tools/fake-cpak PAKSEAL_ORIGIN=github.com/nosini/pakseal $(PYTHON) -m pakseal

image:
	podman build -t $(IMAGE):$(TAG) -f Containerfile .

# Push the image and write its digest into cpak.json, which manifest v3 requires.
pin:
	podman push --digestfile .digest $(IMAGE):$(TAG)
	$(PYTHON) -c 'import json; d = open(".digest").read().strip(); m = json.load(open("cpak.json")); m["image"] = "$(IMAGE)@" + d; open("cpak.json", "w").write(json.dumps(m, indent=2) + "\n")'
	rm -f .digest

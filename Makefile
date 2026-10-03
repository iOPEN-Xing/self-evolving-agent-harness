PYTHON ?= .venv/bin/python

.PHONY: verify test doctor
verify:
	$(PYTHON) scripts/verify.py

test:
	$(PYTHON) -m pytest -q

doctor:
	$(PYTHON) scripts/doctor.py

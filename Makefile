# Aftercare - task interface.
#
# Every target here delegates to tasks.py, which is the real implementation. That keeps
# one source of truth and means the project works on a machine without make (Windows,
# most notably) by running `python tasks.py <target>` instead.

PY ?= python

.DEFAULT_GOAL := help
.PHONY: help setup doctor seed demo smoke publish-playbooks export-audit dev verify \
        test test-adv test-policy fmt deploy destroy clean

help:            ## show this help
	@$(PY) tasks.py help

setup:           ## install Python and Node dependencies
	@$(PY) tasks.py setup

doctor:          ## check the toolchain and print the active mode
	@$(PY) tasks.py doctor

seed:            ## build the fictional estate
	@$(PY) tasks.py seed

demo:            ## replay six simulated weeks at 400x
	@$(PY) tasks.py demo

smoke:           ## end-to-end assertions
	@$(PY) tasks.py smoke

publish-playbooks: ## publish institution playbooks to the registry
	@$(PY) tasks.py publish-playbooks

export-audit:    ## export the court-facing estate record
	@$(PY) tasks.py export-audit

dev:             ## run the API (and dashboard instructions)
	@$(PY) tasks.py dev

verify:          ## assert the deployment or the local loop works
	@$(PY) tasks.py verify

test:            ## unit and contract tests
	@$(PY) tasks.py test

test-adv:        ## the 40-payload adversarial suite
	@$(PY) tasks.py test-adv

test-policy:     ## prove no outbound path bypasses human approval
	@$(PY) tasks.py test-policy

fmt:             ## format and lint
	@$(PY) tasks.py fmt

deploy:          ## deploy to Cloud Run
	@$(PY) tasks.py deploy

destroy:         ## tear down all cloud resources
	@$(PY) tasks.py destroy

clean:           ## delete local state
	@$(PY) tasks.py clean

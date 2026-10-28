.PHONY: eval eval-live eval-gates build up down logs sync restart deploy deploy-push gitops-setup creature-types

build:
	docker compose build

# LEGACY, do not use: builds on Tower and pushes to the local registry,
# bypassing the Portainer stack. See docs/GITOPS.md.
deploy:
	./scripts/deploy_tower.sh

# Prod deploy: trigger Portainer stack 124's webhook so it rebuilds from
# GitHub main. Run after the PR is merged and the version bump has landed.
# See docs/GITOPS.md.
deploy-push:
	curl -fsS -X POST http://192.168.50.3:9009/api/stacks/webhooks/$$(ssh tower cat /mnt/user/appdata/commander-lab/portainer-webhook) && echo "webhook triggered"

# LEGACY, do not use: Gitea-based setup; stack 124 tracks GitHub (see docs/GITOPS.md).
gitops-setup:
	./scripts/setup_portainer_gitops.sh

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

restart: down up

sync:
	docker compose exec app python -m src.server.workers.sync

# Deck Genre: refresh the Kindred autocomplete's creature-type list from Scryfall into Redis.
# Optional: the app falls back to the snapshot in deck_genre.py.
creature-types:
	docker compose exec app python scripts/refresh_creature_types.py

# Deck reviewer evals. eval is a deterministic offline pipeline smoke using the
# same run_review entrypoint. eval-live runs every golden deck through run_review with
# real model calls (COSTS MONEY): needs REVIEW_MODEL, REVIEW_JUDGE_MODEL and the provider
# API key(s) in the environment or the secrets dir; see tests/review_evals/run_eval.py.
# Example: make eval-live EVAL_ARGS="--decks saffi-rumors --tier openrouter:anthropic/claude-sonnet-5.5"
eval-gates:
	python -m tests.review_evals.run_gates --self-test

eval:
	python -m tests.review_evals.run_eval --offline

eval-live:
	python -m tests.review_evals.run_eval --live $(EVAL_ARGS)

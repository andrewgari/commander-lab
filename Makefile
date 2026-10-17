.PHONY: build up down logs sync restart deploy deploy-push gitops-setup

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
	docker compose exec app python sync.py

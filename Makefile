.PHONY: build up down logs sync restart deploy deploy-push gitops-setup

build:
	docker compose build

# Legacy manual deploy path: builds locally-on-Tower and pushes to the local registry.
deploy:
	./scripts/deploy_tower.sh

# Preferred path: push to Gitea and let Portainer's Git-stack webhook rebuild
# and redeploy automatically. See docs/GITOPS.md.
deploy-push:
	git push gitea main

# One-time setup of the Portainer Git stack + Gitea webhook (see docs/GITOPS.md).
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

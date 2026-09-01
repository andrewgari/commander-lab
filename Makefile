.PHONY: build up down logs sync restart deploy

build:
	docker compose build

deploy:
	./scripts/deploy_tower.sh

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

restart: down up

sync:
	docker compose exec app python sync.py

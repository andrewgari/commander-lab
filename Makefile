.PHONY: build up down logs sync restart

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

restart: down up

sync:
	docker compose exec app python sync.py

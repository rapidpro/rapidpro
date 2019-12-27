migrate:
	docker-compose -f docker-compose.yml run rapidpro python manage.py migrate

########## DEV COMMANDS ##########

build.dev:
	docker-compose -f docker-compose.dev.yml build

migrate.dev:
	docker-compose -f docker-compose.dev.yml run rapidpro python manage.py migrate

dev:
	make migrate.dev
	docker-compose -f docker-compose.dev.yml run rapidpro python manage.py runserver 0.0.0.0:8000
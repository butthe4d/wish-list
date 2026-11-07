.PHONY: test i18n-update i18n-compile dev-env run
test:
	.venv/bin/pytest -q

i18n-update:
	.venv/bin/pybabel extract -F babel.cfg -k lazy_gettext -o wishlist/translations/messages.pot . && .venv/bin/pybabel update -i wishlist/translations/messages.pot -d wishlist/translations

i18n-compile:
	.venv/bin/pybabel compile -d wishlist/translations

# Local dev server: `make dev-env` once, then `make run`.
PORT ?= 8000

dev-env:
	@if [ -f dev.env ]; then echo "dev.env already exists, leaving it alone"; else \
	  ip=$$(hostname -I 2>/dev/null | awk '{print $$1}'); \
	  key=$$(python3 -c "import secrets; print(secrets.token_urlsafe(48))"); \
	  sed -e "s|^export BASE_URL=.*|export BASE_URL=http://$${ip:-localhost}:$(PORT)|" \
	      -e "s|^export SECRET_KEY=.*|export SECRET_KEY=$$key|" dev.env.example > dev.env; \
	  echo "Wrote dev.env (BASE_URL=http://$${ip:-localhost}:$(PORT)) - edit ADMIN_EMAIL/ADMIN_NAME if you like"; fi

run: i18n-compile
	@[ -f dev.env ] || { echo "No dev.env - run 'make dev-env' first"; exit 1; }
	. ./dev.env && .venv/bin/python -m wishlist.bootstrap && \
	  .venv/bin/flask --app "wishlist:create_app()" run --host 0.0.0.0 --port $(PORT)

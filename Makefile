.PHONY: test i18n-update i18n-compile
test:
	.venv/bin/pytest -q

i18n-update:
	.venv/bin/pybabel extract -F babel.cfg -k _l -o wishlist/translations/messages.pot . && .venv/bin/pybabel update -i wishlist/translations/messages.pot -d wishlist/translations

i18n-compile:
	.venv/bin/pybabel compile -d wishlist/translations

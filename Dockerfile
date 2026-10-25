FROM python:3.14-slim AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"
WORKDIR /src
COPY pyproject.toml babel.cfg ./
COPY wishlist ./wishlist
# Compiled .mo files are gitignored; compile them before installing so they are packaged.
RUN pip install babel \
 && pybabel compile -d wishlist/translations \
 && pip install . \
 && pip uninstall -y pip

FROM python:3.14-slim
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/wishlist.db
RUN useradd --uid 1000 --create-home app && mkdir /data && chown app:app /data
COPY --from=build /venv /venv
COPY --chmod=755 docker/entrypoint.sh /app/docker/entrypoint.sh
WORKDIR /app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]

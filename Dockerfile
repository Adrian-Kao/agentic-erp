FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.9
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY data/mappings ./data/mappings
COPY data/scenarios ./data/scenarios
RUN groupadd --gid 10001 erp && useradd --uid 10001 --gid erp erp \
    && install -d -o erp -g erp /app/data
USER erp
CMD ["uvicorn", "erp_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

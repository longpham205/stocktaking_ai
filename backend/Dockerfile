# The API: FastAPI + the recognition engine, one process. The same image runs compose's `migrate`
# job (`alembic upgrade head`). Dependencies come from the lock only.
#
#   TORCH_VARIANT=none   (default) light install: mock/fake recognizer, no torch. Small image.
#   TORCH_VARIANT=cpu    + the ML stack with CPU torch wheels
#   TORCH_VARIANT=cu128  + the ML stack with CUDA 12.8 torch wheels (docker-compose.gpu.yml)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG TORCH_VARIANT=none

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    LOG_FORMAT=json

# libzbar0: pyzbar (barcode plugin); libglib2.0-0, libgl1: OpenCV
RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$TORCH_VARIANT" = "none" ]; then \
        uv sync --frozen --no-dev; \
    else \
        uv sync --frozen --no-dev --extra ml \
        && uv pip install --python /opt/venv/bin/python --reinstall torch torchvision \
             --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}"; \
    fi

COPY alembic.ini ./
COPY migrations ./migrations
COPY entrypoints ./entrypoints
COPY engine ./engine
COPY configs ./configs
COPY scripts ./scripts
COPY app ./app

EXPOSE 8000
# one worker: the model lives in this process's memory/GPU
CMD ["uvicorn", "entrypoints.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

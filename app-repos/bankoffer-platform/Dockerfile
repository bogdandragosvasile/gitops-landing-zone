FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser
COPY --from=builder /install /usr/local
COPY services/api/ /app/services/api/
COPY services/worker/__init__.py /app/services/worker/__init__.py
COPY services/worker/profiler.py /app/services/worker/profiler.py
COPY services/worker/scorer.py /app/services/worker/scorer.py
COPY services/worker/ranker.py /app/services/worker/ranker.py
COPY services/__init__.py /app/services/__init__.py
COPY scripts/ /app/scripts/
COPY AI_Hackathon_Product_Offering_Engine_Dataset_v1.xlsx /app/
COPY ["Consent_Checkbox_Texts_Audit_Ready 1.xlsx", "/app/"]
COPY presentation.html /app/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app
EXPOSE 8000
USER appuser
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

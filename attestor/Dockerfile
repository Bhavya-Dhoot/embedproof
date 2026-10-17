# EmbedProof attestor. Build from the REPOSITORY ROOT (the image also needs model/, deployments/, data/):
#   docker build -f attestor/Dockerfile -t embedproof-attestor .
#   docker run --rm -p 8000:8000 -v attestor_data:/data -e CHAIN_ID=... -e RPC_URL=... -e ATTESTOR_PRIVATE_KEY=... embedproof-attestor
#
# ML-DSA-44 backend: pqcrypto (PQClean) by default. Build with --build-arg WITH_LIBOQS=1 to also compile
# liboqs + liboqs-python, which then takes precedence (backend label "liboqs"); if that build step fails the
# image build fails, so leave it at 0 unless you have checked it in your environment.
FROM python:3.11-slim

ARG WITH_LIBOQS=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DATA_DIR=/data \
    DEPLOYMENTS=/app/deployments \
    OQS_INSTALL_PATH=/opt/oqs

WORKDIR /app

COPY attestor/requirements.txt attestor/requirements.txt
COPY model/requirements.txt model/requirements.txt
RUN pip install --no-cache-dir -r attestor/requirements.txt -r model/requirements.txt

RUN if [ "$WITH_LIBOQS" = "1" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends build-essential cmake git ninja-build libssl-dev \
      && rm -rf /var/lib/apt/lists/* \
      && pip install --no-cache-dir "git+https://github.com/open-quantum-safe/liboqs-python@0.12.0" \
      && python -c "import oqs; assert 'ML-DSA-44' in oqs.get_enabled_sig_mechanisms(), 'liboqs lacks ML-DSA-44'"; \
    fi

COPY attestor attestor
COPY model model
COPY data data
COPY deployments deployments

RUN useradd --create-home --uid 10001 attestor && mkdir -p /data && chown -R attestor /data
USER attestor
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "attestor.app:app", "--host", "0.0.0.0", "--port", "8000"]

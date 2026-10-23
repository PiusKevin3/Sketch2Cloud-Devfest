# Sketch2Cloud backend. This image is deliberately heavy: the agent pipeline
# shells out to `npm` (build verification) and `gcloud` (Cloud Run deploy), so
# both toolchains have to be present alongside Python.
FROM google/cloud-sdk:slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt \
 || pip3 install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]

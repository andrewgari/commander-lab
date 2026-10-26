FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Local embedding model for pillar fit (AN-3, docs/DECK_IDENTITY.md):
# bge-small-en-v1.5, int8 ONNX (~34 MB), pinned revision + sha256. Its own
# layer, so it's cached across app rebuilds; the app never needs the network
# to embed. CPU only (onnxruntime), no torch/GPU.
ENV EMBEDDINGS_MODEL_DIR=/opt/models/bge-small-en-v1.5
COPY scripts/fetch_embedding_model.py /tmp/fetch_embedding_model.py
RUN python /tmp/fetch_embedding_model.py && rm /tmp/fetch_embedding_model.py

COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.server.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

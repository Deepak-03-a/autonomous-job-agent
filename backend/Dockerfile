FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./requirements.txt
# Install the CPU wheel explicitly before sentence-transformers so pip does not
# select CUDA libraries on the CPU-only Linux host.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==2.5.1+cpu"
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./
COPY frontend ./frontend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU 전용 torch/torchvision 먼저 설치 (CUDA 제외 → 이미지 ~2GB 절감)
RUN pip install --no-cache-dir \
    "torch>=2.1,<3.0" \
    "torchvision>=0.16,<1.0" \
    --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "ui/main_ui.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]

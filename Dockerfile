FROM python:3.12-slim

WORKDIR /app

# 系统依赖（curl_cffi 需要）
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libcurl4-openssl-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY *.py .

# 数据目录
RUN mkdir -p /app/data

VOLUME ["/app/data"]

# 默认运行挖矿
CMD ["python", "-u", "mine.py"]

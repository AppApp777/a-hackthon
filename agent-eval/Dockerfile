FROM python:3.11-slim

# 不缓冲 Python 输出，方便 docker logs 实时查看
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 先复制依赖清单，利用 Docker 缓存层——代码改了不用重装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制全部代码
COPY . .

# 确保 traces/ 目录存在（dashboard 空目录也能启动）
RUN mkdir -p traces

EXPOSE 8765

# 默认启动仪表盘（演示模式，从 traces/ 读取已有评测数据）
CMD ["python", "dashboard.py"]

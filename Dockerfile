FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY lab ./lab
RUN useradd --uid 10001 --create-home lab && mkdir /data && chown lab:lab /data
USER lab
ENV DATA_DIR=/data PYTHONUNBUFFERED=1
CMD ["uvicorn", "lab.harness:app", "--host", "0.0.0.0", "--port", "8000"]

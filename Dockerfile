FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY jarvis ./jarvis
ENV DB_PATH=/data/jarvis.db
EXPOSE 8080
CMD ["uvicorn", "jarvis.main:app", "--host", "0.0.0.0", "--port", "8080"]

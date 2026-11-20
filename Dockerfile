FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY desk desk
CMD ["python", "-m", "uvicorn", "desk.api:app", "--host", "0.0.0.0", "--port", "8000"]

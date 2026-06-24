FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY taskbound_demo.py task_registry.py admin_ui.py user_ui.py api.py .
ENTRYPOINT ["python", "taskbound_demo.py"]

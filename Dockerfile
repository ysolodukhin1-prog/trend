FROM python:3.12.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/ozon_category_dashboard:/workspace/ozon_category_dashboard/health_check_runtime

WORKDIR /workspace/ozon_category_dashboard
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY . /workspace

USER 1001:1001
CMD ["sh", "-c", "python -u scripts/bootstrap_vps_schema.py && exec python -u pulse_vps_admin.py"]

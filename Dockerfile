FROM runpod/worker-comfyui:5.8.6-base

RUN pip install --no-cache-dir runpod requests

COPY workflow_templates/ /workflow_templates/
COPY handler.py /handler.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]

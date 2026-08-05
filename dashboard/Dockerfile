# Fargate runs linux/amd64 — build with:  docker build --platform linux/amd64 ...
# (on Apple Silicon, omitting the platform yields an arm64 image that fails to
#  start on Fargate with an exec-format error.)
FROM python:3.12-slim

# System deps kept minimal; slim image + no build tools needed for these wheels.
WORKDIR /app

# Install deps first (own layer) so code changes don't re-run pip every build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app.py .

# Streamlit serves on 8501
EXPOSE 8501

# Bind to 0.0.0.0 so the container is reachable from outside; disable Streamlit's
# usage stats and CORS/XSRF fuss that trip up containerized/behind-proxy runs.
ENV STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# Simple healthcheck Fargate/ALB can use later
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py"]
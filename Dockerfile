FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY eval eval
# CORS/XSRF off because the service sits behind Cloud Run / IAM; Streamlit websockets break behind the proxy otherwise.
CMD exec python -m streamlit run app/ui.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false

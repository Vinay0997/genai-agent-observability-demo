FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY eval eval
# UI=streamlit runs the Streamlit front end; default is the FastAPI service (uvicorn).
CMD if [ "$UI" = "streamlit" ]; then exec python -m streamlit run app/ui.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true; else exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}; fi

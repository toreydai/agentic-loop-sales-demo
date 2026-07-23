FROM python:3.12-slim

WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/agent.py .
COPY data/ ./data/

RUN useradd --create-home --shell /bin/false agentuser && chown -R agentuser /app
USER agentuser

EXPOSE 8080

CMD ["opentelemetry-instrument", "python3", "agent.py"]

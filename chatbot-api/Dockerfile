FROM python:3.11-slim

WORKDIR /app

COPY chatbot-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the SDK from the sibling directory in editable mode.
COPY llm-sdk /opt/llm-sdk
RUN pip install --no-cache-dir /opt/llm-sdk

COPY chatbot-api /app

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

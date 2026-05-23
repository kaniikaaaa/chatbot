FROM python:3.11-slim
WORKDIR /app
COPY chatbot-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY llm-sdk /opt/llm-sdk
RUN pip install --no-cache-dir /opt/llm-sdk
COPY chatbot-api /app
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

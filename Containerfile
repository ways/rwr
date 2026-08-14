FROM python:3.13-alpine
WORKDIR /app
COPY index.html server.py ./
COPY sounds/ ./sounds/
COPY fonts/ ./fonts/
EXPOSE 8000
CMD ["python3", "server.py", "8000", "0.0.0.0"]

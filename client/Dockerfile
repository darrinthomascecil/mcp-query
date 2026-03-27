FROM python:3.12-slim

RUN groupadd -r -g 10001 app && useradd -r -u 10001 -g app -d /app -s /sbin/nologin app

WORKDIR /app
COPY app/ .

USER 10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

ENTRYPOINT ["python", "server.py"]

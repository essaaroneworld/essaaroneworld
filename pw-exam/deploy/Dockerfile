# PW Batch Online Examination System — container image (standard library only, no pip installs).
# Build from the pw-exam directory:  docker build -f deploy/Dockerfile -t pwexam .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PWEXAM_HOST=0.0.0.0 \
    PWEXAM_PORT=8080 \
    PWEXAM_DATA=/data/pwexam.db

RUN useradd --create-home --uid 10001 pwexam && mkdir /data && chown pwexam /data
WORKDIR /app
COPY pwexam ./pwexam
COPY web ./web

USER pwexam
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)" || exit 1
CMD ["python", "-m", "pwexam", "--no-prompt"]

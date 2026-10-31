# Python 3.11: numpy 1.24 (requirements.txt) has no packages for 3.12 or newer
FROM python:3.11-slim

# tzdata: so the timestamps in the results and the CSV use the TZ set in docker-compose.yml
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# unbuffered: the new admin code printed on the first start shows up in the container log right away
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
COPY static static
COPY templates templates

# data/ (results, admin code) is a volume on the NAS, see docker-compose.yml.
# One process only: the rounds and open vote streams live in memory.
EXPOSE 8080
CMD ["python", "app.py"]

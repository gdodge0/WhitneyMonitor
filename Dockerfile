# syntax=docker/dockerfile:1

FROM python:3.12.7-bookworm

WORKDIR /python-docker

ENV PYTHONUNBUFFERED=1

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

CMD ["python3", "monitor.py"]
FROM golang:1.26.10-bookworm AS native
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY build_native.py ./
COPY native/ ./native/
ENV GOMAXPROCS=1 GOMEMLIMIT=192MiB GOGC=30
RUN python3 build_native.py /build/messenger-bridge

FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata && rm -rf /var/lib/apt/lists/*
COPY install_bridge.py ./
COPY --from=native /build/messenger-bridge /build/messenger-bridge
RUN python install_bridge.py /usr/local/bin/messenger-bridge /build/messenger-bridge && rm -rf /build
COPY bridge_rpc.py event_store.py logger.py view_messages.py media_saver.py telegram_relay.py telegram_format.py telegram_i18n.py conversation_filters.py telegram_topics.py identities.py contact_names.py retention.py check_telegram_topics.py ./
COPY LICENSE NOTICE THIRD_PARTY.md /usr/share/licenses/messenger-logger/
COPY LICENSES/ /usr/share/licenses/messenger-logger/dependencies/
ENV BRIDGE_BIN=/usr/local/bin/messenger-bridge DATA_DIR=/data LOGGER_TIMEZONE=Europe/Warsaw
ENTRYPOINT ["python", "-u", "/app/logger.py"]

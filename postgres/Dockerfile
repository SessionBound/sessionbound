FROM postgres:16 AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/sessionbound_guard
COPY sessionbound_guard/ ./
RUN make clean && make && make install

FROM postgres:16

COPY --from=build /usr/lib/postgresql/16/lib/sessionbound_guard.so /usr/lib/postgresql/16/lib/
COPY --from=build /usr/share/postgresql/16/extension/sessionbound_guard.control /usr/share/postgresql/16/extension/
COPY --from=build /usr/share/postgresql/16/extension/sessionbound_guard--0.1.sql /usr/share/postgresql/16/extension/

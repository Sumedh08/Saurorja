FROM golang:1.24.8-alpine AS build
ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
RUN apk add --no-cache git
RUN GOBIN=/out go install -v "github.com/minio/minio@${MINIO_VERSION}"

FROM alpine:3.22
RUN apk add --no-cache ca-certificates curl \
    && addgroup -S -g 10001 minio \
    && adduser -S -D -H -u 10001 -G minio minio \
    && mkdir -p /data \
    && chown minio:minio /data
COPY --from=build /out/minio /usr/local/bin/minio
USER 10001:10001
VOLUME ["/data"]
EXPOSE 9000 9001
HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=20s \
  CMD curl -fsS http://127.0.0.1:9000/minio/health/ready >/dev/null || exit 1
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]

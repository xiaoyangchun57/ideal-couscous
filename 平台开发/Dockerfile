FROM node:22-alpine AS frontend-builder

WORKDIR /build/react-vite
COPY react-vite/package.json react-vite/package-lock.json ./
RUN npm ci
COPY react-vite/ ./
RUN npm run build

FROM python:3.12-slim AS sqlite-group-mode-builder

RUN apt-get update \
    && apt-get install --no-install-recommends -y gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# SQLite's Unix VFS requests mode 0644 for new database files, so an umask cannot
# grant the shared group write permission. This narrowly intercepts creations in
# the evidence directory and turns that request into owner/group RW, other NONE.
RUN printf '%s\n' \
    '#define _GNU_SOURCE' \
    '#include <dlfcn.h>' \
    '#include <fcntl.h>' \
    '#include <stdarg.h>' \
    '#include <string.h>' \
    '#include <sys/stat.h>' \
    '' \
    'typedef int (*open_fn)(const char *, int, ...);' \
    '' \
    'static int is_station_data_path(const char *path) {' \
    '    return path && strncmp(path, "/app/backend/data/", 18) == 0;' \
    '}' \
    '' \
    'static mode_t shared_database_mode(mode_t mode) {' \
    '    return (mode & ~0007) | 0060;' \
    '}' \
    '' \
    'static int call_open(const char *symbol, const char *path, int flags, va_list args) {' \
    '    static open_fn real_open;' \
    '    static open_fn real_open64;' \
    '    open_fn target = strcmp(symbol, "open64") == 0 ? real_open64 : real_open;' \
    '    if (!target) {' \
    '        target = (open_fn)dlsym(RTLD_NEXT, symbol);' \
    '        if (strcmp(symbol, "open64") == 0) real_open64 = target; else real_open = target;' \
    '    }' \
    '    if (flags & O_CREAT) {' \
    '        mode_t mode = va_arg(args, mode_t);' \
    '        if (is_station_data_path(path)) mode = shared_database_mode(mode);' \
    '        return target(path, flags, mode);' \
    '    }' \
    '    return target(path, flags);' \
    '}' \
    '' \
    'int open(const char *path, int flags, ...) {' \
    '    va_list args; va_start(args, flags); int result = call_open("open", path, flags, args); va_end(args); return result;' \
    '}' \
    '' \
    'int open64(const char *path, int flags, ...) {' \
    '    va_list args; va_start(args, flags); int result = call_open("open64", path, flags, args); va_end(args); return result;' \
    '}' \
    > /tmp/sqlite-group-mode.c \
    && gcc -shared -fPIC -O2 -Wall -Werror -o /opt/libsqlite-group-mode.so /tmp/sqlite-group-mode.c -ldl \
    && chmod 0555 /opt/libsqlite-group-mode.so \
    && rm /tmp/sqlite-group-mode.c

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --default-timeout=180 --retries=5 \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt

COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY --from=frontend-builder /build/frontend/v2/ /app/frontend/v2/
COPY --from=sqlite-group-mode-builder /opt/libsqlite-group-mode.so /opt/libsqlite-group-mode.so

# The Web container keeps its established root identity. Both services share this
# dedicated group; Compose prepares the bind mount with setgid inheritance so a
# Web-created SQLite database and its WAL/SHM sidecars remain writable by ingest.
RUN groupadd --gid 10001 stationingest \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin stationingest \
    && mkdir -p /app/backend/data /app/frontend/uploads \
    && chown root:stationingest /app/backend/data \
    && chmod 2770 /app/backend/data \
    && command -v setpriv >/dev/null

EXPOSE 5000

CMD ["python", "app.py"]

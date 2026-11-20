#!/usr/bin/env bash
set -euo pipefail

# Initializes and starts a user-space PostgreSQL instance for pg-nl2sql
# with no root privileges required, seeds the demo schema, and configures the
# dedicated read-only role (nl2sql_ro).
#
# Works with any installed PostgreSQL major version (10+). Binaries are located
# automatically; override with:
#   PG_BIN=/path/to/postgres/bin        use this bin directory exactly
#   PG_NL2SQL_PG_VERSION=16             prefer a specific major version
#   PG_NL2SQL_PORT=55432                port for the local server

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PGDATA="${PROJECT_DIR}/.pgdata"
PGSOCK="/tmp/pg_nl2sql_sock"
PGPORT="${PG_NL2SQL_PORT:-55432}"
DB_NAME="nl2sql_demo"

# Print the major version of the initdb in bin dir $1 (e.g. "16", "9.6").
pg_major_version() {
    local full
    full="$("$1/initdb" --version 2>/dev/null | awk '{print $NF}')" || return 1
    # Strip pre-release suffixes such as "17beta1" or "18rc1".
    full="$(printf '%s' "${full}" | sed -E 's/^([0-9]+(\.[0-9]+)?).*/\1/')"
    case "${full}" in
        [0-9].*) printf '%s\n' "${full}" ;;          # 9.x: major is X.Y
        *)       printf '%s\n' "${full%%.*}" ;;      # 10+: major is X
    esac
}

# Emit candidate bin directories, one per line, in priority order.
candidate_bin_dirs() {
    local dir
    # 1. pg_config / initdb already on PATH.
    if command -v pg_config >/dev/null 2>&1; then
        pg_config --bindir 2>/dev/null || true
    fi
    if command -v initdb >/dev/null 2>&1; then
        dirname "$(command -v initdb)"
    fi
    # 2. Well-known install locations across platforms, newest version first.
    for dir in \
        /usr/lib/postgresql/*/bin \
        /usr/pgsql-*/bin \
        /opt/homebrew/opt/postgresql@*/bin \
        /opt/homebrew/opt/postgresql/bin \
        /usr/local/opt/postgresql@*/bin \
        /usr/local/opt/postgresql/bin \
        /Applications/Postgres.app/Contents/Versions/*/bin \
        /usr/local/pgsql/bin \
        /opt/local/lib/postgresql*/bin; do
        if [[ -x "${dir}/initdb" ]]; then printf '%s\n' "${dir}"; fi
    done | while read -r dir; do
        printf '%s\t%s\n' "$(pg_major_version "${dir}")" "${dir}"
    done | sort -t $'\t' -k1,1 -rn | cut -f2
}

# Locate a bin dir containing initdb, pg_ctl, and psql. If $1 is non-empty,
# only accept binaries of that major version.
find_pg_bin() {
    local want="$1" dir
    while read -r dir; do
        [[ -n "${dir}" ]] || continue
        [[ -x "${dir}/initdb" && -x "${dir}/pg_ctl" && -x "${dir}/psql" ]] || continue
        if [[ -z "${want}" || "$(pg_major_version "${dir}")" == "${want}" ]]; then
            printf '%s\n' "${dir}"
            return 0
        fi
    done < <(candidate_bin_dirs)
    return 1
}

# An existing cluster can only be run by binaries of the same major version.
CLUSTER_VERSION=""
if [[ -f "${PGDATA}/PG_VERSION" ]]; then
    CLUSTER_VERSION="$(<"${PGDATA}/PG_VERSION")"
fi
WANT_VERSION="${PG_NL2SQL_PG_VERSION:-${CLUSTER_VERSION}}"

if [[ -n "${PG_BIN:-}" ]]; then
    if [[ ! -x "${PG_BIN}/initdb" ]]; then
        echo "Error: PG_BIN is set but ${PG_BIN}/initdb is not executable." >&2
        exit 1
    fi
elif ! PG_BIN="$(find_pg_bin "${WANT_VERSION}")"; then
    if [[ -n "${WANT_VERSION}" ]]; then
        echo "Error: PostgreSQL ${WANT_VERSION} binaries (initdb, pg_ctl, psql) not found." >&2
    else
        echo "Error: PostgreSQL binaries (initdb, pg_ctl, psql) not found." >&2
    fi
    echo "Install PostgreSQL, put its bin directory on PATH, or set PG_BIN=/path/to/bin." >&2
    exit 1
fi

PG_VERSION="$(pg_major_version "${PG_BIN}")"
if [[ -n "${CLUSTER_VERSION}" && "${CLUSTER_VERSION}" != "${PG_VERSION}" ]]; then
    echo "Error: ${PGDATA} was initialized by PostgreSQL ${CLUSTER_VERSION}," >&2
    echo "but the selected binaries (${PG_BIN}) are version ${PG_VERSION}." >&2
    echo "Point PG_BIN at PostgreSQL ${CLUSTER_VERSION}, or remove ${PGDATA} to re-initialize." >&2
    exit 1
fi
echo "==> Using PostgreSQL ${PG_VERSION} from ${PG_BIN}"

mkdir -p "${PGSOCK}"

if [[ -z "${CLUSTER_VERSION}" ]]; then
    # C.UTF-8 is not available everywhere (notably macOS); fall back to C,
    # which has the same byte-order collation.
    LOCALE="C"
    if locale -a 2>/dev/null | grep -qiE '^c\.utf-?8$'; then
        LOCALE="C.UTF-8"
    fi
    echo "==> Initializing PostgreSQL ${PG_VERSION} cluster in ${PGDATA} (locale ${LOCALE})..."
    "${PG_BIN}/initdb" -D "${PGDATA}" --username=postgres --auth=trust \
        --encoding=UTF8 --locale="${LOCALE}" >/dev/null
    {
        echo "listen_addresses = '127.0.0.1'"
        echo "port = ${PGPORT}"
        echo "unix_socket_directories = '${PGSOCK}'"
        echo "timezone = 'UTC'"
        echo "log_min_messages = warning"
    } >> "${PGDATA}/postgresql.conf"
fi

if ! "${PG_BIN}/pg_ctl" -D "${PGDATA}" status >/dev/null 2>&1; then
    echo "==> Starting PostgreSQL on 127.0.0.1:${PGPORT}..."
    # -o -p keeps the server on PGPORT even if it changed since initdb.
    "${PG_BIN}/pg_ctl" -D "${PGDATA}" -l "${PGDATA}/postgres.log" -w \
        -o "-p ${PGPORT}" start >/dev/null
else
    echo "==> PostgreSQL already running."
fi

PSQL=("${PG_BIN}/psql" -h 127.0.0.1 -p "${PGPORT}" -U postgres -v ON_ERROR_STOP=1)

echo "==> Ensuring database '${DB_NAME}' exists..."
if ! "${PSQL[@]}" -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    "${PSQL[@]}" -d postgres -c "CREATE DATABASE ${DB_NAME}" >/dev/null
fi

echo "==> Seeding demo schema and sample data..."
"${PSQL[@]}" -d "${DB_NAME}" -f "${SCRIPT_DIR}/seed_demo_db.sql" >/dev/null

echo "==> Configuring dedicated read-only role 'nl2sql_ro'..."
"${PSQL[@]}" -d "${DB_NAME}" -f "${SCRIPT_DIR}/init_roles.sql" >/dev/null

echo "==> Ready! Read-only connection string:"
echo "    postgresql://nl2sql_ro:nl2sql_ro_demo_pass@127.0.0.1:${PGPORT}/${DB_NAME}"

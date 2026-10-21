#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "OwnPI installation takes no command-line options." >&2
  echo "Usage: curl -fsSL <OwnPI distribution>/install.sh | bash" >&2
  exit 2
fi

APP_HOME="${OWNPI_HOME:-${HOME}/.ownpi}"
BIN_DIR="${OWNPI_BIN_DIR:-${HOME}/.local/bin}"
CHANNEL="${OWNPI_RELEASE_CHANNEL:-stable}"
DEFAULT_BASE_URL="https://raw.githubusercontent.com/yzz673/OwnPI/main/channels"
BASE_URL="${OWNPI_DISTRIBUTION_BASE_URL:-${DEFAULT_BASE_URL}}"
BASE_URL="${BASE_URL%/}"
UPDATE_URL="${BASE_URL}/${CHANNEL}"

case "$(uname -s)" in
  Darwin) SYSTEM="darwin" ;;
  Linux) SYSTEM="linux" ;;
  *) echo "OwnPI supports macOS and Linux." >&2; exit 1 ;;
esac
case "$(uname -m)" in
  arm64|aarch64) MACHINE="arm64" ;;
  x86_64|amd64) MACHINE="x86_64" ;;
  *) echo "Unsupported CPU architecture: $(uname -m)" >&2; exit 1 ;;
esac
TARGET="${SYSTEM}-${MACHINE}"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ownpi-install.XXXXXX")"
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

download() {
  source_url="$1"
  destination="$2"
  if command -v curl >/dev/null 2>&1; then
    if [ -n "${OWNPI_RELEASE_AUTH_TOKEN:-}" ]; then
      if ! curl -fsSL -H "Authorization: Bearer ${OWNPI_RELEASE_AUTH_TOKEN}" "${source_url}" -o "${destination}"; then
        echo "OwnPI base download failed for ${source_url}; check network access, release channel, and credentials." >&2
        exit 1
      fi
    else
      if ! curl -fsSL "${source_url}" -o "${destination}"; then
        echo "OwnPI base download failed for ${source_url}; check network access and release channel." >&2
        exit 1
      fi
    fi
  elif command -v wget >/dev/null 2>&1; then
    if [ -n "${OWNPI_RELEASE_AUTH_TOKEN:-}" ]; then
      if ! wget --header="Authorization: Bearer ${OWNPI_RELEASE_AUTH_TOKEN}" -qO "${destination}" "${source_url}"; then
        echo "OwnPI base download failed for ${source_url}; check network access, release channel, and credentials." >&2
        exit 1
      fi
    else
      if ! wget -qO "${destination}" "${source_url}"; then
        echo "OwnPI base download failed for ${source_url}; check network access and release channel." >&2
        exit 1
      fi
    fi
  else
    echo "OwnPI installation requires curl or wget." >&2
    exit 1
  fi
}

ARTIFACT="${OWNPI_TEST_BASE_ARTIFACT:-}"
CHECKSUM_FILE="${OWNPI_TEST_BASE_CHECKSUM:-}"
if [ -z "${ARTIFACT}" ]; then
  ARTIFACT="${TMP_DIR}/ownpi-base-${TARGET}.tar.gz"
  if [ "${BASE_URL}" = "${DEFAULT_BASE_URL}" ]; then
    download "${UPDATE_URL}/version.txt" "${TMP_DIR}/version.txt"
    VERSION="$(tr -d '\r\n' < "${TMP_DIR}/version.txt")"
    if ! [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "Invalid OwnPI release version in channel metadata." >&2
      exit 1
    fi
    ARTIFACT_URL="https://github.com/yzz673/OwnPI/releases/download/v${VERSION}/ownpi-base-${VERSION}-${TARGET}.tar.gz"
  else
    # Custom endpoints retain the existing channel/archive layout for testing.
    ARTIFACT_URL="${UPDATE_URL}/ownpi-base-${TARGET}.tar.gz"
  fi
  download "${ARTIFACT_URL}" "${ARTIFACT}"
else
  ARTIFACT_URL=""
fi
if [ -z "${CHECKSUM_FILE}" ]; then
  CHECKSUM_FILE="${TMP_DIR}/artifact.sha256"
  if [ -n "${ARTIFACT_URL}" ]; then
    download "${ARTIFACT_URL}.sha256" "${CHECKSUM_FILE}"
  else
    echo "Local packaging tests require OWNPI_TEST_BASE_CHECKSUM." >&2
    exit 1
  fi
fi

EXPECTED="$(awk 'NF >= 1 {print $1; exit}' "${CHECKSUM_FILE}")"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "${ARTIFACT}" | awk '{print $1}')"
else
  ACTUAL="$(shasum -a 256 "${ARTIFACT}" | awk '{print $1}')"
fi
if [ -z "${EXPECTED}" ] || [ "${EXPECTED}" != "${ACTUAL}" ]; then
  echo "OwnPI base artifact checksum mismatch." >&2
  exit 1
fi

MEMBERS_FILE="${TMP_DIR}/archive-members.txt"
if ! tar -tzf "${ARTIFACT}" > "${MEMBERS_FILE}"; then
  echo "OwnPI base extraction failed: artifact is not a readable tar.gz archive." >&2
  exit 1
fi
while IFS= read -r member; do
  case "${member}" in
    /*|../*|*/../*|*/..) echo "Unsafe archive member: ${member}" >&2; exit 1 ;;
  esac
done < "${MEMBERS_FILE}"
if ! tar -xzf "${ARTIFACT}" -C "${TMP_DIR}"; then
  echo "OwnPI base extraction failed while unpacking the verified artifact." >&2
  exit 1
fi
PACKAGE_ROOT="$(find "${TMP_DIR}" -mindepth 1 -maxdepth 1 -type d -name 'ownpi-base-*' | head -n 1)"
if [ -z "${PACKAGE_ROOT}" ]; then
  echo "OwnPI base artifact root is missing." >&2
  exit 1
fi
RUNTIME_PYTHON="${PACKAGE_ROOT}/runtime/python/bin/python3"
if [ ! -x "${RUNTIME_PYTHON}" ]; then
  echo "OwnPI managed Python runtime is missing." >&2
  exit 1
fi

PYTHONPATH="${PACKAGE_ROOT}/resources" "${RUNTIME_PYTHON}" "${PACKAGE_ROOT}/resources/install_base.py" \
  --artifact-root "${PACKAGE_ROOT}" \
  --artifact-digest "${ACTUAL}" \
  --app-home "${APP_HOME}" \
  --bin-dir "${BIN_DIR}" \
  --update-base-url "${UPDATE_URL}"

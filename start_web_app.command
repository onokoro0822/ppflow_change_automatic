#!/bin/zsh

set -u
cd "${0:A:h}"

GEMINI_KEYCHAIN_SERVICE="ppflow-gemini-api-key"
TAVILY_KEYCHAIN_SERVICE="ppflow-tavily-api-key"
KEYCHAIN_ACCOUNT="${USER:-$(id -un)}"

if [[ "${1:-}" == "--forget-keys" ]]; then
  security delete-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$GEMINI_KEYCHAIN_SERVICE" >/dev/null 2>&1 || true
  security delete-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$TAVILY_KEYCHAIN_SERVICE" >/dev/null 2>&1 || true
  echo "保存したGemini・Tavily APIキーをキーチェーンから削除しました。"
  exit 0
fi

load_api_key() {
  local variable_name="$1"
  local service_name="$2"
  local prompt_label="$3"
  local current_value="${(P)variable_name:-}"
  local stored_value=""

  if [[ -n "$current_value" ]]; then
    return
  fi

  stored_value="$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$service_name" -w 2>/dev/null || true)"
  if [[ -z "$stored_value" ]]; then
    read -s "stored_value?${prompt_label}（初回のみ）: "
    echo
    if [[ -z "$stored_value" ]]; then
      echo "APIキーが空のため起動を中止します。"
      exit 1
    fi
    security add-generic-password \
      -U \
      -a "$KEYCHAIN_ACCOUNT" \
      -s "$service_name" \
      -w "$stored_value" >/dev/null
    echo "${prompt_label}をmacOSキーチェーンへ保存しました。"
  fi

  export "${variable_name}=${stored_value}"
}

load_api_key "GEMINI_API_KEY" "$GEMINI_KEYCHAIN_SERVICE" "Gemini API key"
load_api_key "TAVILY_API_KEY" "$TAVILY_KEYCHAIN_SERVICE" "Tavily API key"

exec python3 -B web_app.py

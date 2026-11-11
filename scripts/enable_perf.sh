#!/usr/bin/env bash
# Разблокирует пользовательские perf-счётчики, нужные LatencyLab.
#
# LatencyLab измеряет аппаратные метрики (cycles/instructions/cache_misses)
# через perf_event_open. Ядро может запрещать это непривилегированным
# пользователям: значение /proc/sys/kernel/perf_event_paranoid >= 3 блокирует
# вообще все события. Скрипт проверяет текущее значение и, если необходимо,
# опускает его до безопасного уровня (по умолчанию 1) через sudo.
#
# Использование:
#   scripts/enable_perf.sh            # спросит подтверждение
#   scripts/enable_perf.sh --yes      # без подтверждения
#   PERF_PARANOID_TARGET=2 scripts/enable_perf.sh   # иной целевой порог
#
# Внимание: меняется глобальная security-настройка ядра для всей машины;
# значение сбрасывается после перезагрузки. Для постоянства положите
# 'kernel.perf_event_paranoid=1' в /etc/sysctl.d/*.conf.
set -euo pipefail

TARGET="${PERF_PARANOID_TARGET:-1}"

case "$TARGET" in
    -1|0|1|2) ;;
    *) echo "PERF_PARANOID_TARGET должен быть -1, 0, 1 или 2 (получено '$TARGET')" >&2; exit 2 ;;
esac

SYSCTL_FILE=/proc/sys/kernel/perf_event_paranoid
if [[ ! -r "$SYSCTL_FILE" ]]; then
    echo "Не удалось прочитать $SYSCTL_FILE — вы точно на Linux?" >&2
    exit 1
fi

CURRENT="$(cat "$SYSCTL_FILE")"
if ! [[ "$CURRENT" =~ ^-?[0-9]+$ ]]; then
    echo "Неожиданное значение perf_event_paranoid: '$CURRENT'" >&2
    exit 1
fi

if (( CURRENT <= TARGET )); then
    echo "perf_event_paranoid уже $CURRENT (<= $TARGET) — ничего делать не нужно."
    exit 0
fi

echo "perf_event_paranoid = $CURRENT — аппаратные счётчики LatencyLab заблокированы."
echo "Будет выполнено: sysctl kernel.perf_event_paranoid=$TARGET (нужны права root)."
echo "Это меняет настройку ядра для всей машины и НЕ переживает перезагрузку."

if (( EUID == 0 )); then
    sysctl "kernel.perf_event_paranoid=$TARGET"
    exit 0
fi

if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo не найден. Выполните вручную от root: sysctl kernel.perf_event_paranoid=$TARGET" >&2
    exit 1
fi

if [[ "${1:-}" == "--yes" ]]; then
    sudo sysctl "kernel.perf_event_paranoid=$TARGET"
    exit $?
fi

if [[ ! -t 0 ]]; then
    echo "Неинтерактивный запуск без --yes: sudo не вызывается." >&2
    echo "Вручную: sudo sysctl kernel.perf_event_paranoid=$TARGET" >&2
    exit 1
fi

read -r -p "Продолжить? [y/N] " answer || true
case "$answer" in
    [yY]|[yY][eE][sS]) sudo sysctl "kernel.perf_event_paranoid=$TARGET" ;;
    *) echo "Пропущено. Вручную: sudo sysctl kernel.perf_event_paranoid=$TARGET"; exit 1 ;;
esac

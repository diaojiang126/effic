#!/bin/bash
# ============================================================
# effic daily ops on Linux: start / stop / restart / status / logs
# ============================================================
ACTION="${1:-status}"

case "${ACTION}" in
    restart)
        systemctl restart effic
        sleep 2
        systemctl status effic --no-pager -l | head -20
        ;;
    stop)    systemctl stop effic ;;
    start)   systemctl start effic ;;
    status)  systemctl status effic --no-pager -l | head -30 ;;
    log)     journalctl -u effic -f -n 100 ;;
    applog)  tail -f /opt/effic/logs/gunicorn_error.log ;;
    health)
        echo "== /healthz =="
        curl -s http://127.0.0.1/healthz | python3 -m json.tool || curl -s http://127.0.0.1/healthz
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|log|applog|health}"
        exit 1
        ;;
esac

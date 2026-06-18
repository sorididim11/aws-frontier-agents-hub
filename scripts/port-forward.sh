#!/bin/bash
# Auto-reconnecting port-forward
while true; do
    echo "$(date): Starting port-forward..."
    kubectl port-forward svc/dashboard 8081:80 -n dashboard 2>&1
    echo "$(date): Connection lost. Reconnecting in 2s..."
    sleep 2
done

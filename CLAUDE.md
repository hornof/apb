# apb

coordination: #25

The line above names the coordination issue, hornof/apb#25. The plugin's hook (`hooks/apb-watch-start.py`) reads it and starts the watcher (`scripts/apb-watch.py`) on that issue, which wakes the orchestrator when the issue gets a new comment or is closed or reopened.

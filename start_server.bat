@echo off
set HTTP_PROXY=http://127.0.0.1:10809
set HTTPS_PROXY=http://127.0.0.1:10809
cd /d C:\tmp\app
python -m uvicorn main:app --host 0.0.0.0 --port 8001

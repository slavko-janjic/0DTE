@echo off
cd /d "C:\Users\slavk\OneDrive\Documents\Projects\0DTE"
rem Rotate the log if it has grown past ~5 MB (single generation is plenty).
if exist worker.log for %%A in (worker.log) do if %%~zA gtr 5242880 move /y worker.log worker.log.old
"C:\Users\slavk\.pyenv\pyenv-win\versions\3.13.2\python.exe" -u worker.py >> worker.log 2>&1

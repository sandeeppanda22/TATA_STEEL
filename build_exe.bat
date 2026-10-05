@echo off
rem Run this once on a Windows PC that has Python 3.9+ installed (python.org, tick "Add to PATH").
rem Output: dist\RoadWorks.exe  (double-click to run; it creates roadworks.db next to itself)
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name RoadWorks --add-data "plan.csv;." --add-data "bottlenecks.csv;." roadworks.py
echo.
echo Done. Your app is dist\RoadWorks.exe
pause

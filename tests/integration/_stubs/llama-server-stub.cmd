@echo off
rem Windows-invocable sibling of ``llama-server-stub`` (#523, A(i)).
rem
rem The shebang script is not directly executable on Windows (no PE
rem header -> WinError 193 "not a valid Win32 application"). Windows'
rem CreateProcess *can* launch a .cmd/.bat file directly (unlike a POSIX
rem shell script), so this shim -- selected only on win32 by
rem tests/integration/conftest.py -- re-invokes the real stub under the
rem current interpreter, forwarding every argument unchanged. Product
rem behavior (the stub's own logic) is untouched; this is purely an
rem invocation-envelope fix.
python "%~dp0llama-server-stub" %*
